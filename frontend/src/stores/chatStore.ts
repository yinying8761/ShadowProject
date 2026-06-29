import { create } from 'zustand';
import type { Message, ApprovalRequest } from '../types';

export interface ToolActivity {
  id: string;
  name: string;
  status: 'running' | 'success' | 'error' | 'denied';
  result?: string;
  startedAt: number;
}

interface ChatState {
  messages: Message[];
  currentConversationId: string | null;
  isStreaming: boolean;
  streamingContent: string;
  streamingIsProactive: boolean;
  pendingApproval: ApprovalRequest | null;
  recentTools: ToolActivity[];
  memoryNotificationCount: number;
  showMemoryViewer: boolean;
  // WebSocket bridge — populated by the singleton connection in App.
  wsSendMessage: ((content: string, characterId: string, opts?: { forceVision?: boolean }) => boolean) | null;
  wsSendApprovalResponse: ((requestId: string, approved: boolean) => void) | null;
  wsSendJson: ((data: Record<string, unknown>) => boolean) | null;

  addMessage: (msg: Message) => void;
  setMessages: (msgs: Message[]) => void;
  appendStreamingToken: (token: string, isProactive?: boolean) => void;
  finalizeStreamingMessage: (msgId: string, isProactive?: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  setConversationId: (id: string) => void;
  clearMessages: () => void;
  setPendingApproval: (req: ApprovalRequest | null) => void;
  pushToolRunning: (name: string) => string;
  finishTool: (name: string, status: 'success' | 'error' | 'denied', result?: string) => void;
  clearOldTools: () => void;
  setWsBridge: (
    send: ((content: string, characterId: string, opts?: { forceVision?: boolean }) => boolean) | null,
    sendApproval: ((requestId: string, approved: boolean) => void) | null,
    sendJson: ((data: Record<string, unknown>) => boolean) | null,
  ) => void;
  addMemoryNotification: (count: number) => void;
  dismissMemoryNotification: () => void;
  setShowMemoryViewer: (show: boolean) => void;
}

const TOOL_RETENTION_MS = 15000;

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  currentConversationId: null,
  isStreaming: false,
  streamingContent: '',
  streamingIsProactive: false,
  pendingApproval: null,
  recentTools: [],
  memoryNotificationCount: 0,
  showMemoryViewer: false,
  wsSendMessage: null,
  wsSendApprovalResponse: null,
  wsSendJson: null,

  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),

  setMessages: (msgs) => set({ messages: msgs }),

  appendStreamingToken: (token, isProactive) =>
    set((state) => ({
      streamingContent: state.streamingContent + token,
      isStreaming: true,
      streamingIsProactive: isProactive ?? state.streamingIsProactive,
    })),

  finalizeStreamingMessage: (msgId, isProactive) =>
    set((state) => {
      const msg: Message = {
        id: msgId,
        conversationId: state.currentConversationId || '',
        role: 'assistant',
        content: state.streamingContent,
        createdAt: new Date().toISOString(),
        isProactive: isProactive ?? state.streamingIsProactive,
      };
      return {
        messages: [...state.messages, msg],
        streamingContent: '',
        isStreaming: false,
        streamingIsProactive: false,
      };
    }),

  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setConversationId: (id) => set({ currentConversationId: id }),
  clearMessages: () =>
    set({
      messages: [],
      streamingContent: '',
      isStreaming: false,
      streamingIsProactive: false,
      recentTools: [],
    }),
  setPendingApproval: (req) => set({ pendingApproval: req }),

  pushToolRunning: (name) => {
    const id = `tool-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((state) => ({
      recentTools: [
        ...state.recentTools,
        { id, name, status: 'running', startedAt: Date.now() },
      ],
    }));
    return id;
  },

  finishTool: (name, status, result) =>
    set((state) => {
      // Find most-recent running entry with matching name and update it
      const idx = [...state.recentTools]
        .reverse()
        .findIndex((t) => t.name === name && t.status === 'running');
      if (idx === -1) {
        return {
          recentTools: [
            ...state.recentTools,
            {
              id: `tool-${Date.now()}`,
              name,
              status,
              result,
              startedAt: Date.now(),
            },
          ],
        };
      }
      const realIdx = state.recentTools.length - 1 - idx;
      const next = [...state.recentTools];
      next[realIdx] = { ...next[realIdx], status, result };
      return { recentTools: next };
    }),

  clearOldTools: () =>
    set((state) => ({
      recentTools: state.recentTools.filter(
        (t) => Date.now() - t.startedAt < TOOL_RETENTION_MS || t.status === 'running'
      ),
    })),

  setWsBridge: (send, sendApproval, sendJson) =>
    set({ wsSendMessage: send, wsSendApprovalResponse: sendApproval, wsSendJson: sendJson }),

  addMemoryNotification: (count) =>
    set((state) => ({
      memoryNotificationCount: state.memoryNotificationCount + count,
    })),

  dismissMemoryNotification: () =>
    set({ memoryNotificationCount: 0 }),

  setShowMemoryViewer: (show) =>
    set({ showMemoryViewer: show }),
}));
