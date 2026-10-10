/**
 * 自检：群聊消息的**序号插入**规则（ticket #11）。
 *
 * 前端没有测试基建（spec 的 Testing Decisions：前端手工验收），但"并发到达时消息排在哪"
 * 是这次唯一有真实分叉的纯逻辑，值得一条能重跑的检查：
 *
 *     cd frontend && node scripts/check_message_order.mjs
 *
 * 只依赖 Node 的类型擦除（v22.6+ / v24 默认），不装任何东西。
 * 这条检查在开发时**抓到过真 bug**：早先的实现是"摘掉同 id 再按 seq 插"，于是重连重播
 * 同一条消息会把已经渲染出来的那条挪走 —— 现在同 id 一律原地替换（见 `insertBySequence`）。
 */
import { insertBySequence } from '../src/utils/messageOrder.ts';

const msg = (id, seq) => ({
  id,
  conversationId: 'c',
  role: 'assistant',
  content: id,
  createdAt: '',
  seq,
});

const ids = (list) => list.map((m) => m.id).join(',');
let failures = 0;

function check(label, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}: ${actual}${ok ? '' : ` (expected ${expected})`}`);
}

// 并发到达被打乱：seq 3 先到，然后 1、5、2、4
let list = [];
for (const [id, seq] of [['c3', 3], ['a1', 1], ['e5', 5], ['b2', 2], ['d4', 4]]) {
  list = insertBySequence(list, msg(id, seq));
}
check('乱序到达 → 按 seq 排好', ids(list), 'a1,b2,c3,d4,e5');

// 用户那条还没有 seq（乐观插入）→ 追加；随后 ack 补上序号
list = [msg('m1', 1)];
list = insertBySequence(list, { ...msg('local-user', undefined), role: 'user' });
check('无序号（乐观用户消息）追加', ids(list), 'm1,local-user');

// 成员消息 seq=2 到达：用户那条此刻还没序号，新消息照样追加在它后面
list = insertBySequence(list, msg('m2', 2));
check('seq 比现有都大 → 追加', ids(list), 'm1,local-user,m2');

// 重连重播同一条：原地替换，不重复、也不挪位置
list = insertBySequence(list, msg('m1', 1));
check('同 id 重播不重复', ids(list), 'm1,local-user,m2');
check('同 id 重播原地替换', list[0].id, 'm1');

// 中间插入：落在第一条更大的 seq 之前
list = [msg('x', 10), msg('z', 20)];
list = insertBySequence(list, msg('y', 15));
check('插到第一条更大的 seq 之前', ids(list), 'x,y,z');

check('空列表', ids(insertBySequence([], msg('a', 1))), 'a');

// 插话竞态（store 的 replaceMessageId 用同一个规则补位）：
// 用户插话那条先乐观追加（无序号），期间某成员的一轮落库；等 message_ack 补上真序号时，
// 必须按序号把它挪到正确位置 —— 具体在回复前还是后，取决于两者真实的序号。
function interjectionLands(bSeq, uSeq) {
  let out = insertBySequence(
    [msg('u1', 1), msg('a1', 2), { ...msg('u2-local', undefined), role: 'user' }],
    msg('b1', bSeq),
  );
  const acked = { ...out.find((m) => m.id === 'u2-local'), id: 'u2', seq: uSeq };
  const left = out.filter((m) => m.id !== 'u2-local');
  return ids(insertBySequence(left, acked));
}

check('ack 序号更大 → 排在那条回复之后', interjectionLands(3, 4), 'u1,a1,b1,u2');
check('ack 序号更小 → 排在那条回复之前', interjectionLands(4, 3), 'u1,a1,u2,b1');

console.log(failures === 0 ? 'PASS' : `${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
