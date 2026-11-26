import React from 'react';
import { OverlapResult } from '../api/client';

interface Props {
  overlap: OverlapResult | null;
}

export const OverlapWarning: React.FC<Props> = ({ overlap }) => {
  if (!overlap || overlap.conflicts.length === 0) return null;

  const severe = overlap.has_conflict;
  return (
    <div className={`alert ${severe ? 'alert-warn' : 'alert-info'}`}>
      <strong>
        {severe
          ? `Stream limit warning: ${overlap.peak_concurrent} concurrent recordings (limit: ${overlap.max_simultaneous_streams})`
          : `Note: Overlaps ${overlap.conflicts.length} existing timer(s) (within your ${overlap.max_simultaneous_streams}-stream limit)`}
      </strong>
      <ul style={{ margin: '0.4rem 0 0 1.2rem', padding: 0 }}>
        {overlap.conflicts.map((c) => (
          <li key={c.id}>
            <strong>{c.name}</strong> ({c.channel || 'Channel'}) — overlaps by{' '}
            {c.overlap_minutes} min ({new Date(c.start_time).toLocaleString()} –{' '}
            {new Date(c.end_time).toLocaleTimeString()})
          </li>
        ))}
      </ul>
    </div>
  );
};
