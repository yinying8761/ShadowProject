import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { getTranslation } from '../i18n/translations';
import type { Lang } from '../i18n/translations';
import { friendlyErrorKey } from '../utils/errorMessages';
import { recoverMissingConversation } from '../services/conversationRecovery';
import type { WsMessage } from '../types';
import { wsUrl } from '../utils/wsUrl';

const WS_BASE = wsUrl('/ws/chat');

/**
 * Singleton WebSocket bridge. Must be used exactly once at the App root.
 * Maintains a single WS per conversationId and publishes its send methods
 * via the chat store so other components can trigger sends without each
 * holding their own connection.
 */
export function useWebSocketBridge(conversationId: string | null) {
  const ws = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const closingOnPurpose = useRef(false);
  const [isConnected, setIsConnected] = useState(false);

  const appendStreamingToken = useChatStore((s) => s.appendStreamingToken);
  const finalizeStreamingMessage = useChatStore((s) => s.finalizeStreamingMessage);
  const replaceMessageId = useChatStore((s) => s.replaceMessageId);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setPendingApproval = useChatStore((s) => s.setPendingApproval);
  const pushToolRunning = useChatStore((s) => s.pushToolRunning);
  const finishTool = useChatStore((s) => s.finishTool);
  const setWsBridge = useChatStore((s) => s.setWsBridge);
  const addMemoryNotification = useChatStore((s) => s.addMemoryNotification);
  const bumpConversationList = useChatStore((s) => s.bumpConversationList);
  const setRetryState = useChatStore((s) => s.setRetryState);
  const clearRetryState = useChatStore((s) => s.clearRetryState);
  const setErrorBubble = useChatStore((s) => s.setErrorBubble);
  const setConnected = useAppStore((s) => s.setConnected);
  const proactiveSystemNotification = useAppStore(
    (s) => s.config.proactiveSystemNotification
  );

  useEffect(() => {
    if (!conversationId) {
      setWsBridge(null, null, null);
      return;
    }

    closingOnPurpose.current = false;

    const connect = () => {
      // Close any existing socket and prevent its onclose from reconnecting
      if (ws.current) {
        closingOnPurpose.current = true;
        ws.current.close();
        ws.current = null;
      }
      const url = `${WS_BASE}/${conversationId}`;
      const socket = new WebSocket(url);
      ws.current = socket;

      socket.onopen = () => {
        setIsConnected(true);
        setConnected(true);
        reconnectCount.current = 0;
        closingOnPurpose.current = false; // old socket's onclose has fired by now
      };

      socket.onmessage = (event) => {
        try {
          const data: WsMessage = JSON.parse(event.data);
          switch (data.type) {
            case 'token':
              // Anything after a retry means the retry resolved (issue #39).
              clearRetryState();
              if (data.content) appendStreamingToken(data.content, data.proactive);
              break;
            case 'done':
              clearRetryState();
              if (data.message_id) {
                const isProactiveLike = !!(data.proactive || data.daily_greeting);
                const content = useChatStore.getState().streamingContent;
                finalizeStreamingMessage(data.message_id, isProactiveLike);
                if (data.daily_greeting) {
                  localStorage.setItem('daily_greeting_date', new Date().toISOString().slice(0, 10));
                }
                if (!isProactiveLike) {
                  bumpConversationList();
                }
                if (isProactiveLike) {
                  console.log('[WS] proactive done, calling notifyProactiveReply, content length=', content.length);
                  window.electronAPI?.notifyProactiveReply?.({
                    title: 'AI Companion',
                    body: content || 'You have a new proactive reply.',
                    enabled: proactiveSystemNotification,
                  });
                }
              }
              break;
            case 'tool_use':
              if (data.name) pushToolRunning(data.name);
              break;
            case 'tool_result':
              if (data.name) {
                const denied = (data as { denied?: boolean }).denied;
                const status = denied
                  ? 'denied'
                  : data.is_error
                  ? 'error'
                  : 'success';
                finishTool(data.name, status, data.result);
              }
              break;
            case 'approval_request':
              setPendingApproval({
                requestId: data.request_id || '',
                toolName: data.name || '',
                arguments: data.arguments || {},
              });
              break;
            case 'proactive_skip':
              break;
            case 'daily_greeting_skip':
              // Server confirms greeting already done — mark complete
              localStorage.setItem('daily_greeting_date', new Date().toISOString().slice(0, 10));
              break;
            case 'memory_updated':
              if (data.count && data.count > 0) {
                addMemoryNotification(data.count);
              }
              break;
            case 'message_ack':
              // Server persisted our user message — swap the temporary
              // local-* id for the real UUID (ticket #12).
              if (data.client_message_id && data.message_id) {
                replaceMessageId(data.client_message_id, data.message_id);
              }
              break;
            case 'llm_retry':
              // Transient progress while the server backs off (issue #38/#39).
              // WsRetryMessage guarantees both fields are present.
              setRetryState({
                attempt: data.attempt,
                maxRetries: data.max_retries,
              });
              break;
            case 'error': {
              // Persistent, manually dismissed inline bubble (issue #39).
              // getState() avoids depending on the i18n hook identity, which
              // would re-run this effect (and reconnect) on every render.
              console.error('Server error:', data.message);
              clearRetryState();
              setStreaming(false);

              // The server no longer has this conversation and refuses to
              // persist against it. Recover by opening a fresh conversation;
              // the effect re-runs and reconnects to it. This socket is done:
              // reconnecting would only hit the same dead id again.
              if (data.code === 'conversation_not_found') {
                closingOnPurpose.current = true;
                void recoverMissingConversation(conversationId ?? '').then((recovered) => {
                  if (recovered) return;
                  const l = (useAppStore.getState().config.language as Lang) || 'zh';
                  setErrorBubble({
                    friendly: getTranslation(friendlyErrorKey(data.message), l),
                    raw: data.message || '',
                  });
                });
                break;
              }

              const lang = (useAppStore.getState().config.language as Lang) || 'zh';
              setErrorBubble({
                friendly: getTranslation(friendlyErrorKey(data.message), lang),
                raw: data.message || '',
              });
              break;
            }
          }
        } catch { /* ignore malformed */ }
      };

      socket.onclose = () => {
        setIsConnected(false);
        setConnected(false);
        if (!closingOnPurpose.current && reconnectCount.current < 5) {
          reconnectCount.current++;
          setTimeout(connect, 2000 * reconnectCount.current);
        }
      };

      socket.onerror = () => socket.close();
    };

    const sendMessage = (
      content: string,
      characterId: string,
      opts?: { forceVision?: boolean },
      clientMessageId?: string,
    ) => {
      if (ws.current?.readyState !== WebSocket.OPEN) {
        connect();
        return false;
      }
      const payload: Record<string, unknown> = {
        type: 'chat',
        content,
        character_id: characterId,
      };
      if (opts?.forceVision) payload.force_vision = true;
      // Temporary local id the server echoes back in message_ack so we can
      // replace it with the persisted message's real UUID (ticket #12).
      if (clientMessageId) payload.client_message_id = clientMessageId;
      ws.current.send(JSON.stringify(payload));
      setStreaming(true);
      return true;
    };

    const sendApprovalResponse = (requestId: string, approved: boolean) => {
      if (ws.current?.readyState !== WebSocket.OPEN) return;
      ws.current.send(
        JSON.stringify({ type: 'approval_response', request_id: requestId, approved }),
      );
      setPendingApproval(null);
    };

    const sendJson = (data: Record<string, unknown>) => {
      if (ws.current?.readyState !== WebSocket.OPEN) {
        connect();
        return false;
      }
      ws.current.send(JSON.stringify(data));
      return true;
    };

    setWsBridge(sendMessage, sendApprovalResponse, sendJson);
    connect();

    return () => {
      closingOnPurpose.current = true;
      ws.current?.close();
      ws.current = null;
      setWsBridge(null, null, null);
    };
  }, [
    conversationId,
    appendStreamingToken,
    finalizeStreamingMessage,
    replaceMessageId,
    setStreaming,
    setConnected,
    setPendingApproval,
    pushToolRunning,
    finishTool,
    setWsBridge,
    proactiveSystemNotification,
    bumpConversationList,
    setRetryState,
    clearRetryState,
    setErrorBubble,
  ]);

  return { isConnected };
}
