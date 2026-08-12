import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { VoiceClonePanel } from './VoiceClonePanel';

export function VoiceSettings() {
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const { t } = useTranslation();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between rounded-lg border border-white/10 bg-black/30 p-3">
        <div>
          <div className="text-xs text-white/60">{t('Voice (TTS)')}</div>
          <div className="mt-0.5 text-[10px] text-white/40">
            {t('Automatically read AI replies aloud. Voice adapts to character personality.')}
          </div>
        </div>
        <button
          onClick={() => {
            const v = !config.ttsEnabled;
            setConfig({ ttsEnabled: v });
            api.updateConfig({ tts_enabled: v });
          }}
          className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${config.ttsEnabled ? 'bg-companion-accent' : 'bg-white/20'}`}
        >
          <div className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${config.ttsEnabled ? 'translate-x-5' : ''}`} />
        </button>
      </div>

      <VoiceClonePanel />
    </div>
  );
}
