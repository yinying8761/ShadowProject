import { useState, useRef, useEffect } from 'react';
import { useChat } from '../../hooks/useChat';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

export function InputBar() {
  const [input, setInput] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { isStreaming, isConnected, send, newConversation } = useChat();
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const config = useAppStore((s) => s.config);
  const { t } = useTranslation();

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 100) + 'px';
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setInput('');
    send(trimmed);
  };

  const handleSeeScreen = () => {
    if (isStreaming || !activeCharacter) return;
    const text = input.trim() || '看看我屏幕';
    setInput('');
    // Window is excluded from screen capture at startup (setContentProtection),
    // so the AI won't see its own UI — no need to hide/toggle here.
    send(text, { forceVision: true });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const placeholder = isStreaming
    ? t('{name} is thinking…', { name: activeCharacter?.name || '' })
    : activeCharacter
    ? t('Say something to {name}…', { name: activeCharacter.name })
    : t('Select a character first');

  return (
    <div className="no-drag mx-3 mb-3 mt-2 input-bar px-3 py-2">
      <div className="flex items-end gap-2">
        <button
          onClick={handleSeeScreen}
          disabled={isStreaming || !activeCharacter}
          title={t('Let her see your screen')}
          className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-md bg-white/8 hover:bg-white/15 disabled:opacity-30 disabled:cursor-not-allowed text-white/70 hover:text-white transition-all"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
            <circle cx="12" cy="13" r="4" />
          </svg>
        </button>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          className="flex-1 bg-transparent text-white placeholder-white/35 resize-none outline-none input-glow rounded-md px-1 py-1.5 leading-relaxed"
          style={{ fontSize: config.fontSize, maxHeight: 100 }}
          disabled={isStreaming || !activeCharacter}
        />
        <button
          onClick={handleSend}
          disabled={isStreaming || !input.trim() || !activeCharacter}
          title="发送 (Enter)"
          className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-md bg-companion-accent hover:bg-companion-accent-hover disabled:opacity-30 disabled:cursor-not-allowed text-white transition-all"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="m22 2-7 20-4-9-9-4Z" />
            <path d="M22 2 11 13" />
          </svg>
        </button>
      </div>
      <div className="flex items-center justify-between mt-1 px-1 text-[10px]">
        <button
          onClick={newConversation}
          className="text-white/40 hover:text-white/80 transition-colors"
        >
          {t('+ New Conversation')}
        </button>
        <span className={`flex items-center gap-1 ${isConnected ? 'text-emerald-400/80' : 'text-red-400/80'}`}>
          <span className={`w-1 h-1 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          {isConnected ? t('Live') : t('Offline (input)')}
        </span>
      </div>
    </div>
  );
}
