import { useState, useEffect, useRef } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { SettingsNav } from './SettingsNav';
import { GeneralSettings } from './GeneralSettings';
import { CharacterSettings } from './CharacterSettings';
import { UserProfileSettings } from './UserProfileSettings';
import { ModelSettings } from './ModelSettings';
import { ProactiveSettings } from './ProactiveSettings';
import { VoiceSettings } from './VoiceSettings';
import { MemoryPanel } from './MemoryPanel';

type NavKey = 'general' | 'character' | 'profile' | 'model' | 'proactive' | 'voice' | 'memory';

export function SettingsPanel() {
  const showSettings = useAppStore((s) => s.showSettings);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setConfig = useAppStore((s) => s.setConfig);
  const setShowMemoryViewer = useChatStore((s) => s.setShowMemoryViewer);
  const { t } = useTranslation();

  const [activeNav, setActiveNav] = useState<NavKey>('general');
  const [healthStatus, setHealthStatus] = useState<string>('');
  const [testing, setTesting] = useState(false);

  const hasFetched = useRef(false);

  useEffect(() => {
    if (showSettings && !hasFetched.current) {
      hasFetched.current = true;
      api.healthCheck()
        .then(() => setHealthStatus(t('Connected')))
        .catch(() => setHealthStatus(t('Offline')));
      api.fetchConfig().then((c) => setConfig(c));
    }
    if (!showSettings) {
      hasFetched.current = false;
    }
  }, [showSettings]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleTestConnection = async () => {
    setTesting(true);
    try {
      await api.healthCheck();
      setHealthStatus(t('Connected'));
    } catch {
      setHealthStatus(t('Offline'));
    }
    setTesting(false);
  };

  if (!showSettings) return null;

  const renderContent = () => {
    switch (activeNav) {
      case 'general':   return <GeneralSettings />;
      case 'character': return <CharacterSettings />;
      case 'profile':   return <UserProfileSettings />;
      case 'model':     return <ModelSettings />;
      case 'proactive': return <ProactiveSettings />;
      case 'voice':     return <VoiceSettings />;
      case 'memory':    return <MemoryPanel />;
    }
  };

  return (
    <div
      className="no-drag fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={() => setShowSettings(false)}
    >
      <div
        className="max-h-[88vh] w-[520px] flex overflow-hidden rounded-2xl border border-white/10 bg-companion-overlay-strong shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t('Settings')}
      >
        <SettingsNav active={activeNav} onChange={setActiveNav} />

        <div className="flex-1 flex flex-col min-w-0">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 flex-shrink-0">
            <h2 className="text-sm font-semibold text-white">{t('Settings')}</h2>
            <button
              onClick={handleTestConnection}
              className="text-xs text-white/50 transition-colors hover:text-white/90"
            >
              {testing ? t('Checking') : healthStatus || t('Check connection')}
            </button>
          </div>

          {/* Content — fixed height, matches largest tab (Proactive) */}
          <div className="overflow-y-auto px-4 py-3 h-[480px]">
            {renderContent()}
          </div>
        </div>
      </div>
    </div>
  );
}
