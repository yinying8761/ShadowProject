import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

export function TitleBar() {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const setShowHistory = useAppStore((s) => s.setShowHistory);
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const layoutMode = useAppStore((s) => s.layoutMode);
  const inGroup = useAppStore((s) => s.activeGroup !== null);
  const setLayoutMode = useAppStore((s) => s.setLayoutMode);
  const { t } = useTranslation();

  const togglePin = () => {
    const next = !config.alwaysOnTop;
    setConfig({ alwaysOnTop: next });
    window.electronAPI?.setAlwaysOnTop(next);
  };

  return (
    <div className="drag-region flex items-center justify-between px-3 h-9 flex-shrink-0">
      <div className="flex items-center gap-2 text-[11px] text-white/90 drop-shadow-[0_1px_3px_rgba(0,0,0,0.7)]">
        <div className="w-2 h-2 rounded-full bg-companion-accent/80 shadow-[0_0_6px_rgba(0,198,255,0.6)] drop-shadow-[0_1px_2px_rgba(0,0,0,0.5)]" />
        <span className="truncate max-w-[140px]">{activeCharacter?.name || t('AI Companion')}</span>
      </div>
      <div className="no-drag flex items-center gap-1">
        {/* 群聊强制完整模式，模式开关在群里没有意义（store 也会拒绝 compact） */}
        {!inGroup && (
        <IconBtn
          title={layoutMode === 'compact' ? t('Expand') : t('Collapse')}
          onClick={() => setLayoutMode(layoutMode === 'compact' ? 'full' : 'compact')}
        >
          {layoutMode === 'compact' ? (
            /* expand arrows icon */
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          ) : (
            /* collapse arrows icon */
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="4 8 4 3 9 3" />
              <polyline points="20 16 20 21 15 21" />
              <line x1="4" y1="3" x2="11" y2="10" />
              <line x1="20" y1="21" x2="13" y2="14" />
            </svg>
          )}
        </IconBtn>
        )}
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
      className={`w-6 h-6 flex items-center justify-center rounded-md transition-colors drop-shadow-[0_1px_2px_rgba(0,0,0,0.5)] ${
        active
          ? 'bg-companion-accent/30 text-companion-accent'
          : danger
          ? 'text-white/80 hover:text-red-400 hover:bg-red-500/15'
          : 'text-white/80 hover:text-white hover:bg-white/15'
      }`}
    >
      {children}
    </button>
  );
}
