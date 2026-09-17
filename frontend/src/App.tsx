import { useEffect } from 'react';
import { TitleBar } from './components/shell/TitleBar';
import { LayoutProvider } from './components/shell/LayoutProvider';
import { CompactView } from './components/shell/CompactView';
import { FullView } from './components/shell/FullView';
import { CharacterEditor } from './components/character/CharacterEditor';
import { HistoryOverlay } from './components/chat/HistoryOverlay';
import { ApprovalDialog } from './components/chat/ApprovalDialog';
import { SettingsPanel } from './components/settings/SettingsPanel';
import { MemoryViewer } from './components/settings/MemoryViewer';
import { DebugConsole } from './components/debug/DebugConsole';
import { useCharacters } from './hooks/useCharacters';
import { useAppStore } from './stores/appStore';
import { useChatStore } from './stores/chatStore';
import { useWebSocketBridge } from './hooks/useWebSocket';
import { useDailyGreeting } from './hooks/useDailyGreeting';
import { useGeolocation } from './hooks/useGeolocation';
import { useTTS } from './hooks/useTTS';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { api } from './services/api';
import { installRendererErrorCapture } from './utils/rendererErrorCapture';

export default function App() {
  useCharacters();
  const setConfig = useAppStore((s) => s.setConfig);
  const showCharacterEditor = useAppStore((s) => s.showCharacterEditor);
  const editingCharacter = useAppStore((s) => s.editingCharacter);
  const closeCharacterEditor = useAppStore((s) => s.closeCharacterEditor);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const layoutMode = useAppStore((s) => s.layoutMode);

  // Single global WebSocket connection — must live at the App root only.
  useWebSocketBridge(currentConversationId);
  useDailyGreeting();
  useGeolocation();
  useTTS();
  useKeyboardShortcuts();

  useEffect(() => {
    api.fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        window.electronAPI?.setAlwaysOnTop(cfg.alwaysOnTop);
      })
      .catch(console.error);
  }, [setConfig]);

  // Frontend errors are captured for the whole session; the debug console's
  // socket replays them when it connects.
  useEffect(() => {
    installRendererErrorCapture();
  }, []);

  return (
    <LayoutProvider>
      <div className="flex flex-col h-screen w-screen bg-transparent overflow-hidden layout-transition">
        <TitleBar />

        {/* Mode-switched main content */}
        {layoutMode === 'compact' ? <CompactView /> : <FullView />}

        {/* Overlays — shared across modes */}
        <ApprovalDialog />
        <HistoryOverlay />
        <SettingsPanel />
        <MemoryViewer />
        <DebugConsole />
        {showCharacterEditor && (
          <CharacterEditor
            character={editingCharacter || undefined}
            onClose={closeCharacterEditor}
          />
        )}
      </div>
    </LayoutProvider>
  );
}
