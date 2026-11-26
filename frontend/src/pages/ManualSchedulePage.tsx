import React, { useEffect, useMemo, useState } from 'react';
import { api, AppSettings, Channel, OverlapResult } from '../api/client';
import { ChannelPicker } from '../components/ChannelPicker';
import { OverlapWarning } from '../components/OverlapWarning';

const WEEKDAYS = [
  { idx: 0, label: 'Mon' },
  { idx: 1, label: 'Tue' },
  { idx: 2, label: 'Wed' },
  { idx: 3, label: 'Thu' },
  { idx: 4, label: 'Fri' },
  { idx: 5, label: 'Sat' },
  { idx: 6, label: 'Sun' },
];

function defaultDateAndTime(): { date: string; time: string } {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
  };
}

export const ManualSchedulePage: React.FC<{ onScheduled?: () => void }> = ({ onScheduled }) => {
  const browserTz = useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    []
  );
  const initDT = useMemo(defaultDateAndTime, []);

  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [title, setTitle] = useState('');
  const [date, setDate] = useState(initDT.date);
  const [startTime, setStartTime] = useState(initDT.time);
  const [lengthMode, setLengthMode] = useState<'duration' | 'end_time'>('duration');
  const [durationMin, setDurationMin] = useState(60);
  const [endTime, setEndTime] = useState('22:00');
  const [prePad, setPrePad] = useState(2);
  const [postPad, setPostPad] = useState(5);
  const [recDays, setRecDays] = useState<number[]>([]);
  const [recEndDate, setRecEndDate] = useState('');

  const [overlap, setOverlap] = useState<OverlapResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        setSettings(s);
        setPrePad(s.defaults.pre_padding_min);
        setPostPad(s.defaults.post_padding_min);
      })
      .catch(() => {});
  }, []);

  const computedRange = useMemo(() => {
    if (!date || !startTime) return null;
    const startDt = new Date(`${date}T${startTime}:00`);
    if (Number.isNaN(startDt.getTime())) return null;

    if (lengthMode === 'duration') {
      if (durationMin <= 0) return null;
      return {
        startIso: startDt.toISOString(),
        durationSec: Math.round(durationMin * 60),
        endIso: undefined as string | undefined,
        displayDurationMin: durationMin,
      };
    }

    if (!endTime) return null;
    let endDt = new Date(`${date}T${endTime}:00`);
    if (Number.isNaN(endDt.getTime())) return null;
    if (endDt <= startDt) {
      endDt = new Date(endDt.getTime() + 24 * 3600 * 1000);
    }
    const diffMin = Math.round((endDt.getTime() - startDt.getTime()) / 60000);
    return {
      startIso: startDt.toISOString(),
      durationSec: undefined as number | undefined,
      endIso: endDt.toISOString(),
      displayDurationMin: diffMin,
    };
  }, [date, startTime, lengthMode, durationMin, endTime]);

  useEffect(() => {
    if (!computedRange) {
      setOverlap(null);
      return;
    }
    const timer = setTimeout(() => {
      api
        .checkOverlap({
          start_time: computedRange.startIso,
          duration_seconds: computedRange.durationSec,
          end_time: computedRange.endIso,
          pre_padding_min: prePad,
          post_padding_min: postPad,
          channel_id: selectedChannel?.id,
        })
        .then(setOverlap)
        .catch(() => setOverlap(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [computedRange, prePad, postPad, selectedChannel?.id]);

  const toggleDay = (idx: number) => {
    setRecDays((prev) =>
      prev.includes(idx) ? prev.filter((d) => d !== idx) : [...prev, idx].sort()
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);
    if (!selectedChannel) {
      setError('Please select a channel first.');
      return;
    }
    if (!computedRange) {
      setError('Please provide a valid date, start time, and duration/end time.');
      return;
    }

    setSubmitting(true);
    try {
      const created = await api.createRecording({
        channel_id: selectedChannel.id,
        name: title.trim() || undefined,
        start_time: computedRange.startIso,
        duration_seconds: computedRange.durationSec,
        end_time: computedRange.endIso,
        pre_padding_min: prePad,
        post_padding_min: postPad,
        recurrence_days: recDays.length > 0 ? recDays : null,
        recurrence_end_date: recDays.length > 0 && recEndDate ? `${recEndDate}T23:59:59Z` : null,
        timezone: browserTz,
      });
      setSuccessMsg(
        `Scheduled "${created.name}" on ${selectedChannel.name} starting ${new Date(
          created.start_time
        ).toLocaleString()} (${Math.round(created.duration_seconds / 60)} min).`
      );
      setTitle('');
      onScheduled?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to schedule recording.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="grid-2">
      <div className="card">
        <h2>1. Pick Channel</h2>
        <ChannelPicker selectedChannel={selectedChannel} onSelect={setSelectedChannel} />
      </div>

      <div className="card">
        <h2>2. Schedule Any Future Date &amp; Time</h2>
        {selectedChannel ? (
          <div className="alert alert-info" style={{ marginBottom: '0.8rem' }}>
            Selected: <strong>{selectedChannel.name}</strong>{' '}
            <span className="muted">({selectedChannel.category_name || 'Uncategorized'})</span>
          </div>
        ) : (
          <div className="muted" style={{ marginBottom: '0.8rem' }}>
            Choose a channel on the left to schedule a recording with no 24/48h guide limit.
          </div>
        )}

        {error && <div className="alert alert-error">{error}</div>}
        {successMsg && <div className="alert alert-success">{successMsg}</div>}
        <OverlapWarning overlap={overlap} />

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label>Recording Title (optional — auto-generated if blank)</label>
            <input
              type="text"
              placeholder="e.g. Sunday Night Match"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="row">
            <div className="field">
              <label>Date (any future date)</label>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label>Start Time (Local)</label>
              <input
                type="time"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="row" style={{ marginBottom: '0.5rem' }}>
            <button
              type="button"
              className={`btn btn-sm ${lengthMode === 'duration' ? 'btn-primary' : ''}`}
              onClick={() => setLengthMode('duration')}
            >
              By Duration
            </button>
            <button
              type="button"
              className={`btn btn-sm ${lengthMode === 'end_time' ? 'btn-primary' : ''}`}
              onClick={() => setLengthMode('end_time')}
            >
              By End Time
            </button>
            {computedRange && (
              <span className="muted">
                Length: {computedRange.displayDurationMin} min (+{prePad + postPad}m padding)
              </span>
            )}
          </div>

          {lengthMode === 'duration' ? (
            <div className="row">
              <div className="field">
                <label>Duration (minutes)</label>
                <input
                  type="number"
                  min={1}
                  max={1440}
                  value={durationMin}
                  onChange={(e) => setDurationMin(Number(e.target.value))}
                  required
                />
              </div>
              <div className="row" style={{ paddingTop: '1.1rem' }}>
                {[30, 60, 90, 120, 180].map((m) => (
                  <button
                    key={m}
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setDurationMin(m)}
                  >
                    {m}m
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="field">
              <label>End Time (automatically wraps past midnight if earlier than start)</label>
              <input
                type="time"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                required
              />
            </div>
          )}

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
            <label>Repeat Weekly (optional · timezone: {browserTz})</label>
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
              <label>Stop Repeating After (optional)</label>
              <input
                type="date"
                value={recEndDate}
                onChange={(e) => setRecEndDate(e.target.value)}
              />
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || !selectedChannel}
            style={{ marginTop: '0.5rem', width: '100%', justifyContent: 'center' }}
          >
            {submitting ? 'Sending to UHF Server...' : 'Schedule Recording'}
          </button>

          {settings && (
            <div className="muted" style={{ marginTop: '0.5rem', textAlign: 'center' }}>
              Target UHF Server: {settings.uhf_server.url || '(not configured)'}
            </div>
          )}
        </form>
      </div>
    </div>
  );
};
