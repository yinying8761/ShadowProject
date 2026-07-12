import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

/**
 * Floating dialogue box — shows only the latest assistant message.
 * During streaming, displays the live token feed.
 * When idle and no message yet, shows a soft greeting placeholder.
 */
export function DialogueBox() {
  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const streamingIsProactive = useChatStore((s) => s.streamingIsProactive);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const memoryNotificationCount = useChatStore((s) => s.memoryNotificationCount);
  const dismissMemoryNotification = useChatStore((s) => s.dismissMemoryNotification);
  const setShowMemoryViewer = useChatStore((s) => s.setShowMemoryViewer);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const config = useAppStore((s) => s.config);
  const scrollRef = useRef<HTMLDivElement>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();

  // Auto-dismiss memory toast after 8 seconds
  useEffect(() => {
    if (memoryNotificationCount > 0) {
      if (toastTimer.current) clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => {
        dismissMemoryNotification();
      }, 8000);
    }
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [memoryNotificationCount, dismissMemoryNotification]);

  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
  const displayContent = isStreaming
    ? streamingContent
    : lastAssistant?.content || '';
  const isProactive = isStreaming
    ? streamingIsProactive
    : lastAssistant?.isProactive || false;

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, [displayContent]);

  const { t } = useTranslation();
  const characterName = activeCharacter?.name || 'AI';
  const showPlaceholder = !displayContent && !isStreaming;

  return (
    <>
      {/* Memory updated toast */}
      {memoryNotificationCount > 0 && (
        <div className="no-drag mx-3 mb-1 animate-fade-in">
          <button
            onClick={() => {
              dismissMemoryNotification();
              setShowMemoryViewer(true);
            }}
            className="flex w-full items-center justify-between rounded-lg border border-companion-accent/30 bg-companion-accent/15 px-3 py-2 text-left transition-colors hover:bg-companion-accent/25"
          >
            <span className="text-xs text-companion-accent">
              {t('{count} new memories saved', {
                count: String(memoryNotificationCount),
              })}
            </span>
            <span className="text-[10px] text-companion-accent/70">
              {t('View')} →
            </span>
          </button>
        </div>
      )}

      <div className="no-drag dialogue-box mx-3 px-4 py-3 animate-fade-in">
      <div className="flex items-center gap-2 mb-2">
        <span className="name-tag">{characterName}</span>
        {lastAssistant && !isStreaming && (
          <button
            onClick={() => useChatStore.getState().speakMessage?.(lastAssistant.content)}
            className="text-[11px] text-white/40 hover:text-companion-accent transition-colors ml-1"
            title={t('Replay')}
          >
            🔊
          </button>
        )}
        {isProactive && (
          <span
            className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300/90 border border-amber-500/30"
            title={t('Proactive')}
          >
            {t('Proactive')}
          </span>
        )}
        {isStreaming && (
          <span className="text-[10px] text-white/40 animate-pulse-soft">
            {t('Speaking…')}
          </span>
        )}
      </div>
      <div
        ref={scrollRef}
        className="max-h-[120px] overflow-y-auto pr-1"
        style={{ fontSize: config.fontSize }}
      >
        {showPlaceholder ? (
          <p className="text-white/40 italic leading-relaxed">
            {t('Say hi to {name}～', { name: characterName })}
          </p>
        ) : (
          <div className="text-white/90 leading-relaxed prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{displayContent}</ReactMarkdown>
            {isStreaming && (
              <span className="inline-block w-1.5 h-4 bg-companion-accent ml-0.5 animate-cursor-blink align-text-bottom" />
            )}
          </div>
        )}
      </div>
    </div>
    </>
  );
}
