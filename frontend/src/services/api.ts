import type { CharacterProfile, Conversation, Message, AppConfig, ApiMessage, MemoryEntry, UserProfile, TokenUsageResponse } from '../types';

const BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  fetchCharacters: () => request<CharacterProfile[]>('/characters'),
  createCharacter: (data: Partial<CharacterProfile>) =>
    request<{ id: string; name: string }>('/characters', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateCharacter: (id: string, data: Partial<CharacterProfile>) =>
    request<{ status: string }>(`/characters/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteCharacter: (id: string) =>
    request<{ status: string }>(`/characters/${id}`, { method: 'DELETE' }),
  fetchConversations: (characterId?: string) =>
    request<Conversation[]>(
      `/conversations${characterId ? `?character_id=${characterId}` : ''}`
    ),
  createConversation: (characterId: string) =>
    request<Conversation>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ character_id: characterId }),
    }),
  fetchMessages: (conversationId: string) =>
    request<ApiMessage[]>(`/conversations/${conversationId}/messages`),
  deleteConversation: (id: string) =>
    request<{ status: string }>(`/conversations/${id}`, { method: 'DELETE' }),
  fetchConfig: async (): Promise<AppConfig> => {
    const raw = await request<Record<string, unknown>>('/config');
    return {
      theme: (raw.theme as string) ?? 'dark',
      alwaysOnTop: (raw.always_on_top as boolean) ?? false,
      fontSize: (raw.font_size as number) ?? 14,
      llmProvider: (raw.llm_provider as string) ?? '',
      llmModel: (raw.llm_model as string) ?? '',
      hasApiKey: (raw.has_api_key as boolean) ?? false,
      showFloatingIcon: (raw.show_floating_icon as boolean) ?? true,
      floatingIconX: (raw.floating_icon_x as number) ?? -1,
      floatingIconY: (raw.floating_icon_y as number) ?? -1,
      proactiveChatLevel: ((raw.proactive_chat_level as string) ?? 'medium') as
        | 'off' | 'low' | 'medium' | 'high',
      proactiveSilentToolApproval:
        (raw.proactive_silent_tool_approval as boolean) ?? false,
      proactiveAutoSeeScreen:
        (raw.proactive_auto_see_screen as boolean) ?? true,
      proactiveDailyLimit: (raw.proactive_daily_limit as number) ?? 10,
      proactiveFixedScheduleEnabled:
        (raw.proactive_fixed_schedule_enabled as boolean) ?? true,
      proactiveSystemNotification:
        (raw.proactive_system_notification as boolean) ?? true,
      language: (raw.language as string) ?? 'zh',
      ttsEnabled: (raw.tts_enabled as boolean) ?? true,
      lastCharacterId: (raw.last_character_id as string) ?? '',
    };
  },
  updateConfig: (data: Record<string, unknown>) =>
    request<{ status: string }>('/config', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  fetchMemories: (characterId?: string) =>
    request<{ memories: MemoryEntry[]; total: number }>(
      `/memories${characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''}`,
    ),
  healthCheck: () => request<{ status: string }>('/health'),
  _profileUrl: (characterId?: string) =>
    `/user-profile${characterId ? `?character_id=${encodeURIComponent(characterId)}` : ''}`,
  fetchUserProfile: (characterId?: string) =>
    request<UserProfile>(api._profileUrl(characterId)),
  updateUserProfile: (characterId: string | null, data: Partial<UserProfile>) =>
    request<UserProfile>(api._profileUrl(characterId ?? undefined), {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  fetchTokenUsage: (conversationId: string) =>
    request<TokenUsageResponse>(
      `/token-usage?conversation_id=${encodeURIComponent(conversationId)}`
    ),
};
