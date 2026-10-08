/**
 * A tab: an xterm plugged into the server's stream.
 *
 * The component knows neither the PTY nor the agent -- it writes what it
 * receives and sends keystrokes back. Replaying the buffer the server sends on
 * attach redraws the screen; the server is also what makes the program redraw,
 * so nothing of the kind is done here.
 *
 * `watch` makes it a screen that looks without driving (the "At work" page):
 * it keeps the tab's grid and never announces another -- that grid belongs to
 * the agent and to the Chats window --, scales its image down to its box, and
 * sends no keystrokes.
 */

import { useEffect, useRef, useState } from "react";
import { Terminal, type ITheme } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { api, type TerminalSession } from "../api";
import { readClipboard, writeClipboard } from "../lib/host";
import { t } from "../lib/i18n";

/**
 * The theme is read from the design system's variables.
 *
 * Only surface colors are set: the sixteen-color ANSI palette stays xterm's,
 * because it belongs to the terminal's contract, not to our design -- an `ls`
 * must stay readable.
 */
function readTheme(): ITheme {
  const css = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    background: read("--bg", "#0f0d0b"),
    foreground: read("--fg", "#fafafa"),
    cursor: read("--accent", "#ff7a45"),
    cursorAccent: read("--bg", "#0f0d0b"),
    selectionBackground: read("--surface-hover", "#2a2420"),
  };
}

function fontFamily(): string {
  return getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim();
}

/**
 * Fits the terminal to its box and returns the size to announce to the
 * program, or `null` when the measurement is worthless.
 *
 * A hidden box -- background tab, hidden window, font not loaded yet -- measures
 * badly: a cell's width drops to almost zero and the computation yields
 * hundreds of columns. Announced to the agent, such a size makes it believe in
 * a huge line, and input never wraps again. No readable font is under 4 px
 * wide: beyond that many columns, the measurement is wrong and is withheld.
 */
function fitted(term: Terminal, fit: FitAddon, element: HTMLElement): { cols: number; rows: number } | null {
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (width < 40 || height < 20) return null;
  const dims = fit.proposeDimensions();
  if (!dims || !Number.isFinite(dims.cols) || !Number.isFinite(dims.rows)) return null;
  if (dims.cols < 2 || dims.rows < 1 || dims.cols > width / 4 || dims.rows > height / 6) return null;
  fit.fit();
  return { cols: term.cols, rows: term.rows };
}

export default function TerminalPane({ session, active, watch = false, onRevive, onInsertReady }: {
  session: TerminalSession;
  active: boolean;
  /** Look without driving: no size announced, no keystrokes. */
  watch?: boolean;
  /** Opens a new session on the same agent. Absent: no button. */
  onRevive?: () => void;
  /**
   * Hands the caller a way to write into this terminal.
   *
   * Dropping a path into the conversation means writing into the PTY, and the
   * xterm lives here, out of the window's reach. The window therefore receives
   * a function, withdrawn on unmount -- otherwise a closed tab would leave an
   * insert that writes into the void.
   */
  onInsertReady?: (insert: ((text: string) => void) | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const fitAddon = useRef<FitAddon | null>(null);
  const socket = useRef<WebSocket | null>(null);

  // `active` must not rebuild the terminal: the session mounts once, and
  // switching tabs only shows it. The flag is therefore read through a ref.
  const isActive = useRef(active);
  useEffect(() => {
    isActive.current = active;
  }, [active]);

  const [truncated, setTruncated] = useState(false);
  const [exited, setExited] = useState(session.state !== "running");
  const [code, setCode] = useState<number | null>(session.exit_code);
  const interrupted = session.state === "interrupted";
  const [lost, setLost] = useState(false);
  // Incremented to reconnect the stream: re-running the effect also replays the
  // server's buffer, so the screen comes back without keeping anything here.
  const [attempt, setAttempt] = useState(0);
  // True when the screen could not be replayed for lack of a box to measure:
  // the buffer was not read, and must be read again on becoming visible.
  const blind = useRef(false);
  // True while the recorded grid must stay in place, from the snapshot message
  // until the buffer has been written. A measurement in between (font finishing
  // loading, resize observer, window regaining focus) would replace it with the
  // window's grid, and the buffer would be replayed in the wrong one -- as a
  // string of fragments.
  const replaying = useRef(false);

  useEffect(() => {
    const element = host.current;
    if (!element) return;

    setTruncated(false);
    setLost(false);
    blind.current = false;
    replaying.current = false;

    const term = new Terminal({
      theme: readTheme(),
      fontFamily: fontFamily(),
      fontSize: 12,
      lineHeight: 1.2,
      cursorBlink: !watch,
      disableStdin: watch,
      scrollback: 8000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(element);
    // xterm renders to the DOM by default: under WebKitGTK, an agent that keeps
    // redrawing its status costs a third of a core per window there, and every
    // keystroke waits its turn. WebGL draws on the GPU; if the context is
    // missing or lost, rendering falls back to the DOM without breaking.
    try {
      const webgl = new WebglAddon();
      webgl.onContextLoss(() => webgl.dispose());
      term.loadAddon(webgl);
    } catch {
      // No WebGL here: the DOM renderer stays in place.
    }
    // An active tab fits itself and takes the keyboard as soon as it is created:
    // it may come from a reconnection, and no tab switch will follow to do it.
    if (isActive.current && !watch) {
      fitted(term, fit, element);
      term.focus();
    }

    terminal.current = term;
    fitAddon.current = fit;

    // The design system disables selection everywhere; a terminal is precisely
    // where one wants to copy what is shown. The clipboard goes through the
    // shell (`lib/host`): read by the page, under WebKitGTK it would open a
    // "Paste" menu to confirm on every paste.
    const copy = () => {
      void writeClipboard(term.getSelection()).catch(() => {});
      term.clearSelection();
    };
    const paste = () => {
      void readClipboard()
        .then((text) => text && term.paste(text))
        .catch(() => {});
    };
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown" || !event.ctrlKey || !event.shiftKey) return true;
      const key = event.key.toLowerCase();
      if (key === "c" && term.hasSelection()) {
        copy();
        return false;
      }
      // A watching screen takes no input: nothing to paste.
      if (key === "v" && !watch) {
        paste();
        return false;
      }
      return true;
    });

    // Right click does what it does in a terminal: copies the selection if there
    // is one, pastes otherwise -- and opens no menu. Caught in the capture phase
    // and stopped there: otherwise xterm would move its input area under the
    // mouse to prepare the browser's menu, which is not wanted.
    const onContextMenu = (event: MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (term.hasSelection()) copy();
      else if (!watch) paste();
      if (!watch) term.focus();
    };
    element.addEventListener("contextmenu", onContextMenu, true);

    const ws = new WebSocket(api.terminalStreamUrl(session.id));
    ws.binaryType = "arraybuffer";
    socket.current = ws;

    let disposed = false;
    // The server closes the stream itself after the exit event: that close is
    // expected, and mistaking it for a lost connection would show every
    // finished tab as broken.
    let finished = false;

    // The buffer is a stream of escapes, and a TUI positions itself absolutely
    // in it: replayed at another width than the one it was drawn for, it comes
    // out in pieces. So the recorded grid is restored first, and the terminal is
    // fitted back to its box once the screen is written.
    let replay = false;

    // Watching: the screen keeps the tab's grid and its image is scaled down to
    // the box -- resizing it would change the agent's terminal, for the agent
    // and for the Chats window.
    const shrink = () => {
      const frame = term.element;
      const screen = frame?.querySelector<HTMLElement>(".xterm-screen");
      if (!frame || !screen || !screen.offsetWidth || !screen.offsetHeight) return;
      const ratio = Math.min(1, element.clientWidth / screen.offsetWidth,
                             element.clientHeight / screen.offsetHeight);
      frame.style.transformOrigin = "0 0";
      frame.style.transform = ratio < 1 ? `scale(${ratio})` : "";
    };
    const resized = watch ? term.onResize(() => requestAnimationFrame(shrink)) : null;

    ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      if (typeof event.data === "string") {
        const message = JSON.parse(event.data) as
          { type: string; truncated?: boolean; code?: number | null;
            cols?: number; rows?: number };
        // The tab changed grid (the Chats window measured it): a watching screen
        // follows; a driving one measures itself.
        if (message.type === "size") {
          if (watch && message.cols && message.rows) term.resize(message.cols, message.rows);
          return;
        }
        if (message.type === "snapshot") {
          setTruncated(Boolean(message.truncated));
          if (message.cols && message.rows
              && (message.cols !== term.cols || message.rows !== term.rows)) {
            if (isActive.current) {
              replay = true;
              replaying.current = true;
              term.resize(message.cols, message.rows);
            } else {
              // A background tab has no box: restoring the recorded grid would
              // allocate a wide screen nobody looks at, and it would never fit
              // back (the measurement is zero). Its buffer is not read at all;
              // activation reconnects the stream, which replays it in a visible
              // pane -- at the size it was drawn, then fitted.
              blind.current = true;
            }
          }
        }
        if (message.type === "exit") {
          finished = true;
          // An empty buffer has no binary chunk to replay: without this, the flag
          // would stay set and the terminal would never be fitted again. When
          // the buffer is not empty, the exit message arrives while it is still
          // being written -- the `write` callback restores the grid, not this.
          if (replay) {
            replay = false;
            replaying.current = false;
          }
          setExited(true);
          setCode(message.code ?? null);
        }
        return;
      }
      const chunk = new Uint8Array(event.data);
      if (!replay) {
        // A blind tab (background, recorded grid not restored): writing here
        // would break the buffer into pieces for nobody -- activation
        // reconnects the stream and replays it in a visible pane, at the right
        // size.
        if (blind.current) return;
        term.write(chunk);
        return;
      }
      // Fitting waits until the screen is written: reflowing a half-replayed
      // buffer would wrap lines that have not arrived yet.
      replay = false;
      term.write(chunk, () => {
        replaying.current = false;
        push();
      });
    };

    ws.onclose = () => {
      // A close during cleanup tells nothing: it is ours.
      if (!disposed && !finished) setLost(true);
    };

    const keys = term.onData((chunk) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data: chunk }));
      }
    });

    // The program must learn the real size: the server only knows an
    // approximate one, requested when the tab opened.
    const push = () => {
      // The buffer is being replayed in the recorded grid: measuring now would
      // replace it with the window's, and the rest would come out in pieces.
      // Fitting happens at the end of the replay.
      if (replaying.current) return;
      if (watch) {
        shrink();
        return;
      }
      const size = fitted(term, fit, element);
      if (size && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", ...size }));
      }
    };
    ws.onopen = push;
    // A withheld measurement is redone as soon as it can be right: when the font
    // is loaded, and when the window comes back -- restored from the tray, it
    // keeps its size, so the observer says nothing.
    void document.fonts.ready.then(() => {
      if (!disposed) push();
    });
    const onShow = () => {
      if (document.visibilityState === "visible") push();
    };
    window.addEventListener("focus", push);
    document.addEventListener("visibilitychange", onShow);

    // A window resized at thirty frames per second must not send thirty
    // resizes: the delay absorbs the gesture.
    let timer: number | undefined;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(push, 80);
    });
    observer.observe(element);

    return () => {
      disposed = true;
      replaying.current = false;
      window.clearTimeout(timer);
      observer.disconnect();
      window.removeEventListener("focus", push);
      document.removeEventListener("visibilitychange", onShow);
      element.removeEventListener("contextmenu", onContextMenu, true);
      keys.dispose();
      resized?.dispose();
      ws.onmessage = null;
      ws.onopen = null;
      ws.onclose = null;
      ws.close();
      term.dispose();
      terminal.current = null;
      fitAddon.current = null;
      socket.current = null;
    };
  }, [session.id, attempt, watch]);

  // The insert function, handed to the caller. The text is pasted as is
  // (`term.paste`), without `\r`: a dropped path is not an answer to submit
  // but a word to complete -- the user can still type around it before sending.
  useEffect(() => {
    if (!onInsertReady) return;
    onInsertReady((text: string) => {
      const term = terminal.current;
      if (term && text) term.paste(text);
    });
    return () => onInsertReady(null);
  }, [onInsertReady]);

  // Becoming the visible tab: the box may have changed size meanwhile, and the
  // keyboard must go to the terminal.
  useEffect(() => {
    if (!active || watch) return;
    // This tab was not replayed (no box to do it in one piece): reconnect the
    // stream; the server replays its buffer, and the new terminal measures and
    // fits itself in a visible pane.
    if (blind.current) {
      blind.current = false;
      setAttempt((count) => count + 1);
      return;
    }
    const frame = requestAnimationFrame(() => {
      const term = terminal.current;
      const fit = fitAddon.current;
      const element = host.current;
      if (!term || !fit || !element) return;
      // Same reason as in `push`: the recorded grid stays in place until the
      // replay ends, even if the tab becomes visible again.
      if (replaying.current) return;
      const size = fitted(term, fit, element);
      term.focus();
      const ws = socket.current;
      if (size && ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", ...size }));
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [active, watch]);

  // The tab was resumed: same id, but a new agent behind it, and the previous
  // stream is closed. Without this the screen would stay the interrupted one,
  // in a terminal nobody listens to. Everything is remounted: the new terminal
  // reconnects to the resumed session, which replays its screen.
  useEffect(() => {
    if (session.state !== "running" || !exited) return;
    setExited(false);
    setCode(null);
    setLost(false);
    setAttempt((count) => count + 1);
  }, [session.state, exited]);

  return (
    <div className={`chat-pane${active ? "" : " off"}`} aria-hidden={!active}>
      {/* The banners stay mounted: inserted conditionally, they would shift the
          terminal in the tree, and React would recreate it -- detaching
          xterm's DOM. */}
      <div className={`chat-note top ${truncated ? "" : "is-hidden"}`}>
        {t("History truncated: the start of the output is no longer in the buffer.")}
      </div>
      <div className="chat-term" ref={host} />
      <div className={`chat-note ${exited || lost ? "" : "is-hidden"}`}>
        {/* Three different endings, kept apart: a tab found on disk returned no
            exit code, it died with the studio -- calling it "finished" would
            misreport what happened. */}
        {exited && interrupted && (
          <span>{t("Tab interrupted: the studio stopped before the process ended.")}</span>
        )}
        {exited && !interrupted && (
          <span>{code === null ? t("Process finished.") : t("Process finished (code {code}).", { code })}</span>
        )}
        {exited && interrupted && onRevive && (
          <button className="btn btn-secondary btn-sm" onClick={onRevive}>
            {t("Resume")}
          </button>
        )}
        {lost && (
          <>
            <span>{t("Connection to the stream lost.")}</span>
            <button className="btn btn-secondary btn-sm" onClick={() => setAttempt((n) => n + 1)}>
              {t("Reconnect")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
