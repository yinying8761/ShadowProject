import { useEffect, useState } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { CharacterAvatar } from '../character/CharacterAvatar';
import type { Conversation } from '../../types';

interface ConversationSidebarProps {
  onSwitchConversation: (convId: string) => void;
  onNewConversation: () => void;
}

export function ConversationSidebar({ onSwitchConversation, onNewConversation }: ConversationSidebarProps) {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const { t } = useTranslation();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const loadConversations = () => {
    if (!activeCharacter) return;
    api.fetchConversations(activeCharacter.id)
      .then(setConversations)
      .catch(console.error);
  };

  useEffect(() => {
    loadConversations();
  }, [activeCharacter, currentConversationId]);

  const handleDelete = async (convId: string) => {
    if (confirmingDelete === convId) {
      // Second click: execute delete
      try {
        await api.deleteConversation(convId);
        setConfirmingDelete(null);

        // Remove from local list
        setConversations((prev) => prev.filter((c) => c.id !== convId));

        // If deleted current conversation, switch to the next one or create new
        if (convId === currentConversationId) {
          const remaining = conversations.filter((c) => c.id !== convId);
          if (remaining.length > 0) {
            onSwitchConversation(remaining[0].id);
          } else {
            onNewConversation();
          }
        }
      } catch (e) {
        console.error('Failed to delete conversation:', e);
        setConfirmingDelete(null);
      }
    } else {
      setConfirmingDelete(convId);
      // Auto-clear confirm state after 3 seconds
      setTimeout(() => setConfirmingDelete(null), 3000);
    }
  };

  const avatarUrl = activeCharacter?.avatar_path
    ? `http://localhost:8722/data/${activeCharacter.avatar_path}`
    : null;

  return (
    <aside className="w-[180px] flex-shrink-0 flex flex-col border-r border-companion-border/30 bg-companion-sidebar/60" role="navigation" aria-label={t('Conversations')}>
      {/* Character avatar section */}
      <div className="flex flex-col items-center gap-2 px-3 py-4 border-b border-companion-border/20">
        <CharacterAvatar
          name={activeCharacter?.name || 'AI'}
          avatarUrl={avatarUrl}
        />
        <span className="text-[11px] text-companion-text/70 truncate max-w-full">
          {activeCharacter?.name || 'AI'}
        </span>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto py-2">
        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={`group flex items-center ${
              conv.id === currentConversationId
                ? 'bg-companion-accent/10 border-r-2 border-companion-accent'
                : ''
            }`}
          >
            <button
              onClick={() => onSwitchConversation(conv.id)}
              className={`flex-1 text-left px-3 py-2 text-xs truncate transition-colors ${
                conv.id === currentConversationId
                  ? 'text-companion-accent'
                  : 'text-companion-text/60 hover:text-companion-text hover:bg-white/5'
              }`}
            >
              {conv.title || `${t('Chat')} ${conv.id.slice(0, 6)}`}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); handleDelete(conv.id); }}
              title={confirmingDelete === conv.id ? t('Confirm?') : t('Delete')}
              className={`px-2 py-1 text-[10px] transition-colors opacity-0 group-hover:opacity-100 ${
                confirmingDelete === conv.id
                  ? 'text-red-400 font-medium opacity-100'
                  : 'text-white/30 hover:text-red-400/80'
              }`}
            >
              {confirmingDelete === conv.id ? '?!' : '✕'}
            </button>
          </div>
        ))}
        {conversations.length === 0 && (
          <p className="px-3 py-2 text-[10px] text-companion-text/40">
            {t('No conversations')}
          </p>
        )}
      </div>

      {/* New conversation button */}
      <div className="p-2 border-t border-companion-border/20">
        <button
          onClick={onNewConversation}
          className="w-full rounded-md border border-companion-accent/20 bg-companion-accent/8 px-3 py-1.5 text-[11px] text-companion-accent hover:bg-companion-accent/15 transition-colors"
        >
          + {t('New')}
        </button>
      </div>
    </aside>
  );
}
