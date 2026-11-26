import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  api,
  AppSettings,
  AutoRecordHistoryItem,
  AutoRecordRule,
  Channel,
  EPGProgramSearchItem,
} from '../api/client';
import { ChannelPicker } from '../components/ChannelPicker';

export const AutoRecordPage: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [rules, setRules] = useState<AutoRecordRule[]>([]);
  const [history, setHistory] = useState<AutoRecordHistoryItem[]>([]);
  const [historyFilterRuleId, setHistoryFilterRuleId] = useState<number | null>(null);

  // Create form state
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [programSearchQ, setProgramSearchQ] = useState('');
  const [channelPrograms, setChannelPrograms] = useState<EPGProgramSearchItem[]>([]);
  const [searchingPrograms, setSearchingPrograms] = useState(false);
  const [titlePattern, setTitlePattern] = useState('');
  const [matchMode, setMatchMode] = useState<'starts_with' | 'contains' | 'exact'>('starts_with');
  const [useCustomPad, setUseCustomPad] = useState(false);
  const [prePad, setPrePad] = useState(2);
  const [postPad, setPostPad] = useState(5);
  const [skipIfConflict, setSkipIfConflict] = useState(true);

  const [busy, setBusy] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [s, r, h] = await Promise.all([
        api.getSettings(),
        api.listRules(),
        api.listRuleHistory(historyFilterRuleId),
      ]);
      setSettings(s);
      setRules(r);
      setHistory(h);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed loading auto-record rules');
    }
  }, [historyFilterRuleId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (settings) {
      setPrePad(settings.defaults.pre_padding_min);
      setPostPad(settings.defaults.post_padding_min);
    }
  }, [settings]);

  // Search upcoming EPG programs:
  // - If a channel is selected -> strictly filter to that selected channel (works even with empty query!)
  // - If no channel is selected yet -> search across all visible channels when user types >= 2 chars
  useEffect(() => {
    const trimmed = programSearchQ.trim();
    if (!selectedChannel && trimmed.length < 2) {
      setChannelPrograms([]);
      setSearchingPrograms(false);
      return;
    }
    setSearchingPrograms(true);
    const t = setTimeout(() => {
      api
        .searchEpgPrograms({
          channel_id: selectedChannel ? selectedChannel.id : undefined,
          q: trimmed || undefined,
          upcoming_only: true,
          limit: 150,
        })
        .then((res) => setChannelPrograms(res.items))
        .catch(() => setChannelPrograms([]))
        .finally(() => setSearchingPrograms(false));
    }, 180);
    return () => clearTimeout(t);
  }, [selectedChannel, programSearchQ]);

  const groupedChannelPrograms = useMemo(() => {
    const map = new Map<
      string,
      {
        key: string;
        title: string;
        channelId: number;
        channelName: string;
        categoryName: string | null;
        logoUrl: string | null;
        isFavorite: boolean;
        count: number;
        nextStart: string;
        description: string | null;
      }
    >();
    for (const p of channelPrograms) {
      const key = `${p.channel_id}::${p.title.trim().toLowerCase()}`;
      const existing = map.get(key);
      if (!existing) {
        map.set(key, {
          key,
          title: p.title,
          channelId: p.channel_id,
          channelName: p.channel_name,
          categoryName: p.category_name,
          logoUrl: p.logo_url,
          isFavorite: p.is_favorite,
          count: 1,
          nextStart: p.start_time,
          description: p.description,
        });
      } else {
        existing.count += 1;
      }
    }
    return Array.from(map.values());
  }, [channelPrograms]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    setErrorMsg(null);
    if (!selectedChannel) {
      setErrorMsg('Please select a channel first.');
      return;
    }
    if (!titlePattern.trim()) {
      setErrorMsg('Please enter a program title to match.');
      return;
    }
    setBusy('create');
    try {
      const created = await api.createRule({
        channel_id: selectedChannel.id,
        title_pattern: titlePattern.trim(),
        match_mode: matchMode,
        pre_padding_min: useCustomPad ? prePad : null,
        post_padding_min: useCustomPad ? postPad : null,
        skip_if_conflict: skipIfConflict,
        enabled: true,
        run_now: true,
      });
      const rs = created.run_summary;
      setStatusMsg(
        rs
          ? `Rule saved for "${created.title_pattern}" on ${created.channel_name}: scheduled ${rs.scheduled}, skipped ${rs.skipped_conflict} conflict(s).`
          : `Rule saved for "${created.title_pattern}" on ${created.channel_name}.`
      );
      setTitlePattern('');
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to create rule');
    } finally {
      setBusy(null);
    }
  };

  const handleToggleEnabled = async (rule: AutoRecordRule) => {
    setBusy(`toggle-${rule.id}`);
    setErrorMsg(null);
    try {
      await api.updateRule(rule.id, { enabled: !rule.enabled });
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to update rule');
    } finally {
      setBusy(null);
    }
  };

  const handleToggleSkipConflict = async (rule: AutoRecordRule) => {
    setBusy(`conflict-${rule.id}`);
    setErrorMsg(null);
    try {
      await api.updateRule(rule.id, { skip_if_conflict: !rule.skip_if_conflict });
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to update rule');
    } finally {
      setBusy(null);
    }
  };

  const handleRunRule = async (rule: AutoRecordRule) => {
    setBusy(`run-${rule.id}`);
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const res = await api.runSingleRule(rule.id);
      if (res.uhf_error) {
        setErrorMsg(`UHF Server error: ${res.uhf_error}`);
      } else {
        setStatusMsg(
          `Checked "${rule.title_pattern}": scheduled ${res.scheduled}, skipped ${res.skipped_conflict} conflict(s).`
        );
      }
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Rule evaluation failed');
    } finally {
      setBusy(null);
    }
  };

  const handleRunAll = async (syncEpgFirst: boolean) => {
    setBusy(syncEpgFirst ? 'sync-run' : 'run-all');
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const res = await api.runAllRules(syncEpgFirst);
      if (res.uhf_error) {
        setErrorMsg(`UHF Server error: ${res.uhf_error}`);
      } else {
        setStatusMsg(
          `${syncEpgFirst ? 'EPG synced & ' : ''}Evaluated ${res.evaluated_rules} rule(s): scheduled ${res.scheduled}, skipped ${res.skipped_conflict} conflict(s).`
        );
      }
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Evaluation failed');
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (rule: AutoRecordRule) => {
    if (
      !window.confirm(
        `Delete auto-record rule "${rule.title_pattern}" on ${rule.channel_name}? (Already-scheduled recordings in UHF Server are kept.)`
      )
    ) {
      return;
    }
    await api.deleteRule(rule.id);
    if (historyFilterRuleId === rule.id) setHistoryFilterRuleId(null);
    await loadAll();
  };

  const handleForceSchedule = async (item: AutoRecordHistoryItem) => {
    setBusy(`force-${item.id}`);
    setStatusMsg(null);
    setErrorMsg(null);
    try {
      const res = await api.forceScheduleHistory(item.id);
      setStatusMsg(`Force-scheduled "${res.name}" on ${item.channel_name}.`);
      await loadAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Force schedule failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      {statusMsg && <div className="alert alert-success">{statusMsg}</div>}
      {errorMsg && <div className="alert alert-error">{errorMsg}</div>}

      <div className="grid-2">
        <div className="card">
          <h2>1. Pick Channel</h2>
          <ChannelPicker selectedChannel={selectedChannel} onSelect={setSelectedChannel} />
        </div>

        <form className="card" onSubmit={handleCreate}>
          <h2>2. Create Auto-Record Rule (Series Pass)</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            Automatically schedules every upcoming airing of this program on the chosen channel
            whenever the EPG syncs (every {settings?.defaults.sync_interval_hours ?? 12}h in the
            background).
          </p>

          {selectedChannel ? (
            <div className="alert alert-info row" style={{ justifyContent: 'space-between', marginBottom: '0.7rem' }}>
              <span>
                Selected Channel: <strong>{selectedChannel.name}</strong>{' '}
                <span className="muted">({selectedChannel.category_name || 'Uncategorized'})</span>
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setSelectedChannel(null)}
                title="Clear channel filter to search programs across all channels"
              >
                Clear Channel
              </button>
            </div>
          ) : (
            <div className="muted" style={{ marginBottom: '0.7rem' }}>
              Pick a channel on the left to filter shows on that channel — or search for a program
              below to pick both the program and its channel in one click.
            </div>
          )}

          <div className="field">
            <label>
              {selectedChannel
                ? `Search Upcoming Programs on ${selectedChannel.name}`
                : 'Search Upcoming Programs (all visible channels)'}{' '}
              <span className="muted">(click a result to fill the rule)</span>
            </label>
            <div className="row" style={{ gap: '0.35rem' }}>
              <input
                type="text"
                value={programSearchQ}
                onChange={(e) => setProgramSearchQ(e.target.value)}
                placeholder={
                  selectedChannel
                    ? `Filter upcoming shows on ${selectedChannel.name}...`
                    : 'Type at least 2 characters (e.g. Capital, Enquête)...'
                }
                style={{ flex: 1 }}
              />
              {programSearchQ && (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setProgramSearchQ('')}
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {(selectedChannel || programSearchQ.trim().length >= 2) && (
            <div className="channel-list" style={{ maxHeight: 195, marginBottom: '0.8rem' }}>
              {searchingPrograms ? (
                <div style={{ padding: '0.6rem', color: 'var(--muted)' }}>
                  Searching upcoming EPG programs...
                </div>
              ) : groupedChannelPrograms.length === 0 ? (
                <div style={{ padding: '0.6rem', color: 'var(--muted)' }}>
                  {programSearchQ.trim()
                    ? `No upcoming shows matching "${programSearchQ.trim()}"${
                        selectedChannel ? ` on ${selectedChannel.name}` : ''
                      }.`
                    : `No upcoming EPG listings found for ${selectedChannel?.name} (you can still type a title below).`}
                </div>
              ) : (
                groupedChannelPrograms.map((gp) => (
                  <div
                    key={gp.key}
                    className={`channel-item ${
                      selectedChannel?.id === gp.channelId &&
                      titlePattern.trim().toLowerCase() === gp.title.trim().toLowerCase()
                        ? 'selected'
                        : ''
                    }`}
                    onClick={() => {
                      setTitlePattern(gp.title);
                      if (!selectedChannel) {
                        setSelectedChannel({
                          id: gp.channelId,
                          source_id: 0,
                          source_name: '',
                          category_id: null,
                          category_name: gp.categoryName,
                          name: gp.channelName,
                          logo_url: gp.logoUrl,
                          epg_channel_id: null,
                          epg_override_id: null,
                          is_favorite: gp.isFavorite,
                        });
                      }
                    }}
                    title={gp.description || gp.title}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.88rem' }}>{gp.title}</div>
                      <div className="muted" style={{ fontSize: '0.75rem' }}>
                        {!selectedChannel && (
                          <>
                            <strong style={{ color: 'var(--text)' }}>{gp.channelName}</strong> ·{' '}
                          </>
                        )}
                        Next:{' '}
                        {new Date(gp.nextStart).toLocaleString([], {
                          weekday: 'short',
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </div>
                    </div>
                    <span className="badge badge-scheduled">
                      {gp.count} {gp.count === 1 ? 'airing' : 'airings'}
                    </span>
                  </div>
                ))
              )}
            </div>
          )}

          <div className="field">
            <label>Program Name (case &amp; accent insensitive)</label>
            <input
              type="text"
              value={titlePattern}
              onChange={(e) => setTitlePattern(e.target.value)}
              placeholder="e.g. Capital, Enquête exclusive, 90' Enquêtes"
              required
            />
          </div>

          <div className="field">
            <label>Title Match Mode</label>
            <select
              value={matchMode}
              onChange={(e) =>
                setMatchMode(e.target.value as 'starts_with' | 'contains' | 'exact')
              }
            >
              <option value="starts_with">Starts with (Recommended — matches season/episode suffixes)</option>
              <option value="contains">Contains anywhere in title</option>
              <option value="exact">Exact title match only</option>
            </select>
          </div>

          <label className="row" style={{ marginBottom: '0.75rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={skipIfConflict}
              onChange={(e) => setSkipIfConflict(e.target.checked)}
            />
            <span>
              <strong>Do not schedule on top of existing programming</strong>{' '}
              <span className="muted">
                (respects max {settings?.defaults.max_simultaneous_streams ?? 1} simultaneous
                stream{(settings?.defaults.max_simultaneous_streams ?? 1) > 1 ? 's' : ''}; trims
                shared padding on back-to-back episodes)
              </span>
            </span>
          </label>

          <label className="row muted" style={{ marginBottom: '0.6rem', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={useCustomPad}
              onChange={(e) => setUseCustomPad(e.target.checked)}
            />
            <span>
              Override default padding ({settings?.defaults.pre_padding_min ?? 2}m pre /{' '}
              {settings?.defaults.post_padding_min ?? 5}m post)
            </span>
          </label>

          {useCustomPad && (
            <div className="row">
              <div className="field">
                <label>Pre-padding (min)</label>
                <input
                  type="number"
                  min={0}
                  max={120}
                  value={prePad}
                  onChange={(e) => setPrePad(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>Post-padding (min)</label>
                <input
                  type="number"
                  min={0}
                  max={180}
                  value={postPad}
                  onChange={(e) => setPostPad(Number(e.target.value))}
                />
              </div>
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy === 'create' || !selectedChannel}
            style={{ width: '100%', justifyContent: 'center', marginTop: '0.4rem' }}
          >
            {busy === 'create' ? 'Saving & Scanning EPG...' : 'Save Rule & Schedule Matches Now'}
          </button>
        </form>
      </div>

      {/* Active Rules List */}
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.75rem' }}>
          <h3 style={{ margin: 0 }}>Auto-Record Rules ({rules.length})</h3>
          <div className="row">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => handleRunAll(false)}
              disabled={busy !== null}
            >
              {busy === 'run-all' ? 'Running...' : 'Run All Rules Now'}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => handleRunAll(true)}
              disabled={busy !== null}
            >
              {busy === 'sync-run' ? 'Syncing EPG & Running...' : 'Sync EPG & Run Rules Now'}
            </button>
          </div>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Program Pattern</th>
                <th>Channel</th>
                <th>Conflict Protection</th>
                <th>Matches</th>
                <th>Last Run</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.length === 0 ? (
                <tr>
                  <td colSpan={7} className="muted" style={{ padding: '1rem' }}>
                    No auto-record rules yet. Add one above or click any show in the EPG Grid.
                  </td>
                </tr>
              ) : (
                rules.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <span className={`badge ${r.enabled ? 'badge-completed' : 'badge-cancelled'}`}>
                        {r.enabled ? 'Active' : 'Paused'}
                      </span>
                    </td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.title_pattern}</div>
                      <div className="muted" style={{ fontSize: '0.75rem' }}>
                        Mode: {r.match_mode.replace('_', ' ')}
                        {r.pre_padding_min != null || r.post_padding_min != null
                          ? ` · Pad: ${r.pre_padding_min ?? settings?.defaults.pre_padding_min ?? 2}m/${r.post_padding_min ?? settings?.defaults.post_padding_min ?? 5}m`
                          : ''}
                      </div>
                    </td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{r.channel_name}</div>
                      <div className="muted" style={{ fontSize: '0.74rem' }}>
                        {r.category_name || r.source_name}
                      </div>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => handleToggleSkipConflict(r)}
                        disabled={busy === `conflict-${r.id}`}
                        title="Toggle 'Do not schedule on top of existing programming'"
                      >
                        {r.skip_if_conflict ? '✓ Skip conflicts' : 'Allow overlaps'}
                      </button>
                    </td>
                    <td>
                      <div>{r.total_scheduled} scheduled</div>
                      {r.total_skipped > 0 && (
                        <div style={{ color: '#fde68a', fontSize: '0.76rem' }}>
                          {r.total_skipped} skipped
                        </div>
                      )}
                    </td>
                    <td className="muted" style={{ fontSize: '0.78rem' }}>
                      {r.last_run_at ? new Date(r.last_run_at).toLocaleString() : 'Never'}
                    </td>
                    <td>
                      <div className="row" style={{ gap: '0.3rem' }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={() => handleRunRule(r)}
                          disabled={busy === `run-${r.id}`}
                        >
                          Run
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => handleToggleEnabled(r)}
                          disabled={busy === `toggle-${r.id}`}
                        >
                          {r.enabled ? 'Pause' : 'Resume'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() =>
                            setHistoryFilterRuleId(historyFilterRuleId === r.id ? null : r.id)
                          }
                        >
                          Log
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => handleDelete(r)}
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
      </div>

      {/* Activity History */}
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.6rem' }}>
          <h3 style={{ margin: 0 }}>
            Rule Activity History ({history.length})
            {historyFilterRuleId != null && (
              <span className="muted" style={{ marginLeft: '0.5rem', fontWeight: 400 }}>
                (filtered to rule #{historyFilterRuleId})
              </span>
            )}
          </h3>
          {historyFilterRuleId != null && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setHistoryFilterRuleId(null)}
            >
              Show All Rules
            </button>
          )}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Result</th>
                <th>Air Time (Local)</th>
                <th>Program</th>
                <th>Channel</th>
                <th>Details</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan={6} className="muted" style={{ padding: '0.8rem' }}>
                    No evaluated airings recorded yet.
                  </td>
                </tr>
              ) : (
                history.map((h) => (
                  <tr key={h.id}>
                    <td>
                      <span
                        className={`badge ${
                          h.status === 'scheduled'
                            ? 'badge-scheduled'
                            : h.status === 'skipped_conflict'
                              ? 'badge-failed'
                              : 'badge-cancelled'
                        }`}
                      >
                        {h.status === 'skipped_conflict' ? 'Skipped' : h.status}
                      </span>
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {new Date(h.start_time).toLocaleString([], {
                        weekday: 'short',
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{h.program_title}</div>
                      <div className="muted" style={{ fontSize: '0.74rem' }}>
                        Rule: {h.title_pattern}
                      </div>
                    </td>
                    <td>{h.channel_name}</td>
                    <td className="muted" style={{ fontSize: '0.78rem' }}>
                      {h.reason || 'Scheduled in UHF Server'}
                    </td>
                    <td>
                      {h.status === 'skipped_conflict' && (
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => handleForceSchedule(h)}
                          disabled={busy === `force-${h.id}`}
                        >
                          {busy === `force-${h.id}` ? 'Scheduling...' : 'Force Schedule'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
