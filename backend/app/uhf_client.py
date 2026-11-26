from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .db import Database

logger = logging.getLogger(__name__)

UHF_MDNS_SERVICE_TYPE = "_uhf-server._tcp.local."


def _browse_mdns_sync(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Browse LAN for _uhf-server._tcp.local. via Zeroconf/mDNS."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        logger.debug("zeroconf not installed; skipping mDNS discovery")
        return []

    found: list[dict[str, Any]] = []

    class _Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
            info = zc.get_service_info(svc_type, name, timeout=1500)
            if not info or not info.port:
                return
            addrs = info.parsed_addresses()
            # Prefer real LAN IPv4 (192.168.*, 10.*) over Docker bridge 172.16-31.* and loopback
            ipv4s = [a for a in addrs if ":" not in a and not a.startswith("127.")]

            def _addr_rank(ip: str) -> int:
                if ip.startswith("192.168.") or ip.startswith("10."):
                    return 0
                if ip.startswith("172."):
                    return 2  # likely Docker bridge
                return 1

            ipv4s.sort(key=_addr_rank)
            chosen = ipv4s[0] if ipv4s else (addrs[0] if addrs else None)
            if not chosen:
                return
            host_display = (info.server or "").rstrip(".") or name.split(".")[0]
            found.append(
                {
                    "url": f"http://{chosen}:{info.port}",
                    "name": host_display,
                    "host": chosen,
                    "port": info.port,
                    "source": "mdns",
                }
            )

        def update_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
            pass

        def remove_service(self, zc: Zeroconf, svc_type: str, name: str) -> None:
            pass

    zc: Zeroconf | None = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, UHF_MDNS_SERVICE_TYPE, _Listener())
        time.sleep(max(0.2, min(timeout, 5.0)))
    except Exception as exc:
        logger.debug("mDNS discovery failed: %s", exc)
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    return found


async def _probe_uhf_url(url: str, source: str, label: str, timeout: float = 2.0) -> dict[str, Any] | None:
    clean = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{clean}/auth/methods")
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            if "uhf_account" not in data and "server_password" not in data and "password_required" not in data:
                return None
            return {
                "url": clean,
                "name": label,
                "source": source,
                "password_required": bool(data.get("password_required", False)),
            }
    except Exception:
        return None


async def discover_uhf_servers(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Discover uhf-server instances via mDNS (_uhf-server._tcp.local.), localhost, and Docker host probes."""
    local_candidates = [
        ("http://127.0.0.1:8085", "Local (port 8085)"),
        ("http://127.0.0.1:8000", "Local (port 8000)"),
        ("http://host.docker.internal:8085", "Host machine (port 8085)"),
        ("http://host.docker.internal:8000", "Host machine (port 8000)"),
    ]

    mdns_task = asyncio.to_thread(_browse_mdns_sync, timeout)
    probe_tasks = [_probe_uhf_url(u, "local_probe", lbl, timeout=1.5) for u, lbl in local_candidates]

    mdns_raw, *local_results = await asyncio.gather(mdns_task, *probe_tasks)

    verified: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Verify mDNS results via /auth/methods so we report password_required accurately
    if mdns_raw:
        mdns_verify_tasks = [
            _probe_uhf_url(item["url"], "mdns", item.get("name") or item["url"], timeout=2.0)
            for item in mdns_raw
        ]
        mdns_verified = await asyncio.gather(*mdns_verify_tasks)
        for item in mdns_verified:
            if item and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                verified.append(item)

    for item in local_results:
        if item and item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            verified.append(item)

    return verified


class UHFServerError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UHFClient:
    """Async client for uhf-server REST API with automatic 30-day session handling."""

    def __init__(self, db: Database, timeout: float = 15.0) -> None:
        self.db = db
        self.timeout = timeout

    def _get_cfg(self) -> dict[str, Any]:
        return self.db.get_setting("uhf_server")

    def _base_url(self, override_url: str | None = None) -> str:
        raw = (override_url if override_url is not None else self._get_cfg().get("url") or "").strip()
        if not raw:
            raise UHFServerError("UHF Server URL is not configured.", status_code=400)
        return raw.rstrip("/")

    async def get_auth_methods(self, base_url: str | None = None) -> dict[str, Any]:
        url = f"{self._base_url(base_url)}/auth/methods"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise UHFServerError(f"Could not reach UHF Server at {url}: {exc}", status_code=502) from exc

    async def login(
        self,
        base_url: str | None = None,
        password: str | None = None,
        persist: bool = True,
    ) -> str:
        cfg = self._get_cfg()
        target_base = self._base_url(base_url)
        effective_password = cfg.get("password", "") if password is None else password
        device_id = cfg.get("device_id") or "uhf-scheduler-web"

        methods = await self.get_auth_methods(target_base)
        password_required = bool(methods.get("password_required", False))
        if password_required and not effective_password:
            raise UHFServerError(
                "UHF Server requires a server password. Enter it in Settings.",
                status_code=401,
            )

        payload: dict[str, Any] = {"device_id": device_id}
        if effective_password:
            payload["server_password"] = effective_password
        else:
            payload["server_password"] = ""

        url = f"{target_base}/auth/password-login"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in (401, 403):
                    raise UHFServerError("Invalid UHF Server password.", status_code=401)
                if resp.status_code == 429:
                    raise UHFServerError(
                        "UHF Server rate-limited login attempts. Wait a minute and retry.",
                        status_code=429,
                    )
                resp.raise_for_status()
                data = resp.json()
        except UHFServerError:
            raise
        except httpx.HTTPError as exc:
            raise UHFServerError(f"UHF Server login failed: {exc}", status_code=502) from exc

        token = data.get("id_token") or data.get("access_token") or data.get("token")
        if not token:
            raise UHFServerError("UHF Server login response did not include a token.", status_code=502)

        if persist:
            cfg["token"] = token
            if base_url is not None:
                cfg["url"] = target_base
            if password is not None:
                cfg["password"] = effective_password
            self.db.set_setting("uhf_server", cfg)

        return token

    async def test_connection(
        self,
        base_url: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        target_base = self._base_url(base_url)
        methods = await self.get_auth_methods(target_base)
        token = await self.login(base_url=target_base, password=password, persist=False)
        # Verify token works by listing recordings
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{target_base}/dvr/recordings",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                recordings = resp.json()
        except httpx.HTTPError as exc:
            raise UHFServerError(f"Authenticated but failed reading recordings: {exc}", status_code=502) from exc

        return {
            "ok": True,
            "url": target_base,
            "password_required": bool(methods.get("password_required", False)),
            "recording_count": len(recordings) if isinstance(recordings, list) else 0,
        }

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        cfg = self._get_cfg()
        target_base = self._base_url()
        token = cfg.get("token")
        if not token:
            token = await self.login(persist=True)

        url = f"{target_base}{path}"
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.request(
                        method,
                        url,
                        json=json_body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.HTTPError as exc:
                raise UHFServerError(
                    f"Could not reach UHF Server ({method} {path}): {exc}",
                    status_code=502,
                ) from exc

            if resp.status_code == 401 and attempt == 0:
                logger.info("UHF Server returned 401; renewing session token.")
                token = await self.login(persist=True)
                continue

            if resp.status_code >= 400:
                detail = resp.text
                try:
                    err_json = resp.json()
                    if isinstance(err_json, dict) and "detail" in err_json:
                        detail = str(err_json["detail"])
                except Exception:
                    pass
                raise UHFServerError(
                    f"UHF Server error ({resp.status_code}): {detail}",
                    status_code=resp.status_code if resp.status_code < 500 else 502,
                )

            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

        raise UHFServerError("UHF Server authentication failed after retry.", status_code=401)

    async def list_recordings(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/dvr/recordings")
        return data if isinstance(data, list) else []

    async def get_recording(self, recording_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/dvr/recordings/{recording_id}")

    async def create_recording(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/dvr/recordings", json_body=payload)

    async def cancel_recording(self, recording_id: str) -> Any:
        return await self._request("PATCH", f"/dvr/recordings/{recording_id}/cancel")

    async def cancel_recurrence(self, recording_id: str) -> Any:
        return await self._request("PATCH", f"/dvr/recordings/{recording_id}/cancel-recurrence")

    async def delete_recording(self, recording_id: str) -> Any:
        return await self._request("DELETE", f"/dvr/recordings/{recording_id}")

    async def detect_playlist_ids(self, host_filter: str | None = None) -> dict[str, list[str]]:
        """Inspect existing uhf-server recordings and map stream hostname -> playlistID(s)."""
        recordings = await self.list_recordings()
        found: dict[str, set[str]] = {}
        filter_host = None
        if host_filter:
            parsed = urlparse(host_filter if "://" in host_filter else f"http://{host_filter}")
            filter_host = (parsed.hostname or host_filter).lower()

        for rec in recordings:
            url = rec.get("url") or ""
            meta = rec.get("metadata") or {}
            pid = meta.get("playlistID")
            if not url or not pid:
                continue
            host = (urlparse(url).hostname or "").lower()
            if not host:
                continue
            if filter_host and host != filter_host:
                continue
            found.setdefault(host, set()).add(str(pid))

        return {h: sorted(pids) for h, pids in found.items()}
