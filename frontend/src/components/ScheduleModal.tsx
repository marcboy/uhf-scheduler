import React, { useEffect, useMemo, useState } from 'react';
import { api, OverlapResult, Recording } from '../api/client';
import { OverlapWarning } from './OverlapWarning';

const WEEKDAYS = [
  { idx: 0, label: 'Mon' },
  { idx: 1, label: 'Tue' },
  { idx: 2, label: 'Wed' },
  { idx: 3, label: 'Thu' },
  { idx: 4, label: 'Fri' },
  { idx: 5, label: 'Sat' },
  { idx: 6, label: 'Sun' },
];

export interface ScheduleModalInitial {
  mode: 'create' | 'edit';
  recordingId?: string;
  channelId?: number | null;
  channelName: string;
  title: string;
  startIso: string;
  durationMin: number;
  prePaddingMin: number;
  postPaddingMin: number;
  recurrenceDays?: number[] | null;
  recurrenceEndDate?: string | null;
}

interface Props {
  initial: ScheduleModalInitial;
  onClose: () => void;
  onSaved: (rec: Recording) => void;
}

function toLocalInputValue(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

export const ScheduleModal: React.FC<Props> = ({ initial, onClose, onSaved }) => {
  const browserTz = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    []
  );

  const [title, setTitle] = useState(initial.title);
  const [startLocal, setStartLocal] = useState(() => toLocalInputValue(initial.startIso));
  const [durationMin, setDurationMin] = useState(initial.durationMin);
  const [prePad, setPrePad] = useState(initial.prePaddingMin);
  const [postPad, setPostPad] = useState(initial.postPaddingMin);
  const [recDays, setRecDays] = useState<number[]>(initial.recurrenceDays || []);
  const [recEndDate, setRecEndDate] = useState(
    initial.recurrenceEndDate ? initial.recurrenceEndDate.slice(0, 10) : ''
  );
  const [overlap, setOverlap] = useState<OverlapResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ruleNotice, setRuleNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [creatingRule, setCreatingRule] = useState(false);

  const startIsoUtc = useMemo(() => {
    if (!startLocal) return null;
    const d = new Date(startLocal);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }, [startLocal]);

  useEffect(() => {
    if (!startIsoUtc || durationMin <= 0) {
      setOverlap(null);
      return;
    }
    const timer = setTimeout(() => {
      api
        .checkOverlap({
          start_time: startIsoUtc,
          duration_seconds: durationMin * 60,
          pre_padding_min: prePad,
          post_padding_min: postPad,
          channel_id: initial.channelId ?? undefined,
          exclude_recording_id: initial.recordingId,
        })
        .then(setOverlap)
        .catch(() => setOverlap(null));
    }, 250);
    return () => clearTimeout(timer);
  }, [startIsoUtc, durationMin, prePad, postPad, initial.channelId, initial.recordingId]);

  const toggleDay = (idx: number) => {
    setRecDays((prev) =>
      prev.includes(idx) ? prev.filter((d) => d !== idx) : [...prev, idx].sort()
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!startIsoUtc) {
      setError('Please enter a valid start date and time.');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = {
        name: title.trim() || undefined,
        start_time: startIsoUtc,
        duration_seconds: Math.round(durationMin * 60),
        pre_padding_min: prePad,
        post_padding_min: postPad,
        recurrence_days: recDays.length > 0 ? recDays : null,
        recurrence_end_date: recDays.length > 0 && recEndDate ? `${recEndDate}T23:59:59Z` : null,
        timezone: browserTz,
      };
      let saved: Recording;
      if (initial.mode === 'edit' && initial.recordingId) {
        if (initial.channelId) payload.channel_id = initial.channelId;
        saved = await api.editRecording(initial.recordingId, payload);
      } else {
        payload.channel_id = initial.channelId;
        saved = await api.createRecording(payload);
      }
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save recording');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCreateAutoRule = async () => {
    if (!initial.channelId) return;
    const pat = title.trim();
    if (!pat) {
      setError('Enter a program title before creating an auto-record rule.');
      return;
    }
    setError(null);
    setRuleNotice(null);
    setCreatingRule(true);
    try {
      const created = await api.createRule({
        channel_id: initial.channelId,
        title_pattern: pat,
        match_mode: 'starts_with',
        pre_padding_min: prePad,
        post_padding_min: postPad,
        skip_if_conflict: true,
        enabled: true,
        run_now: true,
      });
      const rs = created.run_summary;
      setRuleNotice(
        `Auto-record rule created for "${pat}" on ${initial.channelName}! ` +
          (rs ? `Scheduled ${rs.scheduled}, skipped ${rs.skipped_conflict} conflict(s).` : '')
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create auto-record rule');
    } finally {
      setCreatingRule(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '0.8rem' }}>
          <h3 style={{ margin: 0 }}>
            {initial.mode === 'edit' ? 'Edit Scheduled Recording' : 'Schedule Recording'}
          </h3>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="muted" style={{ marginBottom: '0.8rem' }}>
          Channel: <strong style={{ color: 'var(--text)' }}>{initial.channelName}</strong>
        </div>

        {error && <div className="alert alert-error">{error}</div>}
        {ruleNotice && <div className="alert alert-success">{ruleNotice}</div>}
        <OverlapWarning overlap={overlap} />

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label>Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Optional custom recording name"
            />
          </div>

          <div className="row">
            <div className="field">
              <label>Start Date &amp; Time (Local)</label>
              <input
                type="datetime-local"
                value={startLocal}
                onChange={(e) => setStartLocal(e.target.value)}
                required
              />
            </div>
            <div className="field" style={{ maxWidth: 160 }}>
              <label>Duration (min)</label>
              <input
                type="number"
                min={1}
                max={1440}
                value={durationMin}
                onChange={(e) => setDurationMin(Number(e.target.value))}
                required
              />
            </div>
          </div>

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

          <div className="field">
            <label>Weekly Repeat (optional · timezone: {browserTz})</label>
            <div className="weekday-pills">
              {WEEKDAYS.map((d) => (
                <button
                  key={d.idx}
                  type="button"
                  className={`day-pill ${recDays.includes(d.idx) ? 'active' : ''}`}
                  onClick={() => toggleDay(d.idx)}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          {recDays.length > 0 && (
            <div className="field">
              <label>Repeat End Date (optional)</label>
              <input
                type="date"
                value={recEndDate}
                onChange={(e) => setRecEndDate(e.target.value)}
              />
            </div>
          )}

          <div className="row" style={{ justifyContent: 'space-between', marginTop: '1rem' }}>
            <div>
              {initial.mode === 'create' && initial.channelId != null && (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={handleCreateAutoRule}
                  disabled={creatingRule || submitting || !title.trim()}
                  title="Create an automatic series rule (Do not schedule on top of existing enabled by default)"
                >
                  {creatingRule
                    ? 'Creating Rule...'
                    : 'Auto-record every airing on this channel'}
                </button>
              )}
            </div>
            <div className="row">
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={submitting || creatingRule}>
                {submitting
                  ? 'Saving...'
                  : initial.mode === 'edit'
                    ? 'Update Timer'
                    : 'Schedule Recording'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
