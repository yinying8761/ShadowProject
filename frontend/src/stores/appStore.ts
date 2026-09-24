import { create } from 'zustand';
import { characterAvatarUrl } from '../utils/avatarUrl';
import type { CharacterProfile, AppConfig, GroupInfo, LayoutMode } from '../types';

interface AppState {
  activeCharacter: CharacterProfile | null;
  characters: CharacterProfile[];
  config: AppConfig;
  isConnected: boolean;
  showSettings: boolean;
  showHistory: boolean;
  showDebugConsole: boolean;
  showCharacterEditor: boolean;
  editingCharacter: CharacterProfile | null;
  layoutMode: LayoutMode;
  /** 当前所在的群聊；非空即"在群里"（群聊强制完整模式，退出恢复进入前的模式）。 */
  activeGroup: GroupInfo | null;
  layoutModeBeforeGroup: LayoutMode | null;

  setActiveCharacter: (char: CharacterProfile) => void;
  setCharacters: (chars: CharacterProfile[]) => void;
  setConfig: (config: Partial<AppConfig>) => void;
  setConnected: (connected: boolean) => void;
  setShowSettings: (show: boolean) => void;
  setShowHistory: (show: boolean) => void;
  setShowDebugConsole: (show: boolean) => void;
  openCharacterEditor: (char?: CharacterProfile | null) => void;
  closeCharacterEditor: () => void;
  setLayoutMode: (mode: LayoutMode) => void;
  enterGroup: (group: GroupInfo) => void;
  leaveGroup: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeCharacter: null,
  characters: [],
  config: {
    theme: 'dark',
    alwaysOnTop: true,
    fontSize: 14,
    llmProvider: 'deepseek',
    llmModel: 'deepseek-chat',
    hasApiKey: false,
    showFloatingIcon: true,
    floatingIconX: -1,
    floatingIconY: -1,
    proactiveChatLevel: 'medium',
    proactiveSilentToolApproval: false,
    proactiveAutoSeeScreen: true,
    proactiveDailyLimit: 10,
    proactiveFixedScheduleEnabled: true,
    proactiveSystemNotification: true,
    language: 'zh',
    ttsEnabled: true,
    lastCharacterId: '',
  },
  isConnected: false,
  showSettings: false,
  showHistory: false,
  showDebugConsole: false,
  showCharacterEditor: false,
  editingCharacter: null,
  layoutMode: 'compact',
  activeGroup: null,
  layoutModeBeforeGroup: null,

  setActiveCharacter: (char) => {
    set({ activeCharacter: char });
    // Persist last selected character
    fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ last_character_id: char.id }),
    }).catch(() => {});
    const avatarUrl = characterAvatarUrl(char.avatar_path) ?? undefined;
    window.electronAPI?.setFloatingAvatar?.({
      url: avatarUrl,
      initial: char.name?.slice(0, 1)?.toUpperCase() || 'AI',
    });
    window.electronAPI?.refreshFloatingAvatar();
  },
  setCharacters: (chars) => set({ characters: chars }),
  setConfig: (config) =>
    set((state) => ({ config: { ...state.config, ...config } })),
  setConnected: (connected) => set({ isConnected: connected }),
  setShowSettings: (show) => set({ showSettings: show }),
  setShowHistory: (show) => set({ showHistory: show }),
  setShowDebugConsole: (show) => set({ showDebugConsole: show }),
  openCharacterEditor: (char) =>
    set({ showCharacterEditor: true, editingCharacter: char || null }),
  closeCharacterEditor: () =>
    set({ showCharacterEditor: false, editingCharacter: null }),
  // 群聊强制完整模式：紧凑模式没有群聊界面，所以谁打电话来都改不动。
  setLayoutMode: (mode) =>
    set((state) => ({ layoutMode: state.activeGroup ? 'full' : mode })),

  // 进群：关掉设置面板、切完整模式，并记住进来之前的模式（退出时恢复）。
  enterGroup: (group) =>
    set((state) => ({
      activeGroup: group,
      // 已经在群里就**别覆盖**：群→群切换后再退出，要回到最初进来前的模式
      layoutModeBeforeGroup: state.activeGroup ? state.layoutModeBeforeGroup : state.layoutMode,
      layoutMode: 'full',
      showSettings: false,
      showHistory: false,
    })),
  leaveGroup: () =>
    set((state) => ({
      activeGroup: null,
      layoutMode: state.layoutModeBeforeGroup ?? state.layoutMode,
      layoutModeBeforeGroup: null,
    })),
}));
