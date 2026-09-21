import { useCallback, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { api } from '../services/api';
import type { ApiMessage } from '../types';

// Module-level guard: prevents re-initialization when useChat is called
// from multiple mount points (e.g. InputBar inside CompactView + FullView).
// Without this, mode-switching kills the WebSocket because a new useChat
// instance resets currentConversationId to ''.
const _lastInitCharId = { current: null as string | null };

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
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const isConnected = useAppStore((s) => s.isConnected);
  const epoch = useRef(0);

  useEffect(() => {
    if (!activeCharacter) return;
    // Guard: skip if already initialized for this character (mode switch)
    if (activeCharacter.id === _lastInitCharId.current) return;
    _lastInitCharId.current = activeCharacter.id;

    const gen = ++epoch.current; // bump generation to discard stale results
    clearMessages();             // immediately clear old character's messages
    setConversationId('');
    setMessages([]);

    (async () => {
      try {
        const convs = await api.fetchConversations(activeCharacter.id);
        if (gen !== epoch.current) return; // stale

        if (convs.length > 0) {
          const convId = convs[0].id;
          const msgs = await api.fetchMessages(convId);
          if (gen !== epoch.current) return; // stale

          setConversationId(convId);
          setMessages(
            msgs.map((m: ApiMessage) => ({
              id: m.id,
              conversationId: convId,
              role: m.role,
              content: m.content,
              createdAt: m.created_at,
              transcript: m.transcript ?? null,
            }))
          );
        } else {
          const conv = await api.createConversation(activeCharacter.id);
          if (gen !== epoch.current) return; // stale

          setConversationId(conv.id);
          setMessages([]);
        }
      } catch (e) {
        console.error('Failed to load conversation:', e);
      }
    })();
  }, [activeCharacter]);

  const send = useCallback(
    async (text: string, opts?: { forceVision?: boolean }) => {
      if (!text.trim() || !activeCharacter || !currentConversationId) return;
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
      const sent = wsSendMessage?.(text, activeCharacter.id, opts, userMsg.id) ?? false;
      if (!sent) {
        try {
          const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: text,
              conversation_id: currentConversationId,
              character_id: activeCharacter.id,
              client_message_id: userMsg.id,
            }),
          });
          const data = await res.json();
          if (data.user_message_id) {
            replaceMessageId(userMsg.id, data.user_message_id);
          }
          addMessage({
            id: data.message_id || `resp-${Date.now()}`,
            conversationId: currentConversationId,
            role: 'assistant',
            content: data.content || '',
            createdAt: new Date().toISOString(),
          });
        } catch (e) {
          console.error('Failed to send message:', e);
        }
      }
    },
    [activeCharacter, currentConversationId, addMessage, replaceMessageId, wsSendMessage]
  );

  const newConversation = useCallback(async () => {
    if (!activeCharacter) return;
    clearMessages();
    try {
      const conv = await api.createConversation(activeCharacter.id);
      setConversationId(conv.id);
    } catch (e) {
      console.error('Failed to create conversation:', e);
    }
  }, [activeCharacter, clearMessages, setConversationId]);

  const switchConversation = useCallback(
    async (convId: string) => {
      if (!convId || convId === currentConversationId) return;
      clearMessages();
      try {
        const msgs = await api.fetchMessages(convId);
        setConversationId(convId);
        setMessages(
          msgs.map((m: ApiMessage) => ({
            id: m.id,
            conversationId: convId,
            role: m.role,
            content: m.content,
            createdAt: m.created_at,
            transcript: m.transcript ?? null,
          }))
        );
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
