import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';

/**
 * Sends a `daily_greeting` message to the backend whenever:
 * 1. A new character's WebSocket is connected (mount or character switch)
 * 2. The window becomes visible again after being hidden
 *
 * The backend decides whether to actually generate a greeting
 * (per-character last_daily_greeting_date tracking).
 */
export function useDailyGreeting() {
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const pollingRef = useRef(false);

  // Send greeting on mount and when switching characters
  useEffect(() => {
    if (!currentConversationId) return;

    let attempts = 0;
    const MAX = 60;
    pollingRef.current = true;

    const id = setInterval(() => {
      if (!pollingRef.current) {
        clearInterval(id);
        return;
      }

      attempts++;
      const fn = useChatStore.getState().wsSendJson;
      if (!fn) {
        if (attempts >= MAX) {
          clearInterval(id);
          pollingRef.current = false;
          console.log('[DailyGreeting] timeout waiting for ws bridge');
        }
        return;
      }

      const ok = fn({ type: 'daily_greeting' });
      console.log('[DailyGreeting] send attempt', attempts, 'ok:', ok, 'conv:', currentConversationId.slice(0, 8));

      if (ok) {
        clearInterval(id);
        pollingRef.current = false;
      } else if (attempts >= MAX) {
        clearInterval(id);
        pollingRef.current = false;
        console.log('[DailyGreeting] send failed after', MAX, 'attempts');
      }
    }, 1000);

    return () => {
      clearInterval(id);
      pollingRef.current = false;
    };
  }, [currentConversationId]);

  // Window visibility listener — re-send on restore (might be a new day)
  useEffect(() => {
    const api = window.electronAPI;
    if (!api?.onWindowVisibilityChanged) return;

    const handler = (value: { visible: boolean }) => {
      if (!value.visible) return;

      // Don't start a concurrent poll — the conversation-change effect
      // or a prior visibility fire may already be running.
      if (pollingRef.current) {
        console.log('[DailyGreeting] poll already in progress, skipping visibility trigger');
        return;
      }

      let n = 0;
      pollingRef.current = true;
      const id = setInterval(() => {
        n++;
        if (!pollingRef.current) {
          clearInterval(id);
          return;
        }
        const fn = useChatStore.getState().wsSendJson;
        if (fn?.({ type: 'daily_greeting' })) {
          clearInterval(id);
          pollingRef.current = false;
        } else if (n >= 10) {
          clearInterval(id);
          pollingRef.current = false;
        }
      }, 500);
    };

    api.onWindowVisibilityChanged(handler);
    // No cleanup — preload doesn't expose removeListener.
    // The pollingRef guard above prevents concurrent polls even if
    // StrictMode double-mounts and the IPC callback fires twice.
  }, []);
}
