import { useCallback, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { api } from '../services/api';
import { toStoreMessage } from '../services/messageMapper';
import { recoverMissingConversation } from '../services/conversationRecovery';
import { getTranslation } from '../i18n/translations';
import type { Lang } from '../i18n/translations';
import { friendlyErrorKey } from '../utils/errorMessages';
import type { ApiMessage } from '../types';

// Module-level guard: prevents re-initialization when useChat is called
// from multiple mount points (e.g. InputBar inside CompactView + FullView).
// Without this, mode-switching kills the WebSocket because a new useChat
// instance resets currentConversationId to ''.
// The key is the *session* (character or 群) — so leaving a group re-inits the
// 1:1 conversation even though the active character never changed.
const _lastSessionKey = { current: null as string | null };

export function useChat() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const wsSendMessage = useChatStore((s) => s.wsSendMessage);
  const wsSendApprovalResponse = useChatStore((s) => s.wsSendApprovalResponse);
  const setConversationId = useChatStore((s) => s.setConversationId);
  const setMessages = useChatStore((s) => s.setMessages);
  const addMessage = useChatStore((s) => s.addMessage);
  const replaceMessageId = useChatStore((s) => s.replaceMessageId);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const setErrorBubble = useChatStore((s) => s.setErrorBubble);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const activeGroup = useAppStore((s) => s.activeGroup);
  const isConnected = useAppStore((s) => s.isConnected);
  const epoch = useRef(0);

  useEffect(() => {
    if (!activeCharacter && !activeGroup) return;
    const sessionKey = activeGroup ? `group:${activeGroup.id}` : `char:${activeCharacter!.id}`;
    // Guard: skip if already initialized for this session (mode switch)
    if (sessionKey === _lastSessionKey.current) return;
    _lastSessionKey.current = sessionKey;

    const gen = ++epoch.current; // bump generation to discard stale results
    clearMessages();             // immediately clear the old session's messages
    setConversationId('');
    setMessages([]);

    (async () => {
      try {
        if (activeGroup) {
          // 群聊：用该群的对话列表（最近一条优先），没有就开一条（对应左下"新对话"）
          const group = await api.fetchGroup(activeGroup.id);
          if (gen !== epoch.current) return; // stale

          const convId =
            group.conversations.length > 0
              ? group.conversations[0].id
              : (await api.createGroupConversation(group.id)).id;
          if (gen !== epoch.current) return; // stale

          const msgs = await api.fetchMessages(convId);
          if (gen !== epoch.current) return; // stale

          setConversationId(convId);
          setMessages(msgs.map((m: ApiMessage) => toStoreMessage(m, convId)));
          return;
        }

        const convs = await api.fetchConversations(activeCharacter!.id);
        if (gen !== epoch.current) return; // stale

        if (convs.length > 0) {
          const convId = convs[0].id;
          const msgs = await api.fetchMessages(convId);
          if (gen !== epoch.current) return; // stale

          setConversationId(convId);
          setMessages(msgs.map((m: ApiMessage) => toStoreMessage(m, convId)));
        } else {
          const conv = await api.createConversation(activeCharacter!.id);
          if (gen !== epoch.current) return; // stale

          setConversationId(conv.id);
          setMessages([]);
        }
      } catch (e) {
        console.error('Failed to load conversation:', e);
      }
    })();
  }, [activeCharacter, activeGroup]);

  const send = useCallback(
    async (text: string, opts?: { forceVision?: boolean }) => {
      // 群聊没有"当前角色"：只要在群里就能发言（后端按群成员编排）
      if (!text.trim() || !currentConversationId) return;
      if (!activeGroup && !activeCharacter) return;
      const userMsg = {
        // Random suffix: this id is now a wire correlation key echoed back
        // by the server, so millisecond-precision alone could collide.
        id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        conversationId: currentConversationId,
        role: 'user' as const,
        content: text,
        createdAt: new Date().toISOString(),
      };
      addMessage(userMsg);
      // Pass the temporary id so the server can echo it back in message_ack
      // (WS path, ticket #12) or user_message_id (HTTP fallback, ticket #13).
      // 群里没有单一角色：character_id 留空（后端忽略它，群成员由群决定）
      const sent = wsSendMessage?.(text, activeGroup ? '' : activeCharacter!.id, opts, userMsg.id) ?? false;
      if (!sent && activeGroup) {
        // 群聊只走 WS（HTTP 兜底对群对话是 400）：连接没起来就直说，别发一个注定失败的请求。
        // 文案走 i18n（不靠 friendlyErrorKey 猜关键词）。
        const lang = (useAppStore.getState().config.language as Lang) || 'zh';
        setErrorBubble({
          friendly: getTranslation('Reconnecting — group replies need the connection', lang),
          raw: 'Group conversations are answered over the websocket (offline)',
        });
        return;
      }
      if (!sent) {
        const post = (conversationId: string) =>
          fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: text,
              conversation_id: conversationId,
              character_id: activeCharacter!.id,
              client_message_id: userMsg.id,
            }),
          });
        try {
          let conversationId = currentConversationId;
          let res = await post(conversationId);
          if (res.status === 404) {
            // The server no longer has this conversation and now refuses to
            // persist against it (services/conversationRecovery). Open a fresh
            // conversation and retry the same message once — nothing the user
            // typed is lost to a stale id.
            const recovered = await recoverMissingConversation(conversationId);
            if (recovered) {
              conversationId = recovered;
              res = await post(recovered);
            }
          }
          if (!res.ok) {
            const detail = await res.json().catch(() => null);
            throw new Error(detail?.detail || `HTTP ${res.status}`);
          }
          const data = await res.json();
          if (data.user_message_id) {
            replaceMessageId(userMsg.id, data.user_message_id);
          }
          addMessage({
            id: data.message_id || `resp-${Date.now()}`,
            conversationId,
            role: 'assistant',
            content: data.content || '',
            createdAt: new Date().toISOString(),
          });
        } catch (e) {
          console.error('Failed to send message:', e);
          // Same inline bubble as the WS path (issue #39). The HTTP fallback
          // used to fall through to an empty assistant bubble on failure.
          const message = e instanceof Error ? e.message : String(e);
          const lang = (useAppStore.getState().config.language as Lang) || 'zh';
          setErrorBubble({
            friendly: getTranslation(friendlyErrorKey(message), lang),
            raw: message,
          });
        }
      }
    },
    [
      activeCharacter,
      activeGroup,
      currentConversationId,
      addMessage,
      replaceMessageId,
      wsSendMessage,
      setErrorBubble,
    ]
  );

  const newConversation = useCallback(async () => {
    if (activeGroup) {
      // 群里"新对话"：给群再开一条群对话（一个群可以有多条）
      clearMessages();
      try {
        const conv = await api.createGroupConversation(activeGroup.id);
        setConversationId(conv.id);
      } catch (e) {
        console.error('Failed to create group conversation:', e);
      }
      return;
    }
    if (!activeCharacter) return;
    clearMessages();
    try {
      const conv = await api.createConversation(activeCharacter.id);
      setConversationId(conv.id);
    } catch (e) {
      console.error('Failed to create conversation:', e);
    }
  }, [activeGroup, activeCharacter, clearMessages, setConversationId]);

  const switchConversation = useCallback(
    async (convId: string) => {
      if (!convId || convId === currentConversationId) return;
      clearMessages();
      try {
        const msgs = await api.fetchMessages(convId);
        setConversationId(convId);
        setMessages(msgs.map((m: ApiMessage) => toStoreMessage(m, convId)));
      } catch (e) {
        console.error('Failed to switch conversation:', e);
      }
    },
    [currentConversationId, clearMessages, setConversationId, setMessages]
  );

  const sendApprovalResponse = useCallback(
    (requestId: string, approved: boolean) => {
      wsSendApprovalResponse?.(requestId, approved);
    },
    [wsSendApprovalResponse]
  );

  return {
    messages,
    isStreaming,
    streamingContent,
    isConnected,
    pendingApproval,
    send,
    newConversation,
    switchConversation,
    sendApprovalResponse,
  };
}
