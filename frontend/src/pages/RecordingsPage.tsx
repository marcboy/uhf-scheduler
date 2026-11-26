import React, { useCallback, useEffect, useState } from 'react';
import { api, Recording } from '../api/client';
import { ScheduleModal, ScheduleModalInitial } from '../components/ScheduleModal';

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export const RecordingsPage: React.FC = () => {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [uhfUrl, setUhfUrl] = useState('');
  const [filter, setFilter] = useState<'active' | 'all' | 'completed' | 'failed'>('active');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<ScheduleModalInitial | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .listRecordings()
      .then((res) => {
        setRecordings(res.items);
        setUhfUrl(res.uhf_server_url);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load recordings'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const handleStopOrCancel = async (rec: Recording) => {
    const isSeries = rec.status === 'scheduled' && rec.recurrence_days && rec.recurrence_days.length > 0;
    const msg =
      rec.status === 'recording'
        ? `Stop active recording "${rec.name}"?`
        : isSeries
          ? `Stop the entire recurring series "${rec.name}"?`
          : `Delete "${rec.name}"?`;
    if (!window.confirm(msg)) return;
    setNotice(null);
    setError(null);
    try {
      const res = await api.stopRecording(rec.id);
      setNotice(`Done (${res.action.replace(/_/g, ' ')}): "${rec.name}"`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    }
  };

  const openEdit = (rec: Recording) => {
    setEditTarget({
      mode: 'edit',
      recordingId: rec.id,
      channelId: rec.channel_id ?? null,
      channelName: rec.description || String((rec.metadata as { name?: string })?.name || 'Channel'),
      title: rec.name,
      startIso: rec.start_time,
      durationMin: Math.max(1, Math.round(rec.duration_seconds / 60)),
      prePaddingMin: 0,
      postPaddingMin: 0,
      recurrenceDays: rec.recurrence_days || null,
      recurrenceEndDate: rec.recurrence_end_date || null,
    });
  };

  const filtered = recordings
    .filter((r) => {
      const st = (r.status || '').toLowerCase();
      if (filter === 'active') return st === 'scheduled' || st === 'recording';
      if (filter === 'completed') return st === 'completed';
      if (filter === 'failed') return st === 'failed' || st === 'cancelled';
      return true;
    })
    .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.9rem' }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>Recordings</h2>
          <div className="row">
            {(['active', 'completed', 'failed', 'all'] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`btn btn-sm ${filter === f ? 'btn-primary' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'active'
                  ? 'Scheduled & Live'
                  : f === 'completed'
                    ? 'Completed'
                    : f === 'failed'
                      ? 'Failed / Cancelled'
                      : 'All'}
              </button>
            ))}
          </div>
        </div>
        <div className="row">
          {uhfUrl && (
            <a href={uhfUrl} target="_blank" rel="noreferrer" className="btn btn-sm">
              Open UHF Server UI ↗
            </a>
          )}
          <button type="button" className="btn btn-sm" onClick={load} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-success">{notice}</div>}

      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Title &amp; Channel</th>
              <th>Start (Local)</th>
              <th>Duration</th>
              <th>Repeat</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="muted" style={{ padding: '1rem' }}>
                  No recordings in this view.
                </td>
              </tr>
            ) : (
              filtered.map((rec) => {
                const st = (rec.status || '').toLowerCase();
                const isRecurring = Array.isArray(rec.recurrence_days) && rec.recurrence_days.length > 0;
                const durMin = Math.round((rec.duration_seconds || 0) / 60);
                const capturedMin =
                  rec.recorded_seconds != null ? Math.round(rec.recorded_seconds / 60) : null;
                return (
                  <tr key={rec.id}>
                    <td>
                      <span className={`badge badge-${st}`}>{st}</span>
                      {st === 'recording' && capturedMin != null && (
                        <div className="muted" style={{ fontSize: '0.74rem', marginTop: 3 }}>
                          {capturedMin} / {durMin} min
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{rec.name}</div>
                      <div className="muted" style={{ fontSize: '0.78rem' }}>
                        {rec.description || String((rec.metadata as { name?: string })?.name || '')}
                      </div>
                      {(rec.error_code || rec.error) && (
                        <div style={{ color: '#fca5a5', fontSize: '0.76rem', marginTop: 2 }}>
                          {rec.error_code ? `[${rec.error_code}] ` : ''}
                          {rec.error || ''}
                        </div>
                      )}
                    </td>
                    <td>{new Date(rec.start_time).toLocaleString()}</td>
                    <td>{durMin} min</td>
                    <td>
                      {isRecurring ? (
                        <span className="muted">
                          {rec.recurrence_days!.map((d) => DAY_NAMES[d] ?? d).join(', ')}
                          {rec.timezone ? ` (${rec.timezone})` : ''}
                        </span>
                      ) : (
                        <span className="muted">One-off</span>
                      )}
                    </td>
                    <td>
                      <div className="row" style={{ gap: '0.35rem' }}>
                        {st === 'scheduled' && (
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => openEdit(rec)}
                          >
                            Edit
                          </button>
                        )}
                        {st === 'completed' && rec.uhf_web_url && (
                          <a
                            href={rec.uhf_web_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-sm"
                          >
                            Watch ↗
                          </a>
                        )}
                        {st === 'completed' && rec.download_url && (
                          <a
                            href={rec.download_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-sm"
                          >
                            Download
                          </a>
                        )}
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => handleStopOrCancel(rec)}
                        >
                          {st === 'recording'
                            ? 'Stop'
                            : st === 'scheduled' && isRecurring
                              ? 'Stop Series'
                              : 'Delete'}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {editTarget && (
        <ScheduleModal
          initial={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={(saved) => {
            setEditTarget(null);
            setNotice(
              saved.warning
                ? `Updated with warning: ${saved.warning}`
                : `Updated timer "${saved.name}".`
            );
            load();
          }}
        />
      )}
    </div>
  );
};
