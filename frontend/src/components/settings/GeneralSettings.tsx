import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';

export function GeneralSettings() {
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const { t, language } = useTranslation();

  const handleLanguageToggle = () => {
    const next = language === 'zh' ? 'en' : 'zh';
    setConfig({ language: next });
    api.updateConfig({ language: next });
  };

  return (
    <div className="space-y-4">
      {/* Language */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <div className="flex items-center justify-between">
          <span className="text-xs text-white/60">{t('Language')}</span>
          <button
            onClick={handleLanguageToggle}
            className="flex items-center gap-1.5 rounded-md border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/80 transition-colors hover:bg-white/10"
          >
            <span className={language === 'zh' ? 'text-companion-accent' : 'text-white/40'}>{t('Chinese')}</span>
            <span className="text-white/30">|</span>
            <span className={language === 'en' ? 'text-companion-accent' : 'text-white/40'}>{t('English')}</span>
          </button>
        </div>
      </div>

      {/* Font Size */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <label className="mb-1 block text-xs text-white/50">
          {t('Font Size')} ({config.fontSize}px)
        </label>
        <input
          type="range"
          min="12"
          max="20"
          value={config.fontSize}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            setConfig({ fontSize: v });
            api.updateConfig({ font_size: v });
          }}
          className="w-full accent-companion-accent"
        />
      </div>

      {/* Always On Top */}
      <ToggleRow
        label={t('Always On Top')}
        checked={config.alwaysOnTop}
        onChange={(v) => {
          setConfig({ alwaysOnTop: v });
          api.updateConfig({ always_on_top: v });
          window.electronAPI?.setAlwaysOnTop(v);
        }}
      />

      {/* Floating Button */}
      <ToggleRow
        label={t('Floating Desktop Button')}
        hint={t('Show a floating avatar button after minimizing.')}
        checked={config.showFloatingIcon}
        onChange={(v) => {
          setConfig({ showFloatingIcon: v });
          api.updateConfig({ show_floating_icon: v });
          window.electronAPI?.setFloatingEnabled(v);
        }}
      />
    </div>
  );
}

function ToggleRow({ label, hint, checked, onChange }: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-white/10 bg-black/30 p-3">
      <div>
        <div className="text-xs text-white/60">{label}</div>
        {hint && <div className="mt-0.5 text-[10px] text-white/40">{hint}</div>}
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${checked ? 'bg-companion-accent' : 'bg-white/20'}`}
      >
        <div className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${checked ? 'translate-x-5' : ''}`} />
      </button>
    </div>
  );
}
