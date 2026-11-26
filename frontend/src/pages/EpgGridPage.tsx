import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  AppSettings,
  Category,
  EPGChannelOption,
  EPGGridChannel,
  EPGGridResponse,
  EPGProgram,
  EPGProgramSearchItem,
} from '../api/client';
import { ScheduleModal, ScheduleModalInitial } from '../components/ScheduleModal';

const ROW_HEIGHT = 58;
const PX_PER_MIN = 3.2; // 192px per hour -> full 24h day = 4608px
const WINDOW_HOURS = 24;
const EPG_SCOPE_STORAGE_KEY = 'uhf_scheduler.epg_scope';

interface Props {
  onGoToSettings: () => void;
}

function startOfLocalDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

function formatLocalYmd(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const EpgGridPage: React.FC<Props> = ({ onGoToSettings }) => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [selectedScope, setSelectedScope] = useState<string>(() => {
    try {
      return localStorage.getItem(EPG_SCOPE_STORAGE_KEY) || 'favorites';
    } catch {
      return 'favorites';
    }
  });
  const hasSavedScopeRef = useRef<boolean>(
    (() => {
      try {
        return Boolean(localStorage.getItem(EPG_SCOPE_STORAGE_KEY));
      } catch {
        return false;
      }
    })()
  );

  const updateScope = (nextScope: string) => {
    setSelectedScope(nextScope);
    hasSavedScopeRef.current = true;
    try {
      localStorage.setItem(EPG_SCOPE_STORAGE_KEY, nextScope);
    } catch {
      // ignore storage errors
    }
  };
  const [searchQ, setSearchQ] = useState('');
  const [programQ, setProgramQ] = useState('');
  const [searchDescriptions, setSearchDescriptions] = useState(false);
  const [programResults, setProgramResults] = useState<EPGProgramSearchItem[]>([]);
  const [selectedMirrorByRow, setSelectedMirrorByRow] = useState<Record<string, number>>({});
  const [programLoading, setProgramLoading] = useState(false);
  const [selectedDay, setSelectedDay] = useState<Date>(() => startOfLocalDay(new Date()));

  const [grid, setGrid] = useState<EPGGridResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduleTarget, setScheduleTarget] = useState<ScheduleModalInitial | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Per-channel EPG ID override modal state
  const [mappingChannel, setMappingChannel] = useState<EPGGridChannel | null>(null);
  const [epgSearch, setEpgSearch] = useState('');
  const [epgOptions, setEpgOptions] = useState<EPGChannelOption[]>([]);

  // Row virtualization & auto-scroll state
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const hasAutoScrolledRef = useRef(false);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(520);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => {});
    api
      .listCategories(false)
      .then((cats) => {
        setCategories(cats);
      })
      .catch(() => {});
  }, []);

  const windowStart = selectedDay;
  const windowEnd = useMemo(
    () => new Date(windowStart.getTime() + WINDOW_HOURS * 3600 * 1000),
    [windowStart]
  );

  const loadGrid = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getEpgGrid({
        category_id: selectedScope === 'all' ? undefined : selectedScope,
        q: searchQ.trim() || undefined,
        start: windowStart.toISOString(),
        end: windowEnd.toISOString(),
        limit: 300,
      })
      .then((res) => {
        if (
          !hasSavedScopeRef.current &&
          selectedScope === 'favorites' &&
          res.channels.length === 0 &&
          categories.length > 0 &&
          !searchQ
        ) {
          setSelectedScope(String(categories[0].id));
          return;
        }
        setGrid(res);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load EPG grid'))
      .finally(() => setLoading(false));
  }, [selectedScope, searchQ, windowStart, windowEnd, categories]);

  useEffect(() => {
    const t = setTimeout(loadGrid, 150);
    return () => clearTimeout(t);
  }, [loadGrid]);

  useEffect(() => {
    const trimmed = programQ.trim();
    if (trimmed.length < 2) {
      setProgramResults([]);
      setProgramLoading(false);
      return;
    }
    setProgramLoading(true);
    const t = setTimeout(() => {
      api
        .searchEpgPrograms({
          q: trimmed,
          category_id: selectedScope,
          upcoming_only: true,
          search_descriptions: searchDescriptions,
          limit: 120,
        })
        .then((res) => {
          setProgramResults(res.items);
          setSelectedMirrorByRow({});
        })
        .catch((err) => setError(err instanceof Error ? err.message : 'Program search failed'))
        .finally(() => setProgramLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [programQ, selectedScope, searchDescriptions]);

  useEffect(() => {
    if (!mappingChannel) return;
    const t = setTimeout(() => {
      api.searchEpgChannels(epgSearch).then(setEpgOptions).catch(() => {});
    }, 180);
    return () => clearTimeout(t);
  }, [mappingChannel, epgSearch]);

  const totalWidth = WINDOW_HOURS * 60 * PX_PER_MIN;
  const startMs = windowStart.getTime();
  const endMs = windowEnd.getTime();
  const nowMs = Date.now();
  const nowLeft =
    nowMs >= startMs && nowMs <= endMs ? ((nowMs - startMs) / 60000) * PX_PER_MIN : null;

  const scrollToHour = useCallback((hour: number) => {
    const el = viewportRef.current;
    if (!el) return;
    const targetLeft = Math.max(0, hour * 60 * PX_PER_MIN - 60);
    el.scrollTo({ left: targetLeft, behavior: 'smooth' });
  }, []);

  // Auto-scroll horizontally to ~1 hour before "Now" on initial load
  useEffect(() => {
    if (!grid || hasAutoScrolledRef.current) return;
    hasAutoScrolledRef.current = true;
    const el = viewportRef.current;
    if (!el) return;
    if (nowLeft !== null) {
      el.scrollLeft = Math.max(0, nowLeft - 140);
    } else {
      el.scrollLeft = Math.max(0, 19 * 60 * PX_PER_MIN);
    }
  }, [grid, nowLeft]);

  const ticks = useMemo(() => {
    const arr: { left: number; label: string; isHour: boolean }[] = [];
    for (let m = 0; m < WINDOW_HOURS * 60; m += 30) {
      const d = new Date(startMs + m * 60000);
      arr.push({
        left: m * PX_PER_MIN,
        label: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        isHour: m % 60 === 0,
      });
    }
    return arr;
  }, [startMs]);

  const dayButtons = useMemo(() => {
    const today = startOfLocalDay(new Date());
    return Array.from({ length: 8 }, (_, i) => {
      const d = new Date(today.getTime() + i * 24 * 3600 * 1000);
      const label =
        i === 0
          ? 'Today'
          : i === 1
            ? 'Tomorrow'
            : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
      return { date: d, ymd: formatLocalYmd(d), label };
    });
  }, []);

  const shiftDays = (deltaDays: number) => {
    setSelectedDay((prev) => startOfLocalDay(new Date(prev.getTime() + deltaDays * 24 * 3600 * 1000)));
  };

  const jumpNow = () => {
    const today = startOfLocalDay(new Date());
    setSelectedDay(today);
    const now = new Date();
    const hourFloat = now.getHours() + now.getMinutes() / 60;
    setTimeout(() => scrollToHour(Math.max(0, hourFloat - 0.5)), 50);
  };

  const openProgramModal = (ch: EPGGridChannel, prog: EPGProgram) => {
    const durMin = Math.max(1, Math.round((prog.end_ts - prog.start_ts) / 60));
    setScheduleTarget({
      mode: 'create',
      channelId: ch.id,
      channelName: ch.name,
      title: prog.title,
      startIso: prog.start_time,
      durationMin: durMin,
      prePaddingMin: settings?.defaults.pre_padding_min ?? 2,
      postPaddingMin: settings?.defaults.post_padding_min ?? 5,
    });
  };

  const openSearchResultModal = (item: EPGProgramSearchItem, rowKey: string) => {
    const durMin = Math.max(1, Math.round((item.end_ts - item.start_ts) / 60));
    const chosenId = selectedMirrorByRow[rowKey] ?? item.channel_id;
    const chosenCh =
      item.channels?.find((c) => c.channel_id === chosenId) || {
        channel_id: item.channel_id,
        channel_name: item.channel_name,
      };
    setScheduleTarget({
      mode: 'create',
      channelId: chosenCh.channel_id,
      channelName: chosenCh.channel_name,
      title: item.title,
      startIso: item.start_time,
      durationMin: durMin,
      prePaddingMin: settings?.defaults.pre_padding_min ?? 2,
      postPaddingMin: settings?.defaults.post_padding_min ?? 5,
    });
  };

  const openManualForChannel = (ch: EPGGridChannel) => {
    setScheduleTarget({
      mode: 'create',
      channelId: ch.id,
      channelName: ch.name,
      title: '',
      startIso: new Date(Math.max(Date.now() + 60000, startMs)).toISOString(),
      durationMin: 60,
      prePaddingMin: settings?.defaults.pre_padding_min ?? 2,
      postPaddingMin: settings?.defaults.post_padding_min ?? 5,
    });
  };

  const saveEpgOverride = async (xmltvId: string | null) => {
    if (!mappingChannel) return;
    await api.setChannelEpgOverride(mappingChannel.id, xmltvId);
    setMappingChannel(null);
    loadGrid();
  };

  const selectedYmd = formatLocalYmd(selectedDay);
  const channels = grid?.channels || [];
  const overscan = 5;
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - overscan);
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT) + overscan * 2;
  const endIdx = Math.min(channels.length, startIdx + visibleCount);
  const visibleChannels = channels.slice(startIdx, endIdx);
  const topPad = startIdx * ROW_HEIGHT;
  const bottomPad = Math.max(0, (channels.length - endIdx) * ROW_HEIGHT);

  return (
    <div>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.65rem' }}>
          <div className="row">
            <select
              value={selectedScope}
              onChange={(e) => updateScope(e.target.value)}
              style={{ minWidth: 210, width: 'auto' }}
            >
              <option value="favorites">★ Favorites</option>
              <option value="all">All Visible Categories</option>
              {categories.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name} ({c.channel_count})
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Filter channels..."
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              style={{ width: 170 }}
            />
            <div className="row" style={{ gap: '0.4rem' }}>
              <input
                type="text"
                placeholder="Search program titles (all upcoming days)..."
                value={programQ}
                onChange={(e) => setProgramQ(e.target.value)}
                style={{ width: 250 }}
              />
              {programQ && (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setProgramQ('')}
                  title="Clear program search and return to grid"
                >
                  ✕
                </button>
              )}
              <label className="row muted" style={{ gap: '0.25rem', cursor: 'pointer', fontSize: '0.8rem' }}>
                <input
                  type="checkbox"
                  checked={searchDescriptions}
                  onChange={(e) => setSearchDescriptions(e.target.checked)}
                />
                Include descriptions
              </label>
            </div>
          </div>

          <div className="row">
            <button type="button" className="btn btn-sm" onClick={() => shiftDays(-1)}>
              « Prev Day
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={jumpNow}>
              Now
            </button>
            <button type="button" className="btn btn-sm" onClick={() => shiftDays(1)}>
              Next Day »
            </button>
            <input
              type="date"
              value={selectedYmd}
              onChange={(e) => {
                if (!e.target.value) return;
                const d = new Date(`${e.target.value}T00:00:00`);
                if (!Number.isNaN(d.getTime())) setSelectedDay(startOfLocalDay(d));
              }}
              style={{ width: 145 }}
            />
          </div>
        </div>

        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <div className="row" style={{ gap: '0.3rem' }}>
            {dayButtons.map((db) => (
              <button
                key={db.ymd}
                type="button"
                className={`btn btn-sm ${selectedYmd === db.ymd ? 'btn-primary' : ''}`}
                onClick={() => setSelectedDay(db.date)}
              >
                {db.label}
              </button>
            ))}
          </div>

          <div className="row" style={{ gap: '0.3rem' }}>
            <span className="muted">Jump to:</span>
            <button type="button" className="btn btn-sm" onClick={() => scrollToHour(0)}>
              00:00
            </button>
            <button type="button" className="btn btn-sm" onClick={() => scrollToHour(8)}>
              08:00
            </button>
            <button type="button" className="btn btn-sm" onClick={() => scrollToHour(12)}>
              12:00
            </button>
            <button type="button" className="btn btn-sm" onClick={() => scrollToHour(18)}>
              18:00
            </button>
            <button type="button" className="btn btn-sm" onClick={() => scrollToHour(20)}>
              20:00
            </button>
          </div>
        </div>

        <div className="muted">
          Full 24-hour guide for{' '}
          <strong style={{ color: 'var(--text)' }}>
            {windowStart.toLocaleDateString([], {
              weekday: 'long',
              month: 'short',
              day: 'numeric',
              year: 'numeric',
            })}
          </strong>{' '}
          (scroll horizontally for 00:00 – 24:00) ·{' '}
          {loading ? 'Loading...' : `${channels.length} channels loaded`}
        </div>
      </div>

      {toast && <div className="alert alert-success">{toast}</div>}
      {error && <div className="alert alert-error">{error}</div>}
      {grid?.uhf_warning && (
        <div className="alert alert-warn">
          Could not overlay live UHF recordings: {grid.uhf_warning}
        </div>
      )}

      {grid && grid.epg_source_count === 0 && (
        <div className="alert alert-info row" style={{ justifyContent: 'space-between' }}>
          <span>
            No EPG sources configured yet. Manual scheduling works right away, or enable Provider
            EPG / add a 7–14 day XMLTV URL to populate the full grid.
          </span>
          <button type="button" className="btn btn-sm btn-primary" onClick={onGoToSettings}>
            Configure EPG in Settings
          </button>
        </div>
      )}

      {programQ.trim().length >= 2 ? (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.75rem' }}>
            <h3 style={{ margin: 0 }}>
              Program Search Results for &ldquo;{programQ.trim()}&rdquo; (
              {programLoading ? 'searching...' : `${programResults.length} upcoming`})
            </h3>
            <button type="button" className="btn btn-sm" onClick={() => setProgramQ('')}>
              Back to 24h Grid
            </button>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Date &amp; Time (Local)</th>
                  <th>Program</th>
                  <th>Channel</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {!programLoading && programResults.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="muted" style={{ padding: '1rem' }}>
                      No upcoming programs matched &ldquo;{programQ.trim()}&rdquo; in this scope.
                      Try switching the dropdown to &ldquo;All Visible Categories&rdquo;.
                    </td>
                  </tr>
                ) : (
                  programResults.map((item) => {
                    const durMin = Math.max(1, Math.round((item.end_ts - item.start_ts) / 60));
                    const rowKey = `${item.id}-${item.start_ts}`;
                    const mirrors = item.channels && item.channels.length > 0 ? item.channels : [];
                    const selectedChId = selectedMirrorByRow[rowKey] ?? item.channel_id;
                    return (
                      <tr key={rowKey}>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <div style={{ fontWeight: 600 }}>
                            {new Date(item.start_time).toLocaleDateString([], {
                              weekday: 'short',
                              month: 'short',
                              day: 'numeric',
                            })}
                          </div>
                          <div className="muted">
                            {new Date(item.start_time).toLocaleTimeString([], {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}{' '}
                            –{' '}
                            {new Date(item.end_time).toLocaleTimeString([], {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}{' '}
                            ({durMin}m)
                          </div>
                        </td>
                        <td>
                          <div style={{ fontWeight: 600 }}>{item.title}</div>
                          {item.description && (
                            <div
                              className="muted"
                              style={{
                                fontSize: '0.78rem',
                                maxWidth: 480,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                              title={item.description}
                            >
                              {item.description}
                            </div>
                          )}
                        </td>
                        <td>
                          <div className="row" style={{ gap: '0.45rem' }}>
                            {item.logo_url && (
                              <img
                                src={item.logo_url}
                                alt=""
                                className="channel-logo"
                                loading="lazy"
                                onError={(e) => {
                                  (e.currentTarget as HTMLImageElement).style.display = 'none';
                                }}
                              />
                            )}
                            <div style={{ minWidth: 160 }}>
                              {mirrors.length > 1 ? (
                                <select
                                  value={selectedChId}
                                  onChange={(e) =>
                                    setSelectedMirrorByRow((prev) => ({
                                      ...prev,
                                      [rowKey]: Number(e.target.value),
                                    }))
                                  }
                                  style={{ padding: '0.25rem 0.45rem', fontSize: '0.84rem' }}
                                >
                                  {mirrors.map((m) => (
                                    <option key={m.channel_id} value={m.channel_id}>
                                      {m.is_favorite ? '★ ' : ''}
                                      {m.channel_name}
                                      {m.category_name ? ` (${m.category_name})` : ''}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <>
                                  <div style={{ fontWeight: 600 }}>
                                    {item.is_favorite ? '★ ' : ''}
                                    {item.channel_name}
                                  </div>
                                  <div className="muted" style={{ fontSize: '0.74rem' }}>
                                    {item.category_name || 'Uncategorized'}
                                  </div>
                                </>
                              )}
                            </div>
                          </div>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            onClick={() => openSearchResultModal(item, rowKey)}
                          >
                            Schedule
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
      <div
        className="epg-viewport"
        ref={viewportRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          setScrollTop(el.scrollTop);
          setViewportHeight(el.clientHeight);
        }}
      >
        <div className="epg-header-row" style={{ width: 210 + totalWidth }}>
          <div className="epg-channel-col">Channel</div>
          <div className="epg-timeline-ruler" style={{ width: totalWidth }}>
            {ticks.map((t) => (
              <div
                key={t.left}
                className="epg-tick"
                style={{
                  left: t.left,
                  fontWeight: t.isHour ? 600 : 400,
                  color: t.isHour ? 'var(--text)' : 'var(--muted)',
                }}
              >
                {t.label}
              </div>
            ))}
          </div>
        </div>

        <div style={{ width: 210 + totalWidth, paddingTop: topPad, paddingBottom: bottomPad }}>
          {visibleChannels.map((ch) => (
            <div key={ch.id} className="epg-row">
              <div className="epg-channel-col">
                {ch.logo_url ? (
                  <img
                    src={ch.logo_url}
                    alt=""
                    className="channel-logo"
                    loading="lazy"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = 'none';
                    }}
                  />
                ) : null}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      fontWeight: 600,
                      fontSize: '0.82rem',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    title={ch.name}
                  >
                    {ch.name}
                  </div>
                  <div className="row" style={{ gap: '0.3rem', marginTop: 2 }}>
                    <button
                      type="button"
                      className="btn btn-sm"
                      style={{ padding: '1px 5px', fontSize: '0.7rem' }}
                      onClick={() => openManualForChannel(ch)}
                      title="Manual record on this channel"
                    >
                      + Rec
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      style={{ padding: '1px 5px', fontSize: '0.7rem' }}
                      onClick={() => {
                        setMappingChannel(ch);
                        setEpgSearch(ch.name.split(' ')[0] || '');
                      }}
                      title="Override XMLTV EPG ID mapping"
                    >
                      EPG
                    </button>
                  </div>
                </div>
              </div>

              <div className="epg-track" style={{ width: totalWidth }}>
                {nowLeft !== null && <div className="epg-now-line" style={{ left: nowLeft }} />}

                {ch.programs.length === 0 ? (
                  <div
                    className="muted"
                    style={{ padding: '1rem 0.75rem', fontSize: '0.78rem' }}
                  >
                    No guide data on this day ({ch.effective_epg_id || 'no EPG ID'})
                  </div>
                ) : (
                  ch.programs.map((prog) => {
                    const pStartMs = Math.max(startMs, prog.start_ts * 1000);
                    const pEndMs = Math.min(endMs, prog.end_ts * 1000);
                    const left = ((pStartMs - startMs) / 60000) * PX_PER_MIN;
                    const width = Math.max(28, ((pEndMs - pStartMs) / 60000) * PX_PER_MIN - 2);
                    return (
                      <div
                        key={prog.id}
                        className="epg-prog"
                        style={{ left, width }}
                        title={`${prog.title}\n${new Date(prog.start_time).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })} - ${new Date(prog.end_time).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}\n${prog.description || ''}`}
                        onClick={() => openProgramModal(ch, prog)}
                      >
                        <div className="epg-prog-title">{prog.title}</div>
                        <div className="epg-prog-time">
                          {new Date(prog.start_time).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}{' '}
                          –{' '}
                          {new Date(prog.end_time).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </div>
                      </div>
                    );
                  })
                )}

                {ch.recordings.map((rec) => {
                  const rStartMs = Math.max(startMs, rec.start_ts * 1000);
                  const rEndMs = Math.min(endMs, rec.end_ts * 1000);
                  const left = ((rStartMs - startMs) / 60000) * PX_PER_MIN;
                  const width = Math.max(24, ((rEndMs - rStartMs) / 60000) * PX_PER_MIN);
                  return (
                    <div
                      key={rec.id}
                      className="epg-rec-overlay"
                      style={{ left, width }}
                      title={`Scheduled: ${rec.name}`}
                    >
                      <span className="epg-rec-pill">
                        {rec.status === 'recording' ? 'REC' : 'SCHED'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
      )}

      {scheduleTarget && (
        <ScheduleModal
          initial={scheduleTarget}
          onClose={() => setScheduleTarget(null)}
          onSaved={(rec) => {
            setScheduleTarget(null);
            setToast(`Scheduled "${rec.name}" (${new Date(rec.start_time).toLocaleString()}).`);
            loadGrid();
          }}
        />
      )}

      {mappingChannel && (
        <div className="modal-backdrop" onClick={() => setMappingChannel(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.6rem' }}>
              <h3 style={{ margin: 0 }}>Map XMLTV EPG ID — {mappingChannel.name}</h3>
              <button type="button" className="btn btn-sm" onClick={() => setMappingChannel(null)}>
                ✕
              </button>
            </div>
            <div className="muted" style={{ marginBottom: '0.6rem' }}>
              Auto ID: <code>{mappingChannel.epg_channel_id || '(none)'}</code> · Override:{' '}
              <code>{mappingChannel.epg_override_id || '(none)'}</code>
            </div>
            <div className="row" style={{ marginBottom: '0.6rem' }}>
              <input
                type="text"
                placeholder="Search XMLTV channel name or ID..."
                value={epgSearch}
                onChange={(e) => setEpgSearch(e.target.value)}
              />
              {mappingChannel.epg_override_id && (
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  onClick={() => saveEpgOverride(null)}
                >
                  Clear Override
                </button>
              )}
            </div>
            <div className="channel-list" style={{ maxHeight: 280 }}>
              {epgOptions.map((opt) => (
                <div
                  key={`${opt.epg_source_id}-${opt.xmltv_id}`}
                  className="channel-item"
                  onClick={() => saveEpgOverride(opt.xmltv_id)}
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{opt.display_name}</div>
                    <div className="muted" style={{ fontSize: '0.76rem' }}>
                      <code>{opt.xmltv_id}</code> · {opt.epg_source_name}
                    </div>
                  </div>
                  <span className="btn btn-sm">Use</span>
                </div>
              ))}
              {epgOptions.length === 0 && (
                <div style={{ padding: '0.8rem', color: 'var(--muted)' }}>
                  No matching XMLTV channels found. Sync an EPG source first.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
