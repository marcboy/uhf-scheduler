export interface AppSettings {
  uhf_server: {
    url: string;
    password_configured: boolean;
    token_cached: boolean;
    device_id: string;
  };
  defaults: {
    pre_padding_min: number;
    post_padding_min: number;
    max_simultaneous_streams: number;
    sync_interval_hours: number;
    epg_retention_hours: number;
  };
}

export interface IPTVSource {
  id: number;
  name: string;
  type: 'xtream' | 'm3u';
  url: string;
  username: string | null;
  password_configured: boolean;
  playlist_id: string | null;
  auto_epg_enabled: boolean;
  enabled: boolean;
  max_streams: number | null;
  last_synced_at: string | null;
  last_error: string | null;
  channel_count: number;
}

export interface EPGSource {
  id: number;
  name: string;
  type: 'xtream_auto' | 'xmltv_url';
  iptv_source_id: number | null;
  url: string | null;
  enabled: boolean;
  last_synced_at: string | null;
  last_error: string | null;
  program_count: number;
}

export interface Category {
  id: number;
  source_id: number;
  source_name: string;
  external_id: string;
  name: string;
  hidden: boolean;
  channel_count: number;
}

export interface Channel {
  id: number;
  source_id: number;
  source_name: string;
  category_id: number | null;
  category_name: string | null;
  name: string;
  logo_url: string | null;
  epg_channel_id: string | null;
  epg_override_id: string | null;
  is_favorite: boolean;
}

export interface EPGProgram {
  id: number;
  start_time: string;
  end_time: string;
  start_ts: number;
  end_ts: number;
  title: string;
  description: string | null;
  category: string | null;
  icon_url: string | null;
}

export interface GridRecordingBadge {
  id: string;
  name: string;
  status: string;
  start_time: string;
  end_time: string;
  start_ts: number;
  end_ts: number;
  recurrence_days: number[] | null;
}

export interface EPGGridChannel extends Channel {
  effective_epg_id: string | null;
  programs: EPGProgram[];
  recordings: GridRecordingBadge[];
}

export interface EPGGridResponse {
  start_time: string;
  end_time: string;
  start_ts: number;
  end_ts: number;
  epg_source_count: number;
  total_epg_programs: number;
  total_channels: number;
  uhf_warning: string | null;
  channels: EPGGridChannel[];
}

export interface EPGChannelOption {
  epg_source_id: number;
  epg_source_name: string;
  xmltv_id: string;
  display_name: string;
  icon_url: string | null;
}

export interface EPGProgramSearchChannel {
  channel_id: number;
  channel_name: string;
  category_name: string | null;
  logo_url: string | null;
  is_favorite: boolean;
}

export interface EPGProgramSearchItem {
  id: number;
  title: string;
  description: string | null;
  category: string | null;
  start_time: string;
  end_time: string;
  start_ts: number;
  end_ts: number;
  channel_id: number;
  channel_name: string;
  category_name: string | null;
  logo_url: string | null;
  is_favorite: boolean;
  channels: EPGProgramSearchChannel[];
}

export interface Recording {
  id: string;
  name: string;
  description: string | null;
  start_time: string;
  duration_seconds: number;
  status: string;
  created_at?: string;
  error?: string | null;
  error_code?: string | null;
  recorded_seconds?: number | null;
  recurrence_days?: number[] | null;
  recurrence_end_date?: string | null;
  timezone?: string | null;
  metadata?: Record<string, unknown> | null;
  channel_id?: number | null;
  uhf_web_url?: string;
  download_url?: string;
  stream_url_uhf?: string;
  warning?: string;
}

export interface OverlapResult {
  has_conflict: boolean;
  peak_concurrent: number;
  max_simultaneous_streams: number;
  conflicts: Array<{
    id: string;
    name: string;
    channel: string | null;
    status: string;
    start_time: string;
    end_time: string;
    overlap_minutes: number;
  }>;
}

export interface DiscoveredUHFServer {
  url: string;
  name: string;
  source: 'mdns' | 'local_probe';
  password_required: boolean;
}

export interface UpdateCheckResult {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_url: string;
  release_notes: string | null;
  update_command: string;
  checked_online: boolean;
}

export interface AutoRecordRunSummary {
  evaluated_rules: number;
  scheduled: number;
  skipped_conflict: number;
  errors: number;
  uhf_error: string | null;
}

export interface AutoRecordRule {
  id: number;
  channel_id: number;
  channel_name: string;
  logo_url: string | null;
  category_name: string | null;
  source_name: string;
  title_pattern: string;
  match_mode: 'starts_with' | 'contains' | 'exact';
  pre_padding_min: number | null;
  post_padding_min: number | null;
  skip_if_conflict: boolean;
  enabled: boolean;
  last_run_at: string | null;
  last_scheduled_count: number;
  last_skipped_count: number;
  total_scheduled: number;
  total_skipped: number;
  run_summary?: AutoRecordRunSummary;
}

export interface AutoRecordHistoryItem {
  id: number;
  rule_id: number;
  channel_id: number;
  channel_name: string;
  logo_url: string | null;
  title_pattern: string;
  program_title: string;
  program_start_ts: number;
  program_end_ts: number;
  start_time: string;
  end_time: string;
  status: 'scheduled' | 'skipped_conflict' | 'error';
  uhf_recording_id: string | null;
  reason: string | null;
  created_at: string;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!resp.ok) {
    let message = `Request failed (${resp.status})`;
    try {
      const err = await resp.json();
      if (err && typeof err.detail === 'string') {
        message = err.detail;
      } else if (err && Array.isArray(err.detail)) {
        message = err.detail.map((e: { msg?: string }) => e.msg || '').join('; ');
      }
    } catch {
      // ignore parse errors
    }
    throw new Error(message);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  // Settings
  getSettings: () => apiFetch<AppSettings>('/api/settings'),
  updateSettings: (payload: Record<string, unknown>) =>
    apiFetch<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  testUhf: (payload?: { url?: string; password?: string }) =>
    apiFetch<{ ok: boolean; url: string; password_required: boolean; recording_count: number }>(
      '/api/settings/test-uhf',
      { method: 'POST', body: JSON.stringify(payload || {}) }
    ),
  detectPlaylistIds: (host?: string) =>
    apiFetch<{ detected: Record<string, string[]> }>('/api/settings/detect-playlist-id', {
      method: 'POST',
      body: JSON.stringify({ host: host || null }),
    }),
  discoverUhfServers: () =>
    apiFetch<{ servers: DiscoveredUHFServer[] }>('/api/settings/discover-uhf', {
      method: 'POST',
    }),
  checkForUpdates: (force = false) =>
    apiFetch<UpdateCheckResult>(`/api/system/update-check${force ? '?force=true' : ''}`),

  // IPTV Sources
  listIptvSources: () => apiFetch<IPTVSource[]>('/api/sources/iptv'),
  createIptvSource: (payload: Record<string, unknown>) =>
    apiFetch<IPTVSource>('/api/sources/iptv', { method: 'POST', body: JSON.stringify(payload) }),
  updateIptvSource: (id: number, payload: Record<string, unknown>) =>
    apiFetch<IPTVSource>(`/api/sources/iptv/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteIptvSource: (id: number) =>
    apiFetch<{ deleted: boolean }>(`/api/sources/iptv/${id}`, { method: 'DELETE' }),
  syncIptvSource: (id: number) =>
    apiFetch<{ ok: boolean; channel_count: number; error?: string }>(`/api/sources/iptv/${id}/sync`, {
      method: 'POST',
    }),

  // EPG Sources
  listEpgSources: () => apiFetch<EPGSource[]>('/api/sources/epg'),
  createEpgSource: (payload: { name: string; url: string; enabled?: boolean }) =>
    apiFetch<EPGSource>('/api/sources/epg', { method: 'POST', body: JSON.stringify(payload) }),
  deleteEpgSource: (id: number) =>
    apiFetch<{ deleted: boolean }>(`/api/sources/epg/${id}`, { method: 'DELETE' }),
  syncEpgSource: (id: number) =>
    apiFetch<{ ok: boolean; program_count: number; error?: string }>(`/api/sources/epg/${id}/sync`, {
      method: 'POST',
    }),

  // Categories & Channels
  listCategories: (includeHidden = false) =>
    apiFetch<Category[]>(`/api/categories?include_hidden=${includeHidden ? 'true' : 'false'}`),
  setCategoryHidden: (id: number, hidden: boolean) =>
    apiFetch<Category>(`/api/categories/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ hidden }),
    }),
  listChannels: (params: {
    q?: string;
    category_id?: number | null;
    favorites_only?: boolean;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set('q', params.q);
    if (params.category_id != null) qs.set('category_id', String(params.category_id));
    if (params.favorites_only) qs.set('favorites_only', 'true');
    if (params.limit != null) qs.set('limit', String(params.limit));
    if (params.offset != null) qs.set('offset', String(params.offset));
    return apiFetch<{ total: number; items: Channel[] }>(`/api/channels?${qs.toString()}`);
  },
  setChannelFavorite: (id: number, is_favorite: boolean) =>
    apiFetch<{ id: number; is_favorite: boolean }>(`/api/channels/${id}/favorite`, {
      method: 'PATCH',
      body: JSON.stringify({ is_favorite }),
    }),
  setChannelEpgOverride: (id: number, epg_override_id: string | null) =>
    apiFetch<{ id: number; epg_override_id: string | null }>(`/api/channels/${id}/epg-override`, {
      method: 'PATCH',
      body: JSON.stringify({ epg_override_id }),
    }),

  // EPG Grid
  getEpgGrid: (params: {
    category_id?: string | null;
    q?: string;
    start: string;
    end: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams({ start: params.start, end: params.end });
    if (params.category_id) qs.set('category_id', params.category_id);
    if (params.q) qs.set('q', params.q);
    if (params.limit != null) qs.set('limit', String(params.limit));
    if (params.offset != null) qs.set('offset', String(params.offset));
    return apiFetch<EPGGridResponse>(`/api/epg/grid?${qs.toString()}`);
  },
  searchEpgChannels: (q: string) =>
    apiFetch<EPGChannelOption[]>(`/api/epg/channels?q=${encodeURIComponent(q)}&limit=50`),
  searchEpgPrograms: (params: {
    q?: string;
    category_id?: string | null;
    channel_id?: number | null;
    upcoming_only?: boolean;
    search_descriptions?: boolean;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.q != null && params.q !== '') qs.set('q', params.q);
    if (params.channel_id != null) qs.set('channel_id', String(params.channel_id));
    if (params.category_id && params.category_id !== 'all') qs.set('category_id', params.category_id);
    if (params.upcoming_only === false) qs.set('upcoming_only', 'false');
    if (params.search_descriptions) qs.set('search_descriptions', 'true');
    if (params.limit != null) qs.set('limit', String(params.limit));
    return apiFetch<{ query: string; count: number; items: EPGProgramSearchItem[] }>(
      `/api/epg/search?${qs.toString()}`
    );
  },

  // Recordings
  listRecordings: () =>
    apiFetch<{ uhf_server_url: string; items: Recording[] }>('/api/recordings'),
  checkOverlap: (payload: Record<string, unknown>) =>
    apiFetch<OverlapResult>('/api/recordings/check-overlap', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  createRecording: (payload: Record<string, unknown>) =>
    apiFetch<Recording>('/api/recordings', { method: 'POST', body: JSON.stringify(payload) }),
  editRecording: (id: string, payload: Record<string, unknown>) =>
    apiFetch<Recording>(`/api/recordings/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  stopRecording: (id: string) =>
    apiFetch<{ ok: boolean; action: string }>(`/api/recordings/${id}/stop`, { method: 'POST' }),
  deleteRecording: (id: string) =>
    apiFetch<{ ok: boolean; action: string }>(`/api/recordings/${id}`, { method: 'DELETE' }),

  // Auto-Record Rules
  listRules: () => apiFetch<AutoRecordRule[]>('/api/rules'),
  createRule: (payload: {
    channel_id: number;
    title_pattern: string;
    match_mode?: 'starts_with' | 'contains' | 'exact';
    pre_padding_min?: number | null;
    post_padding_min?: number | null;
    skip_if_conflict?: boolean;
    enabled?: boolean;
    run_now?: boolean;
  }) => apiFetch<AutoRecordRule>('/api/rules', { method: 'POST', body: JSON.stringify(payload) }),
  updateRule: (id: number, payload: Record<string, unknown>) =>
    apiFetch<AutoRecordRule>(`/api/rules/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteRule: (id: number) =>
    apiFetch<{ deleted: boolean; id: number }>(`/api/rules/${id}`, { method: 'DELETE' }),
  runAllRules: (sync_epg_first = false) =>
    apiFetch<AutoRecordRunSummary>('/api/rules/run', {
      method: 'POST',
      body: JSON.stringify({ sync_epg_first }),
    }),
  runSingleRule: (id: number) =>
    apiFetch<AutoRecordRunSummary>(`/api/rules/${id}/run`, { method: 'POST' }),
  listRuleHistory: (rule_id?: number | null) => {
    const qs = rule_id != null ? `?rule_id=${rule_id}&limit=100` : '?limit=100';
    return apiFetch<AutoRecordHistoryItem[]>(`/api/rules/history${qs}`);
  },
  forceScheduleHistory: (historyId: number) =>
    apiFetch<{ ok: boolean; recording_id: string; name: string }>(
      `/api/rules/history/${historyId}/force-schedule`,
      { method: 'POST' }
    ),
};
