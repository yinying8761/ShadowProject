import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useChat } from '../../hooks/useChat';
import { useTranslation } from '../../i18n/useTranslation';

/**
 * Overlay showing full conversation history.
 * Triggered from the title bar.
 */
export function HistoryOverlay() {
  const showHistory = useAppStore((s) => s.showHistory);
  const setShowHistory = useAppStore((s) => s.setShowHistory);
  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { newConversation } = useChat();
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (showHistory) {
      bottomRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  }, [showHistory, messages.length, streamingContent]);

  if (!showHistory) return null;

  return (
    <div
      className="no-drag fixed inset-0 bg-black/70 flex items-center justify-center z-40 backdrop-blur-sm"
      onClick={() => setShowHistory(false)}
    >
      <div
        className="bg-companion-overlay-strong border border-white/10 rounded-xl w-[90%] h-[85%] flex flex-col overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/10 flex-shrink-0">
          <h3 className="text-sm font-medium text-white/90">
            {t('Chat with {name}', { name: activeCharacter?.name || 'AI' })}
          </h3>
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                newConversation();
                setShowHistory(false);
              }}
              className="text-[11px] text-white/50 hover:text-white/90 transition-colors"
            >
              {t('New Conversation')}
            </button>
            <button
              onClick={() => setShowHistory(false)}
              className="text-white/50 hover:text-white text-xs"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && !isStreaming && (
            <div className="text-center text-white/40 text-sm mt-12">
              {t('No messages yet')}
            </div>
          )}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
            >
              <div
                className={`max-w-[80%] px-3.5 py-2 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-companion-accent/25 border border-companion-accent/30 text-white rounded-2xl rounded-br-md'
                    : 'bg-white/10 border border-white/10 text-white/90 rounded-2xl rounded-bl-md'
                }`}
              >
                {msg.role === 'user' ? (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                ) : (
                  <div className="prose prose-invert prose-sm max-w-none">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
          ))}
          {isStreaming && streamingContent && (
            <div className="flex justify-start animate-fade-in">
              <div className="max-w-[80%] px-3.5 py-2 text-sm bg-white/10 border border-white/10 text-white/90 rounded-2xl rounded-bl-md">
                <div className="prose prose-invert prose-sm max-w-none">
                  <ReactMarkdown>{streamingContent}</ReactMarkdown>
                </div>
                <span className="inline-block w-1.5 h-4 bg-companion-accent ml-0.5 animate-cursor-blink align-text-bottom" />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
