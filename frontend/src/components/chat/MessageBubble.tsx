import ReactMarkdown from 'react-markdown';
import type { Message } from '../../types';

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
}

export function MessageBubble({ message, isStreaming }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fade-in`}>
      <div
        className={`max-w-[80%] px-3.5 py-2 text-sm leading-relaxed ${
          isUser
            ? 'bg-companion-accent/12 border border-companion-accent/20 text-companion-text rounded-2xl rounded-br-md'
            : 'bg-companion-card/60 border border-companion-border/30 text-companion-text/90 rounded-2xl rounded-bl-md'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        )}
        {isStreaming && (
          <span className="inline-block w-1.5 h-4 bg-companion-accent ml-0.5 animate-cursor-blink align-text-bottom" />
        )}
      </div>
    </div>
  );
}
