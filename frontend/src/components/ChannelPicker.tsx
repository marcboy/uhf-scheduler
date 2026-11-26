import React, { useEffect, useState } from 'react';
import { api, Category, Channel } from '../api/client';

interface Props {
  selectedChannel: Channel | null;
  onSelect: (ch: Channel) => void;
}

const MANUAL_CAT_STORAGE_KEY = 'uhf_scheduler.manual_category_id';
const MANUAL_FAV_STORAGE_KEY = 'uhf_scheduler.manual_favorites_only';

export const ChannelPicker: React.FC<Props> = ({ selectedChannel, onSelect }) => {
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryId, setCategoryId] = useState<string>(() => {
    try {
      return localStorage.getItem(MANUAL_CAT_STORAGE_KEY) || 'all';
    } catch {
      return 'all';
    }
  });
  const [favoritesOnly, setFavoritesOnly] = useState<boolean>(() => {
    try {
      return localStorage.getItem(MANUAL_FAV_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const [query, setQuery] = useState('');
  const [channels, setChannels] = useState<Channel[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.listCategories(false).then(setCategories).catch(() => {});
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .listChannels({
          q: query.trim() || undefined,
          category_id: categoryId !== 'all' ? Number(categoryId) : undefined,
          favorites_only: favoritesOnly,
          limit: 120,
        })
        .then((res) => {
          setChannels(res.items);
          setTotal(res.total);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }, 180);
    return () => clearTimeout(timer);
  }, [query, categoryId, favoritesOnly]);

  const toggleFav = async (e: React.MouseEvent, ch: Channel) => {
    e.stopPropagation();
    const next = !ch.is_favorite;
    await api.setChannelFavorite(ch.id, next);
    setChannels((prev) =>
      prev.map((c) => (c.id === ch.id ? { ...c, is_favorite: next } : c))
    );
    if (selectedChannel?.id === ch.id) {
      onSelect({ ...ch, is_favorite: next });
    }
  };

  return (
    <div>
      <div className="row" style={{ marginBottom: '0.6rem' }}>
        <input
          type="text"
          placeholder="Search channels (e.g. Canal+, ESPN, BBC)..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 2, minWidth: 180 }}
        />
        <select
          value={categoryId}
          onChange={(e) => {
            setCategoryId(e.target.value);
            try {
              localStorage.setItem(MANUAL_CAT_STORAGE_KEY, e.target.value);
            } catch {
              // ignore storage errors
            }
          }}
          style={{ flex: 1, minWidth: 150 }}
        >
          <option value="all">All Visible Categories</option>
          {categories.map((cat) => (
            <option key={cat.id} value={String(cat.id)}>
              {cat.name} ({cat.channel_count})
            </option>
          ))}
        </select>
        <button
          type="button"
          className={`btn ${favoritesOnly ? 'btn-primary' : ''}`}
          onClick={() =>
            setFavoritesOnly((v) => {
              const next = !v;
              try {
                localStorage.setItem(MANUAL_FAV_STORAGE_KEY, String(next));
              } catch {
                // ignore storage errors
              }
              return next;
            })
          }
        >
          {favoritesOnly ? '★ Favorites' : '☆ Favorites'}
        </button>
      </div>

      <div className="muted" style={{ marginBottom: '0.4rem' }}>
        {loading
          ? 'Searching channels...'
          : `Showing ${channels.length} of ${total} channels`}
      </div>

      <div className="channel-list">
        {channels.length === 0 && !loading ? (
          <div style={{ padding: '1rem', color: 'var(--muted)' }}>
            No channels found. Add and sync an IPTV source in Settings first.
          </div>
        ) : (
          channels.map((ch) => (
            <div
              key={ch.id}
              className={`channel-item ${selectedChannel?.id === ch.id ? 'selected' : ''}`}
              onClick={() => onSelect(ch)}
            >
              <div className="row" style={{ gap: '0.55rem', flex: 1, minWidth: 0 }}>
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
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{ch.name}</div>
                  <div className="muted" style={{ fontSize: '0.76rem' }}>
                    {ch.category_name || 'Uncategorized'} · {ch.source_name}
                  </div>
                </div>
              </div>
              <button
                type="button"
                className={`star-btn ${ch.is_favorite ? 'fav' : ''}`}
                title={ch.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
                onClick={(e) => toggleFav(e, ch)}
              >
                {ch.is_favorite ? '★' : '☆'}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
