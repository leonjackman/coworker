import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { chatService } from '../services/chatService';

interface TerminalViewProps {
  projectId?: string;
}

/**
 * A real, interactive terminal backed by a PTY shell on the backend
 * (`/ws/terminal`). Keystrokes are forwarded over a WebSocket and the PTY
 * output is rendered with xterm.js. The connection is created when the
 * component mounts (i.e. the terminal tab is active) and torn down on unmount.
 */
export function TerminalView({ projectId }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 12,
      fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
      theme: {
        background: '#0b0e14',
        foreground: '#c9d1e3',
        cursor: '#c9d1e3',
        selectionBackground: 'rgba(120, 140, 200, 0.35)',
        black: '#0b0e14',
        red: '#ff6b6b',
        green: '#7ee787',
        yellow: '#f2cc60',
        blue: '#79c0ff',
        magenta: '#d2a8ff',
        cyan: '#56d4dd',
        white: '#c9d1e3',
        brightBlack: '#5c6370',
        brightWhite: '#ffffff',
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);

    const fit = () => {
      try {
        fitAddon.fit();
      } catch {
        // The container may not be laid out yet; ignore transient failures.
      }
    };
    fit();

    let socket: WebSocket | null = null;
    let disposed = false;
    let resizeObserver: ResizeObserver | null = null;

    const sendResize = () => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    };

    const url = chatService.getTerminalUrl(projectId);
    socket = new WebSocket(url);

    socket.onopen = () => {
      if (disposed) return;
      // Re-fit once the socket is live and the layout has settled.
      fit();
      sendResize();
    };
    socket.onmessage = (event) => {
      term.write(typeof event.data === 'string' ? event.data : '');
    };
    socket.onclose = () => {
      term.write('\r\n\x1b[90m[connection closed]\x1b[0m\r\n');
    };
    socket.onerror = () => {
      term.write('\r\n\x1b[31m[terminal connection error]\x1b[0m\r\n');
    };

    const disposable = term.onData((data) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'input', data }));
      }
    });

    resizeObserver = new ResizeObserver(() => {
      fit();
      sendResize();
    });
    resizeObserver.observe(container);

    const settleTimer = window.setTimeout(() => {
      fit();
      sendResize();
    }, 60);

    return () => {
      disposed = true;
      window.clearTimeout(settleTimer);
      resizeObserver?.disconnect();
      disposable.dispose();
      if (socket) {
        socket.onmessage = null;
        socket.onopen = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
      term.dispose();
    };
  }, [projectId]);

  return <div className="terminal-view" ref={containerRef} />;
}
