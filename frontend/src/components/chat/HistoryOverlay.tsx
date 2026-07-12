import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useChat } from '../../hooks/useChat';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';

export function HistoryOverlay() {
  const showHistory = useAppStore((s) => s.showHistory);
  const setShowHistory = useAppStore((s) => s.setShowHistory);
  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { newConversation } = useChat();
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    if (showHistory) {
      bottomRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  }, [showHistory, messages.length, streamingContent]);

  const handleDeleteMsg = async (msgId: string) => {
    if (!currentConversationId) return;
    if (confirmingDelete === msgId) {
      try {
        await fetch(`/api/conversations/${currentConversationId}/messages/${msgId}`, { method: 'DELETE' });
        // Remove from local store
        useChatStore.setState((s) => ({
          messages: s.messages.filter((m) => m.id !== msgId),
        }));
        setConfirmingDelete(null);
      } catch { /* ignore */ }
    } else {
      setConfirmingDelete(msgId);
    }
  };

  const handleClearContext = async () => {
    if (!currentConversationId || clearing) return;
    setClearing(true);
    try {
      const resp = await fetch(`/api/conversations/${currentConversationId}/messages`, { method: 'DELETE' });
      const data = await resp.json();
      console.log('[Context] cleared:', data.messages_deleted, 'msgs,', data.memories_deleted, 'memories');
      useChatStore.setState({ messages: [] });
    } catch { /* ignore */ }
    setClearing(false);
  };

  // Token estimate
  const totalChars = messages.reduce((sum, m) => sum + m.content.length, 0);
  const estimatedTokens = Math.round(totalChars * 0.3);

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
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium text-white/90">
              {t('Chat with {name}', { name: activeCharacter?.name || 'AI' })}
            </h3>
            <span className="text-[10px] text-white/40">
              {messages.length} {t('messages')} · ~{estimatedTokens} tokens
            </span>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleClearContext}
              disabled={clearing || messages.length === 0}
              className="text-[11px] text-red-400/70 hover:text-red-400 disabled:opacity-30 transition-colors"
            >
              {clearing ? t('Clearing...') : t('Clear All')}
            </button>
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
              className={`flex group ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-fade-in`}
            >
              <div className="relative">
                <div
                  className={`max-w-[80%] px-3.5 py-2 text-sm leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-companion-accent/25 border border-companion-accent/30 text-white rounded-2xl rounded-br-md'
                      : 'bg-white/10 border border-white/10 text-white/90 rounded-2xl rounded-bl-md'
                  }`}
                >
                  {msg.role === 'user' ? (
                    <p className="whitespace-pre-wrap pr-4">{msg.content}</p>
                  ) : (
                    <div className="prose prose-invert prose-sm max-w-none pr-4">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
                <button
                  onClick={() => handleDeleteMsg(msg.id)}
                  className={`absolute top-1 right-1 text-[10px] transition-colors ${
                    confirmingDelete === msg.id
                      ? 'text-red-400 font-medium'
                      : 'text-white/30 hover:text-red-400/80 opacity-0 group-hover:opacity-100'
                  }`}
                >
                  {confirmingDelete === msg.id ? t('Confirm?') : '🗑'}
                </button>
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
