import type { ApiMessage, Message } from '../types';

/**
 * The ONE place the REST message shape is mapped onto the store shape.
 *
 * Both of useChat's list loads (initial load + conversation switch) and the
 * history overlay's transcript back-fill go through here, so the API contract
 * (`created_at` / `transcript` / `speaker`) is known in exactly one file.
 */
export function toStoreMessage(api: ApiMessage, conversationId: string): Message {
  return {
    id: api.id,
    conversationId,
    role: api.role,
    content: api.content,
    createdAt: api.created_at,
    transcript: api.transcript ?? null,
  };
}

/**
 * Back-fill the REST-only transcript line onto an already-rendered message.
 *
 * A merge, not a replace: WS-streamed / optimistic store state (streaming
 * content, pending local ids) must survive. `transcript` is immutable once the
 * server has produced it, so a message that already has one is left alone.
 */
export function withTranscript(local: Message, fresh: ApiMessage | undefined): Message {
  return fresh?.transcript ? { ...local, transcript: fresh.transcript } : local;
}
