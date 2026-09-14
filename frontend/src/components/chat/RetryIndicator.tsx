import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';

/**
 * Transient retry-progress stripe (Workflow I, issue #39).
 *
 * Visible only while the server is backing off an LLM call; any later
 * event (token / done / error) clears it from the store.
 */
export function RetryIndicator() {
  const retryState = useChatStore((s) => s.retryState);
  const { t } = useTranslation();

  if (!retryState) return null;

  return (
    <div className="no-drag mx-3 mb-1 animate-fade-in">
      <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] text-amber-200">
        <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-amber-300/30 border-t-amber-300" />
        <span>
          {t('Retrying ({attempt}/{max})', {
            attempt: String(retryState.attempt),
            max: String(retryState.maxRetries),
          })}
        </span>
      </div>
    </div>
  );
}