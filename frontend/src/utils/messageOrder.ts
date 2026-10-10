import type { Message } from '../types';

/**
 * 把一条消息按**序号**插进列表（ticket #11）。
 *
 * 群聊并发之后"到达顺序 ≠ 发言顺序"：几个成员同时生成，谁的先想好谁的先落库；前端
 * 又不能假设到达顺序与 `seq` 一致（历史上在途、重连重播都可能打乱），所以一律以
 * 消息自带的 `seq` 为准，而不是 append。
 *
 * 规则（后端 `Message.seq` 是会话内单调递增的，ticket #56）：
 * - 同 id 已在列表里（重连后重播同一条）：**原地**替换内容，绝不挪位置 —— 已经渲染
 *   出来的消息跳来跳去比顺序不完美更糟。
 * - `incoming.seq` 是数字：插在**第一条 seq 比它大**的消息之前；没有这样的消息就追加。
 * - 没有 seq（1:1 的流式消息、乐观插入还没 ack 的用户消息、老数据）：追加到末尾 ——
 *   它们本来就是按到达顺序排的。
 *
 * 纯函数：不改入参、不碰 store，便于单独推理。
 */
export function insertBySequence(messages: Message[], incoming: Message): Message[] {
  const seen = messages.findIndex((m) => m.id === incoming.id);
  if (seen !== -1) {
    const next = [...messages];
    next[seen] = { ...incoming, seq: incoming.seq ?? messages[seen].seq };
    return next;
  }

  const seq = incoming.seq;
  if (typeof seq !== 'number') return [...messages, incoming];

  const at = messages.findIndex((m) => typeof m.seq === 'number' && m.seq > seq);
  if (at === -1) return [...messages, incoming];
  return [...messages.slice(0, at), incoming, ...messages.slice(at)];
}
