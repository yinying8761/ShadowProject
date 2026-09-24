export interface CharacterProfile {
  id: string;
  name: string;
  gender?: string;
  personality: string;
  role: string;
  archetype: string;
  voice_style?: string;
  avatar_path?: string;
  tts_ref_audio?: string;
  tts_prompt_text?: string;
  created_at?: string;
}

export interface Message {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  toolCalls?: ToolCall[];
  createdAt: string;
  isProactive?: boolean;
  /** 时间戳对话记录行（历史视图展示；WS 在途/流式消息没有，由历史接口补齐） */
  transcript?: string | null;
  /** 群聊：这条消息由哪个角色说出（1:1 为 null —— 说话人由会话角色派生） */
  speakerId?: string | null;
  /** 群聊：说话人显示名（历史接口已按消息自己的 speaker_id 解析好） */
  speakerName?: string | null;
}

export interface ApiMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  tool_calls?: ToolCall[];
  created_at: string;
  /** 该条消息的发言角色（群聊用；1:1 为空） */
  speaker_id?: string | null;
  /** 解析后的说话人名（用户名/角色名） */
  speaker?: string | null;
  /** 共享渲染器输出的 `时间 [说话人]: 内容` 行（tool 消息为 null） */
  transcript?: string | null;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  isError?: boolean;
}

export interface Conversation {
  id: string;
  characterId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

/** 布局模式：compact = 桌面陪伴窗口，full = 完整聊天面板。 */
export type LayoutMode = 'compact' | 'full';

/** 群：固定成员集合 + 共享对话流（api/group.py 的返回形状）。 */
export interface GroupMemberInfo {
  character_id: string;
  character_name: string | null;
  position: number;
}

export interface GroupConversationInfo {
  id: string;
  title: string;
  updated_at: string | null;
}

export interface GroupInfo {
  id: string;
  name: string;
  created_at: string | null;
  /** 数组成员顺序 = 发言顺序 */
  members: GroupMemberInfo[];
  conversations: GroupConversationInfo[];
}

export type ProactiveLevel = 'off' | 'low' | 'medium' | 'high';

export interface AppConfig {
  theme: string;
  alwaysOnTop: boolean;
  fontSize: number;
  llmProvider: string;
  llmModel: string;
  hasApiKey: boolean;
  apiKeyHint?: string;
  showFloatingIcon: boolean;
  floatingIconX: number;
  floatingIconY: number;
  proactiveChatLevel: ProactiveLevel;
  proactiveSilentToolApproval: boolean;
  proactiveAutoSeeScreen: boolean;
  proactiveDailyLimit: number;
  proactiveFixedScheduleEnabled: boolean;
  proactiveSystemNotification: boolean;
  language: string;
  ttsEnabled: boolean;
  lastCharacterId: string;
}

export interface ApprovalRequest {
  requestId: string;
  toolName: string;
  arguments: Record<string, unknown>;
}

export interface UserProfile {
  id: string;
  character_id: string | null;
  user_name: string;
  user_gender: string | null;
  user_occupation: string | null;
  user_bio: string | null;
  user_relationship: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryEntry {
  id: string;
  content: string;
  memory_type: string;
  importance: number;
  access_count: number;
  created_at: string | null;
  updated_at: string | null;
  last_accessed_at: string | null;
  source_conversation_id: string | null;
}

export interface WsBaseMessage {
  type:
    | 'token'
    | 'tool_use'
    | 'tool_result'
    | 'done'
    | 'error'
    | 'approval_request'
    | 'proactive_skip'
    | 'memory_updated'
    | 'daily_greeting_skip'
    | 'message_ack'
    | 'group_message';
  content?: string;
  message_id?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: string;
  is_error?: boolean;
  message?: string;
  request_id?: string;
  proactive?: boolean;
  daily_greeting?: boolean;
  count?: number;
  reason?: string;
  client_message_id?: string;
  /** Machine-readable error kind, e.g. `conversation_not_found` (api/chat.py). */
  code?: string;
  /** Group chat: which member said it (services/group_chat.py `group_message`). */
  character_id?: string;
  /** Group chat: that member's display name, resolved server-side. */
  speaker?: string;
  /** Group chat: marks the turn-burst boundary (every group reply carries none). */
  group?: boolean;
}

/**
 * llm_retry variant (ticket #38): both fields are required — the server
 * always sends them together with the llm_retry type.
 */
export interface WsRetryMessage {
  type: 'llm_retry';
  /** 1-based number of the upcoming retry. */
  attempt: number;
  /** Total retries configured for this attempt. */
  max_retries: number;
}

export type WsMessage = WsBaseMessage | WsRetryMessage;
/** Debug-console log protocol (/ws/logs, ADR-0002). */
export type LogSource = 'backend' | 'renderer' | 'cmd';
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

/** One log line, exactly as the backend hub stamps it. */
export interface LogLine {
  source: LogSource;
  level: LogLevel;
  message: string;
  /** Epoch seconds. */
  ts: number;
}

export type LogsInboundMessage =
  | { type: 'logs_history'; lines: LogLine[] }
  | { type: 'logs_line'; line: LogLine };

/** What the panel sends up the same channel. */
export type LogsOutboundMessage =
  | { type: 'renderer_log'; level: LogLevel; message: string }
  | { type: 'command'; command: string };

export interface TokenUsageEntry {
  id: string;
  conversation_id: string;
  round_num: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_prompt_tokens: number;
  created_at: string | null;
}

export interface TokenUsageSummary {
  rounds: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface TokenUsageResponse {
  total: number;
  usage: TokenUsageEntry[];
  summary: TokenUsageSummary;
}

export interface ProviderPreset {
  id: string;
  name: string;
  base_url?: string;
  default_model?: string | null;
  sdk_type?: string;
  is_custom?: boolean;
}

export interface LlmModelsResponse {
  models?: string[];
  error?: string;
}

export interface LlmTestResponse {
  ok: boolean;
  latency_ms?: number;
  error?: string;
}

export interface CustomProvider {
  id: string;
  name: string;
  base_url: string;
}

export interface LlmConfigUpdate {
  llm_provider?: string;
  llm_model?: string;
  base_url?: string;
  api_key?: string | null;
  custom_providers?: CustomProvider[];
}
