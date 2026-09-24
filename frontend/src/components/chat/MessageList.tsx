import { useEffect, useRef } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { characterAvatarUrl } from '../../utils/avatarUrl';
import { speakerColor } from '../../utils/speakerStyle';
import { MessageBubble } from './MessageBubble';
import type { SpeakerMark } from './MessageBubble';
import type { Message } from '../../types';

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const activeGroup = useAppStore((s) => s.activeGroup);
  const characters = useAppStore((s) => s.characters);
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  const characterName = activeCharacter?.name || 'AI';
  const isEmpty = messages.length === 0 && !isStreaming;

  /**
   * 群聊里这条消息是谁说的。
   * 名字优先用历史接口解析好的（成员被移出群后仍报自己的名字），
   * 其次是群里记着的角色名，最后才是角色表。
   */
  const speakerOf = (msg: Message): SpeakerMark | undefined => {
    if (!activeGroup || msg.role !== 'assistant' || !msg.speakerId) return undefined;
    const character = characters.find((c) => c.id === msg.speakerId);
    const member = activeGroup.members.find((m) => m.character_id === msg.speakerId);
    return {
      name: msg.speakerName || member?.character_name || character?.name || null,
      avatarUrl: characterAvatarUrl(character?.avatar_path),
      color: speakerColor(msg.speakerId),
    };
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
      {isEmpty && (
        <div className="text-center text-companion-text/40 text-sm mt-12">
          {activeGroup
            ? t('Say hi to the group～')
            : t('Say hi to {name}～', { name: characterName })}
        </div>
      )}

      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} speaker={speakerOf(msg)} />
      ))}

      {/* Streaming preview — 1:1 only: 群轮不流式（跳过的那条绝不推送） */}
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
