import { useLayoutEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../../stores/appStore';

const DEFAULTS = {
  compact: { width: 440, height: 720 },
  full: { width: 700, height: 580 },
};

const STORAGE_KEY = 'winSize';

function loadSize(mode: 'compact' | 'full') {
  try {
    const raw = localStorage.getItem(`${STORAGE_KEY}:${mode}`);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return DEFAULTS[mode];
}

function saveSize(mode: 'compact' | 'full', size: { width: number; height: number }) {
  try {
    localStorage.setItem(`${STORAGE_KEY}:${mode}`, JSON.stringify(size));
  } catch { /* ignore */ }
}

/**
 * Watches layoutMode changes and resizes the Electron window.
 * Remembers user-adjusted sizes per mode via localStorage.
 */
export function LayoutProvider({ children }: { children: React.ReactNode }) {
  const layoutMode = useAppStore((s) => s.layoutMode);
  const prevMode = useRef(layoutMode);

  const switchTo = useCallback(async (mode: 'compact' | 'full') => {
    // Save current size before switching away
    if (prevMode.current && prevMode.current !== mode) {
      try {
        const bounds = await window.electronAPI?.getWindowBounds();
        if (bounds && bounds.width && bounds.height) {
          saveSize(prevMode.current, bounds);
        }
      } catch { /* ignore */ }
    }

    prevMode.current = mode;

    // Restore saved size for target mode, or use default
    const target = loadSize(mode);
    window.electronAPI?.resizeWindow(target.width, target.height);
  }, []);

  useLayoutEffect(() => {
    if (prevMode.current === layoutMode) return;
    switchTo(layoutMode);
  }, [layoutMode, switchTo]);

  return <>{children}</>;
}
