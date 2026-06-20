import { useState, useRef, useEffect } from 'react';
import { useChat } from '../../hooks/useChat';
import { useAppStore } from '../../stores/appStore';
import { MessageBubble } from './MessageBubble';
import { StreamingText } from './StreamingText';

export function ChatView() {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { messages, isStreaming, streamingContent, isConnected, send, newConversation } = useChat();
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const config = useAppStore((s) => s.config);
  const setShowSettings = useAppStore((s) => s.setShowSettings);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setInput('');
    send(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // If no API key configured, show setup prompt
  if (!config.hasApiKey && !isConnected) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-center space-y-4 max-w-md">
          <div className="text-5xl">&#x1f916;</div>
          <h2 className="text-xl text-companion-text">Welcome to AI Companion</h2>
          <p className="text-sm text-companion-text-muted">
            Configure your LLM in backend/.env. Supports any OpenAI-compatible API.
          </p>
          <button
            onClick={() => setShowSettings(true)}
            className="bg-companion-accent hover:bg-companion-accent-hover text-white px-6 py-2 rounded-lg transition-colors text-sm"
          >
            Check Connection
          </button>
        </div>
      </div>
    );
  }

  if (!activeCharacter) {
    return (
      <div className="flex-1 flex items-center justify-center text-companion-text-muted">
        <p>Loading character...</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && !isStreaming && (
          <div className="text-center text-companion-text-muted text-sm mt-8">
            <p className="text-lg mb-1">&#x1f4ac;</p>
            <p>Start a conversation with {activeCharacter.name}</p>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        {isStreaming && streamingContent && (
          <StreamingText text={streamingContent} />
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="px-4 py-3 border-t border-companion-border/30">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${activeCharacter.name}...`}
            rows={1}
            className="flex-1 bg-companion-card border border-companion-border rounded-xl px-4 py-2.5 text-sm text-companion-text placeholder-companion-text-muted resize-none outline-none input-glow transition-shadow"
            style={{ fontSize: config.fontSize }}
            disabled={isStreaming}
          />
          <button
            onClick={handleSend}
            disabled={isStreaming || !input.trim()}
            className="bg-companion-accent hover:bg-companion-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 rounded-xl transition-all text-sm font-medium"
          >
            Send
          </button>
        </div>
        <div className="flex justify-between mt-2 px-1">
          <button
            onClick={newConversation}
            className="text-[10px] text-companion-text-muted hover:text-companion-text transition-colors"
          >
            + New Chat
          </button>
          <span className="text-[10px] text-companion-text-muted">
            {isConnected ? '&#x1f7e2; Live' : '&#x1f534; Offline'}
          </span>
        </div>
      </div>
    </div>
  );
}
