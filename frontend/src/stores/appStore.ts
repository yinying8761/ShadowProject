import { create } from 'zustand';
import type { CharacterProfile, AppConfig } from '../types';

interface AppState {
  activeCharacter: CharacterProfile | null;
  characters: CharacterProfile[];
  config: AppConfig;
  isConnected: boolean;
  showSettings: boolean;
  showHistory: boolean;
  showCharacterEditor: boolean;
  editingCharacter: CharacterProfile | null;

  setActiveCharacter: (char: CharacterProfile) => void;
  setCharacters: (chars: CharacterProfile[]) => void;
  setConfig: (config: Partial<AppConfig>) => void;
  setConnected: (connected: boolean) => void;
  setShowSettings: (show: boolean) => void;
  setShowHistory: (show: boolean) => void;
  openCharacterEditor: (char?: CharacterProfile | null) => void;
  closeCharacterEditor: () => void;
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
  },
  isConnected: false,
  showSettings: false,
  showHistory: false,
  showCharacterEditor: false,
  editingCharacter: null,

  setActiveCharacter: (char) => {
    set({ activeCharacter: char });
    const avatarUrl = char.avatar_path
      ? `http://localhost:8722/data/${char.avatar_path}`
      : undefined;
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
  openCharacterEditor: (char) =>
    set({ showCharacterEditor: true, editingCharacter: char || null }),
  closeCharacterEditor: () =>
    set({ showCharacterEditor: false, editingCharacter: null }),
}));
