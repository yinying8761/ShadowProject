/**
 * Raw server/LLM error text → friendly, user-facing copy.
 *
 * Single place to add new error kinds (Workflow I, issue #39). The returned
 * value is an i18n key; render it through `t()` (or `getTranslation`).
 */
const CONNECTION_HINTS = [
  'connect',
  'network',
  'socket',
  'econn',
  'enotfound',
  'dns',
  'offline',
  '网络',
  '连接',
];

const TIMEOUT_HINTS = ['timeout', 'timed out', 'time out', '超时'];

function contains(text: string, hints: string[]): boolean {
  return hints.some((hint) => text.includes(hint));
}

export function friendlyErrorKey(raw?: string): string {
  // Lowercasing keeps ASCII hints case-insensitive; CJK is unaffected.
  const message = (raw ?? '').toLowerCase();
  if (contains(message, CONNECTION_HINTS)) {
    return 'Network error. Check your connection or try again later.';
  }
  if (contains(message, TIMEOUT_HINTS)) {
    return 'Response timed out. Please try again later.';
  }
  return 'Something went wrong. Please try again later.';
}