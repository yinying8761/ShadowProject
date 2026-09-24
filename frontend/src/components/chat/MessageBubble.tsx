import ReactMarkdown from 'react-markdown';
import type { Message } from '../../types';
import { CharacterAvatar } from '../character/CharacterAvatar';

/** 群聊里这条消息的说话人（名字 + 视觉标识）。1:1 不传。 */
export interface SpeakerMark {
  name: string | null;
  avatarUrl?: string | null;
  color: string;
}

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  speaker?: SpeakerMark;
}

export function MessageBubble({ message, isStreaming, speaker }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fade-in`}>
      {!isUser && speaker && (
        <div className="flex-shrink-0 mr-2 self-start">
          <CharacterAvatar name={speaker.name || 'AI'} avatarUrl={speaker.avatarUrl} size="sm" />
        </div>
      )}

      <div className="max-w-[80%] min-w-0">
        {/* 群聊：谁说的（名字 + 每个说话人一个稳定颜色） */}
        {!isUser && speaker && (
          <div className="mb-0.5 px-0.5">
            <span className="text-[10px] font-medium" style={{ color: speaker.color }}>
              {speaker.name || 'AI'}
            </span>
          </div>
        )}

        <div
          className={`px-3.5 py-2 text-sm leading-relaxed ${
            isUser
              ? 'bg-companion-accent/12 border border-companion-accent/20 text-companion-text rounded-2xl rounded-br-md'
              : 'bg-companion-card/60 border border-companion-border/30 text-companion-text/90 rounded-2xl rounded-bl-md'
          }`}
          style={!isUser && speaker ? { borderLeftWidth: 2, borderLeftColor: speaker.color } : undefined}
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
    </div>
  );
}
