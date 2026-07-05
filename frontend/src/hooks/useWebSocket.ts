import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import type { WsMessage } from '../types';

const WS_BASE = `ws://${window.location.hostname}:8722/ws/chat`;

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
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setPendingApproval = useChatStore((s) => s.setPendingApproval);
  const pushToolRunning = useChatStore((s) => s.pushToolRunning);
  const finishTool = useChatStore((s) => s.finishTool);
  const setWsBridge = useChatStore((s) => s.setWsBridge);
  const addMemoryNotification = useChatStore((s) => s.addMemoryNotification);
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
      // Close any existing socket (handles StrictMode double-mount)
      if (ws.current) {
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
      };

      socket.onmessage = (event) => {
        try {
          const data: WsMessage = JSON.parse(event.data);
          switch (data.type) {
            case 'token':
              if (data.content) appendStreamingToken(data.content, data.proactive);
              break;
            case 'done':
              if (data.message_id) {
                const isProactiveLike = !!(data.proactive || data.daily_greeting);
                const content = useChatStore.getState().streamingContent;
                finalizeStreamingMessage(data.message_id, isProactiveLike);
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
            case 'daily_greeting_skip':
              // Model decided not to speak, or greeting already done today
              break;
            case 'memory_updated':
              if (data.count && data.count > 0) {
                addMemoryNotification(data.count);
              }
              break;
            case 'error':
              console.error('Server error:', data.message);
              setStreaming(false);
              break;
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
    setStreaming,
    setConnected,
    setPendingApproval,
    pushToolRunning,
    finishTool,
    setWsBridge,
    proactiveSystemNotification,
  ]);

  return { isConnected };
}
