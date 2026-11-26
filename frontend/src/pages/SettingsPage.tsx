import React, { useCallback, useEffect, useState } from 'react';
import {
  api,
  AppSettings,
  Category,
  DiscoveredUHFServer,
  EPGSource,
  IPTVSource,
  UpdateCheckResult,
} from '../api/client';
import { APP_VERSION, BUILD_TIMESTAMP } from '../version';

export const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [uhfUrl, setUhfUrl] = useState('');
  const [uhfPassword, setUhfPassword] = useState('');
  const [discoveredServers, setDiscoveredServers] = useState<DiscoveredUHFServer[]>([]);
  const [updateInfo, setUpdateInfo] = useState<UpdateCheckResult | null>(null);
  const [prePad, setPrePad] = useState(2);
  const [postPad, setPostPad] = useState(5);
  const [maxStreams, setMaxStreams] = useState(1);
  const [syncHours, setSyncHours] = useState(12);
  const [epgRetHours, setEpgRetHours] = useState(24);

  const [iptvSources, setIptvSources] = useState<IPTVSource[]>([]);
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [catFilter, setCatFilter] = useState('');

  // New IPTV source form
  const [srcName, setSrcName] = useState('');
  const [srcType, setSrcType] = useState<'xtream' | 'm3u'>('xtream');
  const [srcUrl, setSrcUrl] = useState('');
  const [srcUser, setSrcUser] = useState('');
  const [srcPass, setSrcPass] = useState('');
  const [srcPlaylistId, setSrcPlaylistId] = useState('');
  const [srcAutoEpg, setSrcAutoEpg] = useState(true);

  // New EPG source form
  const [epgName, setEpgName] = useState('');
  const [epgUrl, setEpgUrl] = useState('');

  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [s, iptv, epg, cats] = await Promise.all([
        api.getSettings(),
        api.listIptvSources(),
        api.listEpgSources(),
        api.listCategories(true),
      ]);
      setSettings(s);
      setUhfUrl(s.uhf_server.url);
      setPrePad(s.defaults.pre_padding_min);
      setPostPad(s.defaults.post_padding_min);
      setMaxStreams(s.defaults.max_simultaneous_streams);
      setSyncHours(s.defaults.sync_interval_hours);
      setEpgRetHours(s.defaults.epg_retention_hours);
      setIptvSources(iptv);
      setEpgSources(epg);
      setCategories(cats);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed loading settings');
    }
  }, []);

  useEffect(() => {
    loadAll();
    api.checkForUpdates(false).then(setUpdateInfo).catch(() => {});
  }, [loadAll]);

  const checkUpdatesNow = async () => {
    setBusyId('check-update');
    try {
      const res = await api.checkForUpdates(true);
      setUpdateInfo(res);
      if (res.update_available && res.latest_version) {
        setStatusMsg(`Update available: v${res.latest_version} (currently v${res.current_version}).`);
      } else if (res.checked_online) {
        setStatusMsg(`You are up to date (v${res.current_version}).`);
      } else {
        setStatusMsg('Could not reach GitHub Releases right now.');
      }
    } finally {
      setBusyId(null);
    }
  };

  const saveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const updated = await api.updateSettings({
        uhf_server: {
          url: uhfUrl,
          password: uhfPassword || undefined,
        },
        defaults: {
          pre_padding_min: prePad,
          post_padding_min: postPad,
          max_simultaneous_streams: maxStreams,
          sync_interval_hours: syncHours,
          epg_retention_hours: epgRetHours,
        },
      });
      setSettings(updated);
      setUhfPassword('');
      setStatusMsg('Settings saved.');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to save settings');
    }
  };

  const testUhf = async () => {
    setStatusMsg(null);
    setErrorMsg(null);
    setBusyId('test-uhf');
    try {
      const res = await api.testUhf({
        url: uhfUrl || undefined,
        password: uhfPassword || undefined,
      });
      setStatusMsg(
        `Connected to ${res.url}! Password required: ${
          res.password_required ? 'yes' : 'no'
        } · Existing recordings: ${res.recording_count}.`
      );
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Connection failed');
    } finally {
      setBusyId(null);
    }
  };

  const discoverUhf = async () => {
    setStatusMsg(null);
    setErrorMsg(null);
    setBusyId('discover-uhf');
    try {
      const res = await api.discoverUhfServers();
      setDiscoveredServers(res.servers);
      if (res.servers.length === 0) {
        setErrorMsg('No UHF Server found on the local network or localhost ports 8000 / 8085.');
      } else {
        setUhfUrl(res.servers[0].url);
        setStatusMsg(
          `Found ${res.servers.length} UHF Server(s) — selected ${res.servers[0].url}. Click Save Settings to apply.`
        );
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Auto-detection failed');
    } finally {
      setBusyId(null);
    }
  };

  const detectPlaylist = async () => {
    setStatusMsg(null);
    setErrorMsg(null);
    setBusyId('detect-pl');
    try {
      const res = await api.detectPlaylistIds(srcUrl || undefined);
      const entries = Object.entries(res.detected);
      if (entries.length === 0) {
        setStatusMsg('No existing playlistID found in UHF Server recordings for that host.');
      } else {
        const firstPid = entries[0][1][0];
        setSrcPlaylistId(firstPid);
        setStatusMsg(`Detected playlistID ${firstPid} from ${entries[0][0]}.`);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Detection failed');
    } finally {
      setBusyId(null);
    }
  };

  const addIptvSource = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const created = await api.createIptvSource({
        name: srcName,
        type: srcType,
        url: srcUrl,
        username: srcType === 'xtream' ? srcUser : null,
        password: srcType === 'xtream' ? srcPass : null,
        playlist_id: srcPlaylistId || null,
        auto_epg_enabled: srcType === 'xtream' ? srcAutoEpg : false,
      });
      setSrcName('');
      setSrcUrl('');
      setSrcUser('');
      setSrcPass('');
      setSrcPlaylistId('');
      setStatusMsg(`Added IPTV source "${created.name}". Click Sync to load its channels.`);
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to add IPTV source');
    }
  };

  const syncIptv = async (id: number) => {
    setBusyId(`iptv-${id}`);
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const res = await api.syncIptvSource(id);
      if (res.ok) {
        setStatusMsg(`Synced ${res.channel_count} channels.`);
      } else {
        setErrorMsg(`Sync failed: ${res.error}`);
      }
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Sync failed');
    } finally {
      setBusyId(null);
    }
  };

  const deleteIptv = async (id: number, name: string) => {
    if (!window.confirm(`Remove IPTV source "${name}" and its cached channels?`)) return;
    await api.deleteIptvSource(id);
    await loadAll();
  };

  const addEpgSource = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const created = await api.createEpgSource({ name: epgName, url: epgUrl });
      setEpgName('');
      setEpgUrl('');
      setStatusMsg(`Added EPG source "${created.name}". Click Sync to ingest guide data.`);
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to add EPG source');
    }
  };

  const syncEpg = async (id: number) => {
    setBusyId(`epg-${id}`);
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const res = await api.syncEpgSource(id);
      if (res.ok) {
        setStatusMsg(`EPG synced: ${res.program_count} programs indexed.`);
      } else {
        setErrorMsg(`EPG sync failed: ${res.error}`);
      }
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'EPG sync failed');
    } finally {
      setBusyId(null);
    }
  };

  const toggleCategoryHidden = async (cat: Category) => {
    await api.setCategoryHidden(cat.id, !cat.hidden);
    setCategories((prev) =>
      prev.map((c) => (c.id === cat.id ? { ...c, hidden: !c.hidden } : c))
    );
  };

  const visibleCats = categories.filter((c) =>
    !catFilter.trim() ? true : c.name.toLowerCase().includes(catFilter.trim().toLowerCase())
  );

  return (
    <div>
      {statusMsg && <div className="alert alert-success">{statusMsg}</div>}
      {errorMsg && <div className="alert alert-error">{errorMsg}</div>}

      <div className="grid-2">
        {/* UHF Server & Defaults */}
        <form className="card" onSubmit={saveSettings}>
          <h2>UHF Server &amp; Defaults</h2>
          <div className="field">
            <label>UHF Server URL</label>
            <div className="row" style={{ gap: '0.4rem' }}>
              <input
                type="url"
                value={uhfUrl}
                onChange={(e) => setUhfUrl(e.target.value)}
                placeholder="http://127.0.0.1:8085"
                required
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={discoverUhf}
                disabled={busyId === 'discover-uhf'}
                title="Scan LAN via mDNS (_uhf-server._tcp) and localhost ports"
              >
                {busyId === 'discover-uhf' ? 'Scanning...' : 'Auto-Detect'}
              </button>
            </div>
            {discoveredServers.length > 0 && (
              <div className="row" style={{ marginTop: '0.4rem', gap: '0.35rem' }}>
                {discoveredServers.map((srv) => (
                  <button
                    key={srv.url}
                    type="button"
                    className={`btn btn-sm ${uhfUrl === srv.url ? 'btn-primary' : ''}`}
                    onClick={() => setUhfUrl(srv.url)}
                  >
                    {srv.name} ({srv.url}) · {srv.source === 'mdns' ? 'mDNS' : 'local'}
                    {srv.password_required ? ' 🔒' : ''}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="field">
            <label>
              Server Password{' '}
              <span className="muted">
                ({settings?.uhf_server.password_configured ? 'configured — leave blank to keep' : 'optional'})
              </span>
            </label>
            <input
              type="password"
              value={uhfPassword}
              onChange={(e) => setUhfPassword(e.target.value)}
              placeholder="Leave empty if server has no password"
            />
          </div>
          <div className="row">
            <div className="field">
              <label>Default Pre-padding (m)</label>
              <input type="number" min={0} max={120} value={prePad} onChange={(e) => setPrePad(Number(e.target.value))} />
            </div>
            <div className="field">
              <label>Default Post-padding (m)</label>
              <input type="number" min={0} max={180} value={postPad} onChange={(e) => setPostPad(Number(e.target.value))} />
            </div>
          </div>
          <div className="row">
            <div className="field">
              <label>Max Simultaneous Streams</label>
              <input type="number" min={1} max={50} value={maxStreams} onChange={(e) => setMaxStreams(Number(e.target.value))} />
            </div>
            <div className="field">
              <label>Auto-Sync Interval (h)</label>
              <input type="number" min={1} max={168} value={syncHours} onChange={(e) => setSyncHours(Number(e.target.value))} />
            </div>
            <div className="field">
              <label>EPG Retention (h)</label>
              <input type="number" min={1} max={336} value={epgRetHours} onChange={(e) => setEpgRetHours(Number(e.target.value))} />
            </div>
          </div>
          <div className="row">
            <button type="submit" className="btn btn-primary">
              Save Settings
            </button>
            <button type="button" className="btn" onClick={testUhf} disabled={busyId === 'test-uhf'}>
              {busyId === 'test-uhf' ? 'Testing...' : 'Test UHF Connection'}
            </button>
          </div>
        </form>

        {/* Add IPTV Source */}
        <form className="card" onSubmit={addIptvSource}>
          <h2>Add IPTV Source</h2>
          <div className="row">
            <div className="field">
              <label>Source Name</label>
              <input
                type="text"
                value={srcName}
                onChange={(e) => setSrcName(e.target.value)}
                placeholder="e.g. Main IPTV"
                required
              />
            </div>
            <div className="field" style={{ maxWidth: 160 }}>
              <label>Type</label>
              <select value={srcType} onChange={(e) => setSrcType(e.target.value as 'xtream' | 'm3u')}>
                <option value="xtream">Xtream Codes</option>
                <option value="m3u">M3U URL</option>
              </select>
            </div>
          </div>

          <div className="field">
            <label>{srcType === 'xtream' ? 'Server URL (e.g. http://provider.org)' : 'M3U Playlist URL'}</label>
            <input type="url" value={srcUrl} onChange={(e) => setSrcUrl(e.target.value)} required />
          </div>

          {srcType === 'xtream' && (
            <div className="row">
              <div className="field">
                <label>Username</label>
                <input type="text" value={srcUser} onChange={(e) => setSrcUser(e.target.value)} required />
              </div>
              <div className="field">
                <label>Password</label>
                <input type="password" value={srcPass} onChange={(e) => setSrcPass(e.target.value)} required />
              </div>
            </div>
          )}

          <div className="row">
            <div className="field">
              <label>Apple TV UHF playlistID (optional)</label>
              <input
                type="text"
                value={srcPlaylistId}
                onChange={(e) => setSrcPlaylistId(e.target.value)}
                placeholder="Auto-detect from existing recordings"
              />
            </div>
            <button
              type="button"
              className="btn btn-sm"
              style={{ marginTop: '0.6rem' }}
              onClick={detectPlaylist}
              disabled={busyId === 'detect-pl'}
            >
              Auto-Detect
            </button>
          </div>

          {srcType === 'xtream' && (
            <label className="row" style={{ marginBottom: '0.75rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={srcAutoEpg}
                onChange={(e) => setSrcAutoEpg(e.target.checked)}
              />
              <span className="muted">Auto-enable provider XMLTV EPG (xmltv.php)</span>
            </label>
          )}

          <button type="submit" className="btn btn-primary">
            Add IPTV Source
          </button>
        </form>
      </div>

      {/* Configured IPTV Sources */}
      <div className="card">
        <h3>Configured IPTV Sources ({iptvSources.length})</h3>
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>URL</th>
              <th>Channels</th>
              <th>Last Sync</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {iptvSources.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted">
                  No IPTV sources configured yet.
                </td>
              </tr>
            ) : (
              iptvSources.map((s) => (
                <tr key={s.id}>
                  <td>
                    <strong>{s.name}</strong>
                    {s.playlist_id && (
                      <div className="muted" style={{ fontSize: '0.74rem' }}>
                        playlistID: {s.playlist_id.slice(0, 12)}…
                      </div>
                    )}
                  </td>
                  <td>{s.type}</td>
                  <td className="muted">{s.url}</td>
                  <td>{s.channel_count.toLocaleString()}</td>
                  <td>
                    <div className="muted">
                      {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : 'Never'}
                    </div>
                    {s.last_error && (
                      <div style={{ color: '#fca5a5', fontSize: '0.76rem' }}>{s.last_error}</div>
                    )}
                  </td>
                  <td>
                    <div className="row" style={{ gap: '0.35rem' }}>
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => syncIptv(s.id)}
                        disabled={busyId === `iptv-${s.id}`}
                      >
                        {busyId === `iptv-${s.id}` ? 'Syncing...' : 'Sync Now'}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => deleteIptv(s.id, s.name)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* EPG Sources */}
      <div className="card">
        <h3>EPG / XMLTV Sources ({epgSources.length})</h3>
        <form className="row" style={{ marginBottom: '0.8rem' }} onSubmit={addEpgSource}>
          <input
            type="text"
            placeholder="Guide Name (e.g. 7-Day FR XMLTV)"
            value={epgName}
            onChange={(e) => setEpgName(e.target.value)}
            required
            style={{ flex: 1 }}
          />
          <input
            type="url"
            placeholder="https://example.com/guide.xml.gz"
            value={epgUrl}
            onChange={(e) => setEpgUrl(e.target.value)}
            required
            style={{ flex: 2 }}
          />
          <button type="submit" className="btn btn-primary">
            Add XMLTV URL
          </button>
        </form>

        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Programs</th>
              <th>Last Sync</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {epgSources.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No EPG sources configured (optional).
                </td>
              </tr>
            ) : (
              epgSources.map((e) => (
                <tr key={e.id}>
                  <td>
                    <strong>{e.name}</strong>
                    {e.url && <div className="muted" style={{ fontSize: '0.74rem' }}>{e.url}</div>}
                  </td>
                  <td>{e.type}</td>
                  <td>{e.program_count.toLocaleString()}</td>
                  <td>
                    <div className="muted">
                      {e.last_synced_at ? new Date(e.last_synced_at).toLocaleString() : 'Never'}
                    </div>
                    {e.last_error && (
                      <div style={{ color: '#fca5a5', fontSize: '0.76rem' }}>{e.last_error}</div>
                    )}
                  </td>
                  <td>
                    <div className="row" style={{ gap: '0.35rem' }}>
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => syncEpg(e.id)}
                        disabled={busyId === `epg-${e.id}`}
                      >
                        {busyId === `epg-${e.id}` ? 'Syncing...' : 'Sync Now'}
                      </button>
                      {e.type === 'xmltv_url' && (
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={async () => {
                            await api.deleteEpgSource(e.id);
                            await loadAll();
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Category Visibility */}
      {categories.length > 0 && (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.6rem' }}>
            <h3 style={{ margin: 0 }}>Category Visibility ({categories.length})</h3>
            <input
              type="text"
              placeholder="Filter categories..."
              value={catFilter}
              onChange={(e) => setCatFilter(e.target.value)}
              style={{ maxWidth: 240 }}
            />
          </div>
          <div className="channel-list" style={{ maxHeight: 260 }}>
            {visibleCats.map((cat) => (
              <div key={cat.id} className="channel-item" onClick={() => toggleCategoryHidden(cat)}>
                <div>
                  <span style={{ opacity: cat.hidden ? 0.5 : 1, fontWeight: 500 }}>{cat.name}</span>{' '}
                  <span className="muted">
                    ({cat.channel_count} ch · {cat.source_name})
                  </span>
                </div>
                <span className={`badge ${cat.hidden ? 'badge-cancelled' : 'badge-completed'}`}>
                  {cat.hidden ? 'Hidden' : 'Visible'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* App Version & Docker Updates */}
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <h3 style={{ margin: 0 }}>Version &amp; Updates</h3>
          <button
            type="button"
            className="btn btn-sm"
            onClick={checkUpdatesNow}
            disabled={busyId === 'check-update'}
          >
            {busyId === 'check-update' ? 'Checking...' : 'Check for Updates'}
          </button>
        </div>
        <div className="muted" style={{ marginBottom: '0.5rem' }}>
          Installed: <strong style={{ color: 'var(--text)' }}>v{APP_VERSION}</strong> (built{' '}
          {BUILD_TIMESTAMP})
          {updateInfo?.latest_version && (
            <>
              {' '}
              · Latest release:{' '}
              <a href={updateInfo.release_url} target="_blank" rel="noreferrer">
                v{updateInfo.latest_version} ↗
              </a>
            </>
          )}
        </div>
        {updateInfo?.update_available && (
          <div className="alert alert-info" style={{ marginBottom: 0 }}>
            <strong>New version v{updateInfo.latest_version} available!</strong> Update with Docker
            (all settings, channels, and Auto-Record rules in <code>/data</code> are preserved):
            <div style={{ marginTop: '0.4rem' }}>
              <code>{updateInfo.update_command}</code>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
