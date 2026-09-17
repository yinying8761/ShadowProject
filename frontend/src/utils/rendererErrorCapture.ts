import { reportRendererLog } from './rendererLogBuffer';

/**
 * Capture frontend noise into the renderer log buffer: console.error/warn,
 * uncaught errors and unhandled rejections.
 *
 * console.log is deliberately NOT captured — ordinary frontend logs belong in
 * DevTools (F12), and forwarding them would drown the panel.
 */

const MAX_MESSAGE_CHARS = 2000;
let installed = false;

function describe(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function formatArgs(args: unknown[]): string {
  const text = args.map(describe).join(' ');
  return text.length > MAX_MESSAGE_CHARS ? `${text.slice(0, MAX_MESSAGE_CHARS)}…` : text;
}

/** Install the capture hooks once; safe to call from a React effect. */
export function installRendererErrorCapture(): void {
  if (installed) return;
  installed = true;

  const originalError = console.error;
  const originalWarn = console.warn;

  console.error = (...args: unknown[]) => {
    reportRendererLog('error', formatArgs(args));
    originalError(...args);          // DevTools keeps working as before
  };
  console.warn = (...args: unknown[]) => {
    reportRendererLog('warn', formatArgs(args));
    originalWarn(...args);
  };

  window.addEventListener('error', (event) => {
    const where = event.filename ? ` (${event.filename}:${event.lineno})` : '';
    reportRendererLog('error', `${event.message || describe(event.error)}${where}`);
  });

  window.addEventListener('unhandledrejection', (event) => {
    reportRendererLog('error', `unhandled rejection: ${describe(event.reason)}`);
  });
}
