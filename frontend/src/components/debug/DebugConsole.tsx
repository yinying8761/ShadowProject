import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useLogsSocket } from '../../hooks/useLogsSocket';
import { useTranslation } from '../../i18n/useTranslation';
import type { LogLevel, LogLine } from '../../types';

const LEVEL_CLASS: Record<LogLevel, string> = {
  error: 'text-red-400',
  warn: 'text-amber-300',
  info: 'text-gray-300',
  debug: 'text-gray-500',
};

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false });
}

function formatLine(line: LogLine): string {
  return `${formatTime(line.ts)} [${line.source}] ${line.message}`;
}

/**
 * The debug console: a slide-over log tail with a command line (ticket 05).
 *
 * Ctrl+Shift+D toggles it (see useKeyboardShortcuts); the log socket only
 * exists while it is open, and it is not the chat socket.
 */
export function DebugConsole() {
  const show = useAppStore((s) => s.showDebugConsole);
  const setShow = useAppStore((s) => s.setShowDebugConsole);
  const { lines, connected, sendCommand } = useLogsSocket(show);
  const { t } = useTranslation();
  const [hideRenderer, setHideRenderer] = useState(false);
  const [command, setCommand] = useState('');
  const [copied, setCopied] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const visible = hideRenderer ? lines.filter((l) => l.source !== 'renderer') : lines;

  useEffect(() => {
    if (show) bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [show, visible.length]);

  useEffect(() => {
    if (show) inputRef.current?.focus();
  }, [show]);

  useEffect(
    () => () => {
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
    },
    [],
  );

  if (!show) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(visible.map(formatLine).join('\n'));
      setCopied(true);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
      copiedTimer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // The clipboard can be refused; leave the button showing "Copy".
    }
  };

  const handleSubmit = () => {
    const trimmed = command.trim();
    if (!trimmed) return;
    // Keep the typed text when the socket is down, so it is not lost silently.
    if (sendCommand(trimmed)) setCommand('');
  };

  return (
    <div
      role="dialog"
      aria-label={t('Debug Console')}
      className="no-drag fixed inset-y-0 right-0 z-50 flex w-[560px] max-w-[92vw] animate-fade-in flex-col border-l border-white/10 bg-companion-overlay-strong shadow-2xl backdrop-blur-sm"
    >
      <div className="flex items-center gap-3 border-b border-white/10 px-3 py-2 text-xs text-gray-300">
        <span className="font-semibold">{t('Debug Console')}</span>
        <span className={connected ? 'text-emerald-400' : 'text-red-400'}>
          {connected ? t('Connected') : t('Offline')}
        </span>
        <span className="text-gray-500">{visible.length}</span>
        <div className="ml-auto flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1">
            <input
              type="checkbox"
              checked={hideRenderer}
              onChange={(e) => setHideRenderer(e.target.checked)}
            />
            {t('Hide [renderer]')}
          </label>
          <button className="hover:text-white" onClick={handleCopy}>
            {copied ? t('Copied') : t('Copy')}
          </button>
          <button className="hover:text-white" onClick={() => setShow(false)}>
            {t('Close')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed">
        {visible.map((line, index) => (
          <div
            key={`${line.ts}-${index}`}
            className={`whitespace-pre-wrap break-words ${LEVEL_CLASS[line.level] ?? 'text-gray-300'}`}
          >
            <span className="text-gray-500">{formatTime(line.ts)} </span>
            <span className="text-gray-400">[{line.source}] </span>
            {line.message}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="flex items-center gap-2 border-t border-white/10 px-3 py-2">
        <span className="text-xs text-gray-500">&gt;</span>
        <input
          ref={inputRef}
          className="flex-1 bg-transparent text-xs text-gray-100 outline-none"
          placeholder={t('Command (help)')}
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleSubmit();
            }
          }}
        />
      </div>
    </div>
  );
}
