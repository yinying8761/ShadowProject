import type { LogLine, LogLevel } from '../types';

/**
 * Frontend log reports on their way to the debug console.
 *
 * Errors are captured from the moment the app starts, but the log socket only
 * exists while the panel is open — so a report goes live when a channel is
 * attached and queues otherwise (newest 100), to be replayed on connect.
 */
export const MAX_BUFFERED_REPORTS = 100;

type LiveSender = (report: LogLine) => void;

let buffered: LogLine[] = [];
let liveSender: LiveSender | null = null;

/**
 * Attach (or detach, with null) the connected channel. Reports raised while a
 * sender is attached are sent straight away instead of queuing.
 */
export function setRendererLogSender(sender: LiveSender | null): void {
  liveSender = sender;
}

export function reportRendererLog(level: LogLevel, message: string): void {
  const report: LogLine = { source: 'renderer', level, message, ts: Date.now() / 1000 };
  if (liveSender) {
    liveSender(report);
    return;
  }
  buffered.push(report);
  if (buffered.length > MAX_BUFFERED_REPORTS) {
    buffered = buffered.slice(-MAX_BUFFERED_REPORTS);
  }
}

/** Take everything queued so far; the queue is empty afterwards. */
export function drainRendererLogs(): LogLine[] {
  const drained = buffered;
  buffered = [];
  return drained;
}
