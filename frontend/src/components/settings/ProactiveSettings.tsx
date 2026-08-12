import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';

export function ProactiveSettings() {
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { t } = useTranslation();

  const levels = [
    { v: 'off' as const, label: t('Off'), hint: t('Never proactive') },
    { v: 'low' as const, label: t('Low'), hint: t('8-15 minutes') },
    { v: 'medium' as const, label: t('Medium'), hint: t('4-8 minutes') },
    { v: 'high' as const, label: t('High'), hint: t('2-5 minutes') },
  ];

  return (
    <div className="space-y-3">
      <div className="text-xs font-medium text-white/60">{t('Proactive Chat')}</div>

      {/* Level selector */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <div className="mb-2 text-[10px] text-white/40">
          {t('After a period of silence, {name} may start a conversation first.', {
            name: activeCharacter?.name || t('AI Companion'),
          })}
        </div>
        <div className="grid grid-cols-4 gap-1">
          {levels.map((opt) => (
            <button
              key={opt.v}
              onClick={() => {
                setConfig({ proactiveChatLevel: opt.v });
                api.updateConfig({ proactive_chat_level: opt.v });
              }}
              title={opt.hint}
              className={`rounded-md border px-2 py-1.5 text-[11px] transition-colors ${
                config.proactiveChatLevel === opt.v
                  ? 'border-companion-accent/40 bg-companion-accent/20 text-companion-accent'
                  : 'border-white/10 bg-white/5 text-white/60 hover:bg-white/10'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Daily limit */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <label className="mb-1 block text-xs text-white/50">
          {t('Daily Idle Trigger Limit')} ({config.proactiveDailyLimit})
        </label>
        <input
          type="range" min="0" max="30" value={config.proactiveDailyLimit}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            setConfig({ proactiveDailyLimit: v });
            api.updateConfig({ proactive_daily_limit: v });
          }}
          className="w-full accent-companion-accent"
        />
        <div className="mt-1 text-[10px] text-white/40">
          {t('This limit only applies to silence-based proactive messages.')}
        </div>
      </div>

      <ProactiveToggle
        label={t('Fixed Schedule Check-ins')}
        hint={t('Trigger at 07:00, 12:00, and 18:00 without consuming the daily limit.')}
        checked={config.proactiveFixedScheduleEnabled}
        onChange={(v) => {
          setConfig({ proactiveFixedScheduleEnabled: v });
          api.updateConfig({ proactive_fixed_schedule_enabled: v });
        }}
      />

      <ProactiveToggle
        label={t('Auto Check Screen Before Proactive Reply')}
        hint={t('Gather the latest screen context before composing a proactive message.')}
        checked={config.proactiveAutoSeeScreen}
        onChange={(v) => {
          setConfig({ proactiveAutoSeeScreen: v });
          api.updateConfig({ proactive_auto_see_screen: v });
        }}
      />

      <ProactiveToggle
        label={t('Skip Approval For Proactive Tools')}
        hint={t('Only affects proactive flows. Regular manual tool calls still ask for approval.')}
        checked={config.proactiveSilentToolApproval}
        onChange={(v) => {
          setConfig({ proactiveSilentToolApproval: v });
          api.updateConfig({ proactive_silent_tool_approval: v });
        }}
      />

      <ProactiveToggle
        label={t('System Notification For Proactive Reply')}
        hint={t('When minimized or hidden, show a system notification for proactive replies.')}
        checked={config.proactiveSystemNotification}
        onChange={(v) => {
          setConfig({ proactiveSystemNotification: v });
          api.updateConfig({ proactive_system_notification: v });
        }}
      />
    </div>
  );
}

function ProactiveToggle({ label, hint, checked, onChange }: {
  label: string; hint: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-white/10 bg-black/30 p-3">
      <div>
        <div className="text-xs text-white/60">{label}</div>
        <div className="mt-0.5 text-[10px] text-white/40">{hint}</div>
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
