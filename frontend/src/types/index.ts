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
}

export interface ApiMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  tool_calls?: ToolCall[];
  created_at: string;
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

export type ProactiveLevel = 'off' | 'low' | 'medium' | 'high';

export interface AppConfig {
  theme: string;
  alwaysOnTop: boolean;
  fontSize: number;
  llmProvider: string;
  llmModel: string;
  hasApiKey: boolean;
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

export interface WsMessage {
  type: 'token' | 'tool_use' | 'tool_result' | 'done' | 'error' | 'approval_request' | 'proactive_skip' | 'memory_updated' | 'daily_greeting_skip' | 'message_ack';
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
}

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
