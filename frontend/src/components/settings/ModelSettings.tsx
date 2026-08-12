import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

export function ModelSettings() {
  const config = useAppStore((s) => s.config);
  const { t } = useTranslation();

  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
      <div className="mb-1 text-xs font-medium text-white/60">{t('Model')}</div>
      <div className="flex justify-between text-xs">
        <span className="text-white/50">{t('Provider')}</span>
        <span className="text-white/90">{config.llmProvider}</span>
      </div>
      <div className="flex justify-between text-xs">
        <span className="text-white/50">{t('Model')}</span>
        <span className="text-white/90">{config.llmModel}</span>
      </div>
      <div className="flex justify-between text-xs">
        <span className="text-white/50">{t('API Key')}</span>
        <span className={config.hasApiKey ? 'text-emerald-400' : 'text-red-400'}>
          {config.hasApiKey ? t('Configured') : t('Missing')}
        </span>
      </div>
      <p className="border-t border-white/10 pt-1 text-[10px] text-white/40">
        {t('Edit `backend/.env` to change provider, model, and API key.')}
      </p>
    </div>
  );
}
