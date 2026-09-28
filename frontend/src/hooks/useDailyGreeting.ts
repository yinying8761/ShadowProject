import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';

/**
 * Sends a `daily_greeting` message to the backend whenever:
 * 1. A new character's WebSocket is connected (mount or character switch)
 * 2. The window becomes visible again after being hidden
 *
 * The backend decides whether to actually generate a greeting
 * (per-character last_daily_greeting_date tracking).
 *
 * 群聊里**根本不发**：spec 明说群聊禁用每日问候，服务端虽然会拒（回
 * `daily_greeting_skip{reason:"group_conversation"}`），但没必要白跑一趟往返。
 */
export function useDailyGreeting() {
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const inGroup = useAppStore((s) => s.activeGroup !== null);
  const pollingRef = useRef(false);

  // Send greeting on mount and when switching characters
  useEffect(() => {
    if (!currentConversationId || inGroup) return;

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
      // 群聊里不发（读实时状态：这个 effect 只挂一次，不能闭包捕获 inGroup）
      if (useAppStore.getState().activeGroup) return;

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
