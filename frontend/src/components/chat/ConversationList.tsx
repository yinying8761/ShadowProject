import { useEffect, useRef, useState } from 'react';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';

const DEFAULT_TITLE = 'New Conversation';

export interface ConversationListItem {
  id: string;
  title: string;
}

/** 两个侧栏共用的"什么时候该重新载入列表"的键（拼成字符串，避免每次渲染都重载）。 */
export function conversationReloadKey(
  ...parts: (string | null | undefined)[]
): string {
  return parts.map((p) => p ?? '').join('|');
}

interface ConversationListProps {
  /** 载入当前会话的对话列表：1:1 用角色的，群聊用群的 */
  load: () => Promise<ConversationListItem[]>;
  /** 变化即重新载入（会话身份 / 当前对话 / 列表版本号），传字符串以免每次渲染都重载 */
  reloadKey: string;
  currentId: string | null;
  onSwitch: (convId: string) => void;
  onNew: () => void;
}

/**
 * 对话列表：选中、行内改名、删除二次确认、左下「新对话」。
 * 1:1 与群聊共用同一份交互（只有载入方式不同），头部由各自侧栏负责。
 */
export function ConversationList({
  load,
  reloadKey,
  currentId,
  onSwitch,
  onNew,
}: ConversationListProps) {
  const { t } = useTranslation();
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);

  // load 每次渲染都是新函数：用 ref 取最新的，避免它进依赖导致死循环
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    loadRef
      .current()
      .then((rows) => {
        if (!cancelled) setConversations(rows);
      })
      .catch(console.error);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // Focus input when entering edit mode
  useEffect(() => {
    if (editingId) {
      editInputRef.current?.focus();
      editInputRef.current?.select();
    }
  }, [editingId]);

  const displayTitle = (title: string, convId: string) => {
    if (title === DEFAULT_TITLE) return t('New Conversation');
    return title || `${t('Chat')} ${convId.slice(0, 6)}`;
  };

  const handleStartEdit = (convId: string, currentTitle: string) => {
    setEditingId(convId);
    setEditValue(currentTitle === DEFAULT_TITLE ? '' : currentTitle);
  };

  const handleConfirmEdit = async (convId: string) => {
    const trimmed = editValue.trim();
    if (!trimmed) return;

    try {
      const updated = await api.updateConversation(convId, trimmed);
      setConversations((prev) =>
        prev.map((c) => (c.id === convId ? { ...c, title: updated.title } : c))
      );
    } catch (e) {
      console.error('Failed to rename conversation:', e);
    } finally {
      setEditingId(null);
      setEditValue('');
    }
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditValue('');
  };

  const handleDelete = async (convId: string) => {
    if (confirmingDelete !== convId) {
      setConfirmingDelete(convId);
      setTimeout(() => setConfirmingDelete(null), 3000);
      return;
    }
    try {
      await api.deleteConversation(convId);
      setConfirmingDelete(null);
      const remaining = conversations.filter((c) => c.id !== convId);
      setConversations(remaining);

      if (convId === currentId) {
        if (remaining.length > 0) onSwitch(remaining[0].id);
        else onNew();
      }
    } catch (e) {
      console.error('Failed to delete conversation:', e);
      setConfirmingDelete(null);
    }
  };

  return (
    <>
      <div className="flex-1 overflow-y-auto py-2">
        {conversations.map((conv) => {
          const isEditing = editingId === conv.id;
          return (
            <div
              key={conv.id}
              className={`group flex items-center ${
                conv.id === currentId
                  ? 'bg-companion-accent/10 border-r-2 border-companion-accent'
                  : ''
              }`}
            >
              {isEditing ? (
                <div className="flex-1 flex items-center px-2 py-1.5">
                  <input
                    ref={editInputRef}
                    type="text"
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleConfirmEdit(conv.id);
                      } else if (e.key === 'Escape') {
                        handleCancelEdit();
                      }
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      handleCancelEdit();
                    }}
                    // Click anywhere outside the input blurs it → cancel edit
                    onBlur={() => handleCancelEdit()}
                    className="flex-1 bg-companion-bg/80 text-companion-text text-xs px-2 py-1 rounded border border-companion-border/40 focus:border-companion-accent/60 outline-none truncate"
                  />
                </div>
              ) : (
                <>
                  <button
                    onClick={() => onSwitch(conv.id)}
                    className={`flex-1 text-left px-3 py-2 text-xs truncate transition-colors ${
                      conv.id === currentId
                        ? 'text-companion-accent'
                        : 'text-companion-text/60 hover:text-companion-text hover:bg-white/5'
                    }`}
                  >
                    {displayTitle(conv.title, conv.id)}
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleStartEdit(conv.id, conv.title);
                    }}
                    title={t('Edit')}
                    className="px-1.5 py-1 text-[10px] transition-colors opacity-0 group-hover:opacity-100 text-white/30 hover:text-companion-accent/80"
                  >
                    ✎
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(conv.id);
                    }}
                    title={confirmingDelete === conv.id ? t('Confirm?') : t('Delete')}
                    className={`px-2 py-1 text-[10px] transition-colors opacity-0 group-hover:opacity-100 ${
                      confirmingDelete === conv.id
                        ? 'text-red-400 font-medium opacity-100'
                        : 'text-white/30 hover:text-red-400/80'
                    }`}
                  >
                    {confirmingDelete === conv.id ? '?!' : '✕'}
                  </button>
                </>
              )}
            </div>
          );
        })}
        {conversations.length === 0 && (
          <p className="px-3 py-2 text-[10px] text-companion-text/40">
            {t('No conversations')}
          </p>
        )}
      </div>

      {/* New conversation button */}
      <div className="p-2 border-t border-companion-border/20">
        <button
          onClick={onNew}
          className="w-full rounded-md border border-companion-accent/20 bg-companion-accent/8 px-3 py-1.5 text-[11px] text-companion-accent hover:bg-companion-accent/15 transition-colors"
        >
          + {t('New')}
        </button>
      </div>
    </>
  );
}
