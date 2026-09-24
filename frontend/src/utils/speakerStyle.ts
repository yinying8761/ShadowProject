/** 每个说话人一个稳定的强调色：群聊里一眼看出"这句是谁说的"。 */
const SPEAKER_COLORS = ['#f0908c', '#8cb8f0', '#a8d98c', '#e0b46a', '#c39cf0', '#6fd6c4'];

export function speakerColor(characterId: string | null | undefined): string {
  if (!characterId) return SPEAKER_COLORS[0];
  let hash = 0;
  for (const ch of characterId) hash = (hash * 31 + ch.charCodeAt(0)) % 9973;
  return SPEAKER_COLORS[hash % SPEAKER_COLORS.length];
}
