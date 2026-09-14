import { useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';

/**
 * Inline, persistent error bubble (Workflow I, issue #39).
 *
 * Friendly copy plus an expandable raw server message. Lives in chatStore
 * memory only: dismissed by hand, cleared on conversation switch, never
 * written to the database. Keyed by `raw` so a new error starts collapsed.
 */
function ErrorDetails({ raw }: { raw: string }) {
  const [showRaw, setShowRaw] = useState(false);
  const { t } = useTranslation();

  return (
    <div className="mt-1 pl-5">
      <button
        onClick={() => setShowRaw((v) => !v)}
        className="text-[10px] text-red-200/70 hover:text-red-100 transition-colors"
      >
        {showRaw ? t('Hide details') : t('Show details')}
      </button>
      {showRaw && (
        <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-black/25 px-2 py-1 text-[10px] text-red-100/80">
          {raw}
        </pre>
      )}
    </div>
  );
}

export function ErrorBubble() {
  const errorBubble = useChatStore((s) => s.errorBubble);
  const dismissErrorBubble = useChatStore((s) => s.dismissErrorBubble);
  const { t } = useTranslation();

  if (!errorBubble) return null;

  return (
    <div className="no-drag mx-3 mb-1 animate-fade-in">
      <div
        role="alert"
        className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2"
      >
        <div className="flex items-start gap-2">
          <span aria-hidden className="text-xs leading-5 text-red-300">
            ⚠
          </span>
          <span className="flex-1 text-xs leading-5 text-red-200">
            {errorBubble.friendly}
          </span>
          <button
            onClick={dismissErrorBubble}
            title={t('Close')}
            aria-label={t('Close')}
            className="text-[11px] leading-5 text-red-200/60 hover:text-red-100 transition-colors"
          >
            ✕
          </button>
        </div>
        {errorBubble.raw && <ErrorDetails key={errorBubble.raw} raw={errorBubble.raw} />}
      </div>
    </div>
  );
}