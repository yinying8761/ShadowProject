import { create } from 'zustand';
import type { Message, ApprovalRequest } from '../types';
import { insertBySequence } from '../utils/messageOrder';

export interface ToolActivity {
  id: string;
  name: string;
  status: 'running' | 'success' | 'error' | 'denied';
  result?: string;
  startedAt: number;
}

/** Transient LLM-retry progress (Workflow I). Cleared by any later event. */
export interface RetryState {
  attempt: number;
  maxRetries: number;
}

/** Persistent, manually dismissed inline error (Workflow I). */
export interface ErrorBubbleState {
  friendly: string;
  raw: string;
}

type WsSendMessage = (
  content: string,
  characterId: string,
  opts?: { forceVision?: boolean },
  clientMessageId?: string,
) => boolean;

interface ChatState {
  messages: Message[];
  currentConversationId: string | null;
  isStreaming: boolean;
  streamingContent: string;
  streamingIsProactive: boolean;
  /** 群聊里"正在思考"的成员（顺序 = 亮起的先后）。并发时会有好几个（ticket #11）。 */
  groupThinking: string[];
  pendingApproval: ApprovalRequest | null;
  recentTools: ToolActivity[];
  memoryNotificationCount: number;
  showMemoryViewer: boolean;
  speakMessage: ((content: string) => void) | null;
  conversationListVersion: number;
  bumpConversationList: () => void;
  retryState: RetryState | null;
  errorBubble: ErrorBubbleState | null;
  stopSpeaking: (() => void) | null;
  // WebSocket bridge — populated by the singleton connection in App.
  wsSendMessage: WsSendMessage | null;
  wsSendApprovalResponse: ((requestId: string, approved: boolean) => void) | null;
  wsSendJson: ((data: Record<string, unknown>) => boolean) | null;

  addMessage: (msg: Message) => void;
  /** 群聊：按 `seq` 插到正确位置（并发下到达顺序 ≠ 发言顺序，ticket #11） */
  addGroupMessage: (msg: Message) => void;
  /** 群聊：某成员开始/结束思考 —— 每个成员一个独立的"正在输入" */
  setGroupThinking: (characterId: string, thinking: boolean) => void;
  clearGroupThinking: () => void;
  removeMessage: (msgId: string) => void;
  replaceMessageId: (clientMessageId: string, serverId: string, seq?: number | null) => void;
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
    send: WsSendMessage | null,
    sendApproval: ((requestId: string, approved: boolean) => void) | null,
    sendJson: ((data: Record<string, unknown>) => boolean) | null,
  ) => void;
  addMemoryNotification: (count: number) => void;
  dismissMemoryNotification: () => void;
  setShowMemoryViewer: (show: boolean) => void;
  setRetryState: (state: RetryState) => void;
  clearRetryState: () => void;
  setErrorBubble: (bubble: ErrorBubbleState) => void;
  dismissErrorBubble: () => void;
}

const TOOL_RETENTION_MS = 15000;

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  currentConversationId: null,
  isStreaming: false,
  streamingContent: '',
  streamingIsProactive: false,
  groupThinking: [],
  pendingApproval: null,
  recentTools: [],
  memoryNotificationCount: 0,
  showMemoryViewer: false,
  speakMessage: null,
  conversationListVersion: 0,
  retryState: null,
  errorBubble: null,
  stopSpeaking: null,
  wsSendMessage: null,
  wsSendApprovalResponse: null,
  wsSendJson: null,

  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),

  // 群聊的一条消息：按序号插到正确位置，而不是追加。并发下几个成员同时生成，
  // 到达顺序与发言顺序可能不一致（重连重播、历史加载交叉都会放大它）。
  addGroupMessage: (msg) =>
    set((state) => ({ messages: insertBySequence(state.messages, msg) })),

  // 幂等：消息落库和 thinking:false 都会收掉指示器，先后到都不该出错。
  setGroupThinking: (characterId, thinking) =>
    set((state) => {
      const known = state.groupThinking.includes(characterId);
      if (thinking === known) return {};
      return {
        groupThinking: thinking
          ? [...state.groupThinking, characterId]
          : state.groupThinking.filter((id) => id !== characterId),
      };
    }),

  clearGroupThinking: () =>
    set((state) => (state.groupThinking.length === 0 ? {} : { groupThinking: [] })),

  removeMessage: (msgId) =>
    set((state) => ({
      messages: state.messages.filter((m) => m.id !== msgId),
    })),

  // Swap a client-side temporary id for the server-persisted UUID in place
  // (issue #10, tickets #12/#13). No-ops silently when there is no match
  // (stale ack) or the server id already exists (ack replayed after the
  // conversation was reloaded).
  //
  // `seq` comes from `message_ack` (ticket #10). Stamping it is not enough:
  // the optimistic message was *appended*, and a reply that was persisted
  // before it (an interjection racing an in-flight member) may already sit
  // after it — so once the real sequence is known the message is re-placed by
  // it (ticket #11: "渲染与追加一律以序号为准").
  replaceMessageId: (clientMessageId, serverId, seq) =>
    set((state) => {
      if (state.messages.some((m) => m.id === serverId)) return {};
      const target = state.messages.find((m) => m.id === clientMessageId);
      if (!target) return {};
      const renamed: Message = {
        ...target,
        id: serverId,
        seq: seq ?? target.seq ?? null,
      };
      const rest = state.messages.filter((m) => m.id !== clientMessageId);
      return { messages: insertBySequence(rest, renamed) };
    }),

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
      // 换会话（尤其离开群聊）时把"正在输入"一起收掉，否则会挂在新会话上
      groupThinking: [],
      recentTools: [],
      // Retry progress and error bubbles are in-memory only: switching
      // conversations must not leak the previous turn's errors (issue #39).
      retryState: null,
      errorBubble: null,
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

  bumpConversationList: () =>
    set((state) => ({
      conversationListVersion: state.conversationListVersion + 1,
    })),

  setRetryState: (state) => set({ retryState: state }),

  // Returns the current state when there is nothing to clear so the common
  // case (every streamed token calls this) skips a store notification.
  clearRetryState: () =>
    set((state) => (state.retryState === null ? state : { retryState: null })),

  setErrorBubble: (bubble) => set({ errorBubble: bubble }),

  dismissErrorBubble: () => set({ errorBubble: null }),
}));
