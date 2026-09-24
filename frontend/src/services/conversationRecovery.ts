import { api } from './api';
import { useAppStore } from '../stores/appStore';
import { useChatStore } from '../stores/chatStore';

/**
 * The conversation the client holds an id for can be gone without the client
 * ever seeing it: deleted in another window, or a stale id because the backend
 * restarted against a different data dir. The server now *refuses* such turns
 * (HTTP 404 / ws `conversation_not_found`) instead of silently persisting
 * messages nothing owns — see
 * docs/analysis/group-chat-review-and-orphan-data.md §B2.3 — and this is the
 * client half of that contract: open a fresh conversation for the active
 * character and let the ws bridge reconnect to it.
 *
 * Guarded per failed id so a server that keeps refusing cannot turn into a
 * create-conversation loop. Returns the new conversation id, or null when
 * recovery was already attempted for that id or is impossible — callers then
 * surface the error instead of swallowing it.
 *
 * Deliberately does not touch the message list: the HTTP fallback retries the
 * message the user just typed and must keep it.
 */
let lastAttemptedId = '';

export async function recoverMissingConversation(failedId: string): Promise<string | null> {
  if (!failedId || lastAttemptedId === failedId) return null;
  const { activeCharacter, activeGroup } = useAppStore.getState();
  if (!activeGroup && !activeCharacter) return null;

  lastAttemptedId = failedId;
  try {
    // 在群里就再开一条群对话，否则开该角色的单聊（同一个恢复动作，两种会话）。
    const conv = activeGroup
      ? await api.createGroupConversation(activeGroup.id)
      : await api.createConversation(activeCharacter!.id);
    useChatStore.getState().setConversationId(conv.id);
    return conv.id;
  } catch (e) {
    console.error('Failed to recover from a missing conversation:', e);
    return null;
  }
}
