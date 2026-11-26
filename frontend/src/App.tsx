import React, { useEffect, useState } from 'react';
import { api, UpdateCheckResult } from './api/client';
import { ManualSchedulePage } from './pages/ManualSchedulePage';
import { EpgGridPage } from './pages/EpgGridPage';
import { AutoRecordPage } from './pages/AutoRecordPage';
import { RecordingsPage } from './pages/RecordingsPage';
import { SettingsPage } from './pages/SettingsPage';
import { APP_VERSION, BUILD_TIMESTAMP } from './version';

type Tab = 'manual' | 'epg' | 'rules' | 'recordings' | 'settings';

export const App: React.FC = () => {
  const [tab, setTab] = useState<Tab>('manual');
  const [updateInfo, setUpdateInfo] = useState<UpdateCheckResult | null>(null);

  useEffect(() => {
    api.checkForUpdates(false).then(setUpdateInfo).catch(() => {});
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-dot" />
          <span>UHF Scheduler</span>
          <span className="badge badge-scheduled" style={{ fontSize: '0.7rem', textTransform: 'none' }}>
            v{APP_VERSION}
          </span>
          {updateInfo?.update_available && updateInfo.latest_version && (
            <button
              type="button"
              className="badge badge-completed"
              style={{
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.7rem',
                textTransform: 'none',
              }}
              onClick={() => setTab('settings')}
              title={`Update available: v${updateInfo.latest_version}. Click for update command.`}
            >
              Update v{updateInfo.latest_version} ↑
            </button>
          )}
        </div>
        <nav className="nav-tabs" aria-label="Main navigation">
          <button
            type="button"
            className={`nav-tab ${tab === 'manual' ? 'active' : ''}`}
            onClick={() => setTab('manual')}
          >
            Manual Schedule
          </button>
          <button
            type="button"
            className={`nav-tab ${tab === 'epg' ? 'active' : ''}`}
            onClick={() => setTab('epg')}
          >
            EPG Grid
          </button>
          <button
            type="button"
            className={`nav-tab ${tab === 'rules' ? 'active' : ''}`}
            onClick={() => setTab('rules')}
          >
            Auto-Record
          </button>
          <button
            type="button"
            className={`nav-tab ${tab === 'recordings' ? 'active' : ''}`}
            onClick={() => setTab('recordings')}
          >
            Recordings
          </button>
          <button
            type="button"
            className={`nav-tab ${tab === 'settings' ? 'active' : ''}`}
            onClick={() => setTab('settings')}
          >
            Settings
          </button>
        </nav>
      </header>

      <main className="main-content">
        {tab === 'manual' && <ManualSchedulePage />}
        {tab === 'epg' && <EpgGridPage onGoToSettings={() => setTab('settings')} />}
        {tab === 'rules' && <AutoRecordPage />}
        {tab === 'recordings' && <RecordingsPage />}
        {tab === 'settings' && <SettingsPage />}
      </main>

      <footer className="footer">
        <span>UHF Scheduler · a 6-kids effort</span>
        <span>
          v{APP_VERSION} · Built {BUILD_TIMESTAMP}
        </span>
      </footer>
    </div>
  );
};
