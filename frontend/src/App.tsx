import { useEffect } from 'react';
import { TitleBar } from './components/shell/TitleBar';
import { CharacterDisplay } from './components/character/CharacterDisplay';
import { CharacterEditor } from './components/character/CharacterEditor';
import { DialogueBox } from './components/chat/DialogueBox';
import { InputBar } from './components/chat/InputBar';
import { HistoryOverlay } from './components/chat/HistoryOverlay';
import { ApprovalDialog } from './components/chat/ApprovalDialog';
import { ToolStatusStrip } from './components/chat/ToolStatusStrip';
import { SettingsPanel } from './components/settings/SettingsPanel';
import { MemoryViewer } from './components/settings/MemoryViewer';
import { useCharacters } from './hooks/useCharacters';
import { useAppStore } from './stores/appStore';
import { useChatStore } from './stores/chatStore';
import { useWebSocketBridge } from './hooks/useWebSocket';
import { api } from './services/api';

export default function App() {
  useCharacters();
  const setConfig = useAppStore((s) => s.setConfig);
  const showCharacterEditor = useAppStore((s) => s.showCharacterEditor);
  const editingCharacter = useAppStore((s) => s.editingCharacter);
  const closeCharacterEditor = useAppStore((s) => s.closeCharacterEditor);
  const currentConversationId = useChatStore((s) => s.currentConversationId);

  // Single global WebSocket connection — must live at the App root only.
  useWebSocketBridge(currentConversationId);

  useEffect(() => {
    api.fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        window.electronAPI?.setAlwaysOnTop(cfg.alwaysOnTop);
      })
      .catch(console.error);
  }, [setConfig]);

  return (
    <div className="flex flex-col h-screen w-screen bg-transparent overflow-hidden">
      <TitleBar />

      {/* Portrait fills remaining vertical space */}
      <CharacterDisplay />

      {/* Bottom dialogue + input stack */}
      <div className="flex-shrink-0 pb-1">
        <DialogueBox />
        <ToolStatusStrip />
        <InputBar />
      </div>

      {/* Overlays */}
      <ApprovalDialog />
      <HistoryOverlay />
      <SettingsPanel />
      <MemoryViewer />
      {showCharacterEditor && (
        <CharacterEditor
          character={editingCharacter || undefined}
          onClose={closeCharacterEditor}
        />
      )}
    </div>
  );
}
