import { useCallback, useEffect, useRef, useState } from 'react';
import { drainRendererLogs, setRendererLogSender } from '../utils/rendererLogBuffer';
import { wsUrl } from '../utils/wsUrl';
import type { LogLine, LogsInboundMessage, LogsOutboundMessage } from '../types';

const WS_URL = wsUrl('/ws/logs');

/** The panel is a tail, so it keeps the same window the hub does. */
const MAX_LINES = 500;

/** A backend restart must not kill the panel for the rest of the session. */
const MAX_RECONNECTS = 5;
const RECONNECT_DELAY_MS = 2000;

export interface LogsSocket {
  lines: LogLine[];
  connected: boolean;
  sendCommand: (command: string) => boolean;
}

/**
 * The debug console's own WebSocket, connected only while the panel is open.
 *
 * It is deliberately separate from the chat socket: closing the panel must not
 * touch the conversation, and closing a conversation must not kill the logs.
 */
export function useLogsSocket(open: boolean): LogsSocket {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!open) return;

    let disposed = false;
    let attempts = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const socket = new WebSocket(WS_URL);
      socketRef.current = socket;

      const send = (message: LogsOutboundMessage) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
      };

      socket.onopen = () => {
        attempts = 0;
        setConnected(true);
        // From here on, fresh frontend errors go straight down this socket...
        setRendererLogSender((report) =>
          send({ type: 'renderer_log', level: report.level, message: report.message }),
        );
        // ...and everything raised before the panel opened is replayed.
        for (const report of drainRendererLogs()) {
          send({ type: 'renderer_log', level: report.level, message: report.message });
        }
      };

      socket.onmessage = (event) => {
        let data: LogsInboundMessage;
        try {
          data = JSON.parse(event.data) as LogsInboundMessage;
        } catch {
          return;                       // ignore anything that is not ours
        }
        if (data.type === 'logs_history') {
          setLines(data.lines.slice(-MAX_LINES));
        } else if (data.type === 'logs_line') {
          setLines((prev) => [...prev, data.line].slice(-MAX_LINES));
        }
      };

      socket.onclose = () => {
        setConnected(false);
        setRendererLogSender(null);
        if (disposed || attempts >= MAX_RECONNECTS) return;
        attempts += 1;
        retry = setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => setConnected(false);
    };

    connect();

    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      setRendererLogSender(null);
      socketRef.current?.close();     // panel closed → channel closed
      socketRef.current = null;
      setConnected(false);
    };
  }, [open]);

  const sendCommand = useCallback((command: string) => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify({ type: 'command', command }));
    return true;
  }, []);

  return { lines, connected, sendCommand };
}
