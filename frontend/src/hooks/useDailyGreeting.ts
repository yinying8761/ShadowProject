import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';

const STORAGE_KEY = 'daily_greeting_date';

function todayStr(): string {
  const now = new Date();
  if (now.getHours() < 4) {
    now.setDate(now.getDate() - 1);
  }
  return now.toISOString().slice(0, 10);
}

/**
 * On mount, poll until the WebSocket bridge is ready and the greeting
 * can be sent.  Only mark localStorage *after* a successful send.
 */
export function useDailyGreeting() {
  const doneRef = useRef(false);

  useEffect(() => {
    // Already sent today — skip
    if (localStorage.getItem(STORAGE_KEY) === todayStr()) return;

    let attempts = 0;
    const MAX = 60;

    const id = setInterval(() => {
      attempts++;
      const fn = useChatStore.getState().wsSendJson;
      if (!fn) {
        if (attempts >= MAX) {
          clearInterval(id);
          console.log('[DailyGreeting] timeout waiting for ws bridge');
        }
        return;
      }

      const ok = fn({ type: 'daily_greeting' });
      console.log('[DailyGreeting] send attempt', attempts, 'ok:', ok);

      if (ok) {
        clearInterval(id);
        localStorage.setItem(STORAGE_KEY, todayStr());
        doneRef.current = true;
        console.log('[DailyGreeting] sent successfully');
      } else if (attempts >= MAX) {
        clearInterval(id);
        console.log('[DailyGreeting] send failed after', MAX, 'attempts');
      }
    }, 1000);

    return () => clearInterval(id);
  }, []); // empty deps — runs once on mount

  // Window visibility listener for hide/show during the day
  useEffect(() => {
    const api = window.electronAPI;
    console.log('[DailyGreeting] preload check: electronAPI =', !!api, 'onVisibility =', !!(api as any)?.onWindowVisibilityChanged);

    if (!api?.onWindowVisibilityChanged) return;

    api.onWindowVisibilityChanged((value: { visible: boolean }) => {
      if (!value.visible) return;
      if (localStorage.getItem(STORAGE_KEY) === todayStr()) return;

      let n = 0;
      const id = setInterval(() => {
        n++;
        const fn = useChatStore.getState().wsSendJson;
        if (fn?.({ type: 'daily_greeting' })) {
          clearInterval(id);
          localStorage.setItem(STORAGE_KEY, todayStr());
        } else if (n >= 10) {
          clearInterval(id);
        }
      }, 500);
    });
  }, []);
}
