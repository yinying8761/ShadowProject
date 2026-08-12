import { useEffect } from 'react';
import { useAppStore } from '../stores/appStore';
import { useChatStore } from '../stores/chatStore';
import { useChat } from './useChat';

/**
 * Global keyboard shortcuts.
 * Attach once at App root.
 */
export function useKeyboardShortcuts() {
  const layoutMode = useAppStore((s) => s.layoutMode);
  const setLayoutMode = useAppStore((s) => s.setLayoutMode);
  const showSettings = useAppStore((s) => s.showSettings);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const showHistory = useAppStore((s) => s.showHistory);
  const setShowHistory = useAppStore((s) => s.setShowHistory);
  const showMemoryViewer = useChatStore((s) => s.showMemoryViewer);
  const setShowMemoryViewer = useChatStore((s) => s.setShowMemoryViewer);
  const showCharacterEditor = useAppStore((s) => s.showCharacterEditor);
  const closeCharacterEditor = useAppStore((s) => s.closeCharacterEditor);
  const { newConversation } = useChat();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't intercept when user is typing in an input/textarea
      const tag = (e.target as HTMLElement)?.tagName;
      const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

      // Escape: close topmost overlay
      if (e.key === 'Escape') {
        if (showMemoryViewer) { setShowMemoryViewer(false); return; }
        if (showCharacterEditor) { closeCharacterEditor(); return; }
        if (showHistory) { setShowHistory(false); return; }
        if (showSettings) { setShowSettings(false); return; }
        return;
      }

      if (isInput) return; // allow normal typing

      // Ctrl+Shift+F: toggle compact/full
      if (e.key === 'F' && e.ctrlKey && e.shiftKey) {
        e.preventDefault();
        setLayoutMode(layoutMode === 'compact' ? 'full' : 'compact');
        return;
      }

      // Ctrl+N: new conversation
      if (e.key === 'n' && e.ctrlKey) {
        e.preventDefault();
        newConversation();
        return;
      }

      // Ctrl+,: toggle settings
      if (e.key === ',' && e.ctrlKey) {
        e.preventDefault();
        setShowSettings(!showSettings);
        return;
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [
    layoutMode, setLayoutMode,
    showSettings, setShowSettings,
    showHistory, setShowHistory,
    showMemoryViewer, setShowMemoryViewer,
    showCharacterEditor, closeCharacterEditor,
    newConversation,
  ]);
}
