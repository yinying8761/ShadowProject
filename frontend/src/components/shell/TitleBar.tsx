import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

export function TitleBar() {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setShowHistory = useAppStore((s) => s.setShowHistory);
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const { t } = useTranslation();

  const togglePin = () => {
    const next = !config.alwaysOnTop;
    setConfig({ alwaysOnTop: next });
    window.electronAPI?.setAlwaysOnTop(next);
  };

  return (
    <div className="drag-region flex items-center justify-between px-3 h-9 flex-shrink-0">
      <div className="flex items-center gap-2 text-[11px] text-white/60">
        <div className="w-2 h-2 rounded-full bg-emerald-400/80 shadow-[0_0_6px_rgba(52,211,153,0.6)]" />
        <span className="truncate max-w-[140px]">{activeCharacter?.name || t('AI Companion')}</span>
      </div>
      <div className="no-drag flex items-center gap-1">
        <IconBtn title={t('History')} onClick={() => setShowHistory(true)}>
          {/* clock-rewind icon (svg) */}
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12a9 9 0 1 0 3-6.7" />
            <path d="M3 4v5h5" />
            <path d="M12 7v5l3 2" />
          </svg>
        </IconBtn>
        <IconBtn title={config.alwaysOnTop ? t('Unpin') : t('Pin')} onClick={togglePin} active={config.alwaysOnTop}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="m12 17 .01 5" />
            <path d="M5 9V3h14v6l3 4H2l3-4Z" />
          </svg>
        </IconBtn>
        <IconBtn title={t('Settings')} onClick={() => setShowSettings(true)}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
          </svg>
        </IconBtn>
        <IconBtn title={t('Minimize to tray')} onClick={() => window.electronAPI?.hide()}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14" />
          </svg>
        </IconBtn>
        <IconBtn title={t('Close')} onClick={() => window.electronAPI?.close()} danger>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </IconBtn>
      </div>
    </div>
  );
}

interface IconBtnProps {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  active?: boolean;
  danger?: boolean;
}

function IconBtn({ children, onClick, title, active, danger }: IconBtnProps) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-6 h-6 flex items-center justify-center rounded-md transition-colors ${
        active
          ? 'bg-companion-accent/30 text-companion-accent'
          : danger
          ? 'text-white/60 hover:text-red-400 hover:bg-red-500/15'
          : 'text-white/60 hover:text-white hover:bg-white/10'
      }`}
    >
      {children}
    </button>
  );
}
