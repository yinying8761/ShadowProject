import { useEffect, useRef } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { MessageBubble } from './MessageBubble';

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  const characterName = activeCharacter?.name || 'AI';
  const isEmpty = messages.length === 0 && !isStreaming;

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
      {isEmpty && (
        <div className="text-center text-companion-text/40 text-sm mt-12">
          {t('Say hi to {name}～', { name: characterName })}
        </div>
      )}

      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}

      {/* Streaming preview */}
      {isStreaming && streamingContent && (
        <MessageBubble
          message={{
            id: 'streaming',
            conversationId: '',
            role: 'assistant',
            content: streamingContent,
            createdAt: new Date().toISOString(),
          }}
          isStreaming
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
}
