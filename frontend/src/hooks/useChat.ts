import { useCallback, useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { api } from '../services/api';
import type { ApiMessage } from '../types';

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
  const clearMessages = useChatStore((s) => s.clearMessages);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const isConnected = useAppStore((s) => s.isConnected);
  const epoch = useRef(0);

  useEffect(() => {
    if (!activeCharacter) return;

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
        id: `local-${Date.now()}`,
        conversationId: currentConversationId,
        role: 'user' as const,
        content: text,
        createdAt: new Date().toISOString(),
      };
      addMessage(userMsg);
      const sent = wsSendMessage?.(text, activeCharacter.id, opts) ?? false;
      if (!sent) {
        try {
          const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: text,
              conversation_id: currentConversationId,
              character_id: activeCharacter.id,
            }),
          });
          const data = await res.json();
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
    [activeCharacter, currentConversationId, addMessage, wsSendMessage]
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
    sendApprovalResponse,
  };
}
