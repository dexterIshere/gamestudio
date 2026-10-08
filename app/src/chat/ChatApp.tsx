/**
 * The Chats window: a strip of tabs, one terminal per tab.
 *
 * It renders nothing of the studio -- no rail -- but follows its active
 * project: a new tab opens in the project's folder, and that folder's sessions
 * come before the others. Tabs live in the server: closing this window closes
 * no agent, and reopening it finds them all. Only their presentation is kept
 * here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Harness, type InboxFile, type TerminalSession } from "../api";
import { Badge, Dialog, Empty, Menu, bytes } from "../components/ui";
import {
  CHAT_FOCUS_KEY, chatPinned, inTauri, listenFileDrop, pickFile, toggleChatPin, toggleStudio,
} from "../lib/host";
import { activeProject } from "../lib/store";
import TerminalPane from "./TerminalPane";
import { HARNESS_LOGOS } from "./logos";
import { TAB_COLORS, readMeta, writeMeta, type ChatMeta } from "./meta";
import { t, tr } from "../lib/i18n";
import { folderName } from "../lib/paths";

const why = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * The studio's active project, followed from this window.
 *
 * Both windows share their storage: the studio writes the project there, and
 * the `storage` event tells this one. Regaining focus reads it again too, in
 * case the event was lost.
 */
function useActiveProject(): string {
  const [project, setProject] = useState(activeProject);
  useEffect(() => {
    const read = () => setProject(activeProject());
    window.addEventListener("storage", read);
    window.addEventListener("focus", read);
    return () => {
      window.removeEventListener("storage", read);
      window.removeEventListener("focus", read);
    };
  }, []);
  return project;
}

export default function ChatApp() {
  const client = useQueryClient();
  const { data: sessions, error } = useQuery({
    queryKey: ["terminal", "sessions"],
    queryFn: api.terminalSessions,
    // The server is local and the list short: polling it costs less than
    // keeping a second stream open to learn what changed.
    refetchInterval: 2000,
  });

  const [meta, setMeta] = useState<ChatMeta>(readMeta);
  const [wanted, setWanted] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  // Picking the agent is a decision, not a setting: it goes through a dialog
  // where each provider is recognized by its logo.
  const [picker, setPicker] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  // Closing a live tab kills the agent and loses the conversation: the
  // window's only destructive action, so it is confirmed.
  const [closing, setClosing] = useState<TerminalSession | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // The inbox: what was dropped for the agent. Opened to take a path from it,
  // reloaded on every opening -- it is a folder, it changes.
  const [inboxOpen, setInboxOpen] = useState(false);
  const [dropping, setDropping] = useState(false);
  // A writer into each tab's terminal, published by the tabs themselves and
  // withdrawn on unmount.
  const inserters = useRef(new Map<string, (text: string) => void>());
  // Where the session list opens: under its button, like any menu. `Menu`
  // closes it on a click elsewhere.
  const [pick, setPick] = useState<{ x: number; y: number } | null>(null);

  // Both windows open together: at startup, the studio is visible. Outside the
  // shell there is no second window, and the button is disabled.
  const [studio, setStudio] = useState<boolean | null>(() => (inTauri() ? true : null));

  // Pinning belongs to the compositor, not to the window: only the shell can
  // answer. Until it does, the button is disabled -- it cannot promise what the
  // window manager does not know.
  const { data: pinned } = useQuery({
    queryKey: ["chat", "pinned"],
    queryFn: chatPinned,
    enabled: inTauri(),
    // Polled, not only read on mount: a compositor reload reapplies the config
    // file's `pin on` rule, so the state can change without this button. A
    // label ignoring that would be wrong on the next click.
    //
    // Polling stops only when there is no answer: after a few tries, a machine
    // without a compositor will never give one.
    refetchInterval: (query) =>
      query.state.data == null && query.state.dataUpdateCount >= 6 ? false : 3000,
  });
  const [busyPin, setBusyPin] = useState(false);

  // The catalog is fetched only when the dialog opens: that is the only time it
  // is used, and the server runs a `which` for every entry.
  const { data: harnesses } = useQuery({
    queryKey: ["terminal", "harnesses"],
    queryFn: api.terminalHarnesses,
    enabled: picker,
    staleTime: 30_000,
  });

  useEffect(() => writeMeta(meta), [meta]);

  // The active project's folder. A studio project without a folder works at the
  // studio root: its sessions are then those in no other project's folder.
  const project = useActiveProject();
  const { data: recipes } = useQuery({
    queryKey: ["recipes"],
    queryFn: () => api.recipes(),
    staleTime: 10_000,
  });
  const roots = useMemo(
    () => new Set((recipes ?? []).flatMap((recipe) => (recipe.root ? [recipe.root] : []))),
    [recipes],
  );
  const here = (recipes ?? []).find((recipe) => recipe.project === project)?.root ?? null;
  const isHere = useCallback(
    (session: TerminalSession) => (here ? session.cwd === here : !roots.has(session.cwd)),
    [here, roots],
  );

  const ordered = useMemo(() => {
    const rank = (session: TerminalSession) => {
      const at = meta.order.indexOf(session.id);
      return [
        // The active project's sessions first, then other folders'.
        (isHere(session) ? 0 : 2) + (meta.pinned.includes(session.id) ? 0 : 1),
        at === -1 ? Number.MAX_SAFE_INTEGER : at,
        session.created_at,
      ] as const;
    };
    return [...(sessions ?? [])].sort((left, right) => {
      const a = rank(left);
      const b = rank(right);
      return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
    });
  }, [sessions, meta.order, meta.pinned, isHere]);
  const local = ordered.filter(isHere);
  const elsewhere = ordered.filter((session) => !isHere(session));

  // Switching project switches sessions: a tab chosen in another folder does
  // not stay on screen.
  useEffect(() => setWanted(null), [project]);

  // The studio can point at a tab -- the one it just handed a VFX to, for
  // instance: switch to it, then forget the request.
  useEffect(() => {
    const follow = () => {
      try {
        const id = localStorage.getItem(CHAT_FOCUS_KEY);
        if (!id) return;
        localStorage.removeItem(CHAT_FOCUS_KEY);
        setWanted(id);
        void client.invalidateQueries({ queryKey: ["terminal", "sessions"] });
      } catch {
        /* Without storage, nothing to follow. */
      }
    };
    follow();
    window.addEventListener("storage", follow);
    window.addEventListener("focus", follow);
    return () => {
      window.removeEventListener("storage", follow);
      window.removeEventListener("focus", follow);
    };
  }, [client]);

  // The chosen tab if it still exists, else the project's first. Never another
  // folder's by default: it would pass for one of this project's sessions.
  const active = ordered.find((session) => session.id === wanted) ?? local[0] ?? null;
  const activeId = active?.id ?? null;

  // What is on screen is seen. The server alone judges both counters: what
  // matters is not what was displayed but what arrived -- and, for an agent,
  // when it handed control back.
  useEffect(() => {
    if (!active) return;
    const turn = active.turn?.at ?? 0;
    setMeta((current) =>
      (current.seen[active.id] ?? 0) >= active.seq &&
      (current.seenTurn[active.id] ?? 0) >= turn
        ? current
        : {
            ...current,
            seen: { ...current.seen, [active.id]: Math.max(active.seq, current.seen[active.id] ?? 0) },
            seenTurn: {
              ...current.seenTurn,
              [active.id]: Math.max(turn, current.seenTurn[active.id] ?? 0),
            },
          });
  }, [active]);

  /**
   * Is the tab asking for something?
   *
   * For an agent whose transcript can be read, the only question that matters
   * is "has it finished and is it waiting for a reply". `seq` cannot tell: it
   * moves as soon as a character is shown, so a blinking counter or a progress
   * bar would light the badge with nobody waiting. For other tabs -- a `bash`,
   * a harness without a transcript -- it stays the only criterion, coarse but
   * right.
   */
  const waiting = (session: TerminalSession): boolean =>
    session.turn
      ? session.turn.state === "waiting" && session.turn.at > (meta.seenTurn[session.id] ?? 0)
      : session.seq > (meta.seen[session.id] ?? 0);

  /**
   * How many sessions are waiting for a reply, not counting the one on screen:
   * with the list closed, one should not have to open it to learn that
   * something happened elsewhere.
   */
  const waitingCount = ordered.filter(
    (session) => session.id !== activeId && waiting(session),
  ).length;

  const refresh = useCallback(
    () => client.invalidateQueries({ queryKey: ["terminal", "sessions"] }),
    [client],
  );

  const create = async (harness: string, effort = "") => {
    setFailure(null);
    try {
      // A project opened on a folder: the agent works in that folder (the server
      // gives it the studio's tools). Otherwise, the studio root.
      const current = activeProject();
      const root = current
        ? (await api.recipes()).find((recipe) => recipe.project === current)?.root
        : null;
      const session = await api.terminalCreate({ harness, effort, ...(root ? { cwd: root } : {}) });
      setWanted(session.id);
      setMeta((current) => ({ ...current, lastHarness: harness }));
      await refresh();
    } catch (error) {
      setFailure(why(error));
    }
  };

  /** Remembers an effort level, per agent (shown, never applied silently). */
  const setEffort = (harness: string, level: string) => {
    setMeta((current) => ({
      ...current,
      effort: { ...current.effort, [harness]: level },
    }));
  };

  /**
   * Writes a path into the active tab's terminal.
   *
   * That is all a drop does: the agent reads files, so what it gets is a path
   * -- not an attachment it could not open. The text is inserted without
   * submitting: the user can still type around it before sending.
   */
  const insert = useCallback((text: string) => {
    const session = activeId;
    if (!session) {
      setFailure(t("No tab open: the path is in the inbox."));
      return;
    }
    const write = inserters.current.get(session);
    if (!write) {
      setFailure(t("This tab has ended: resume the session to drop files into it."));
      return;
    }
    write(`${text} `);
  }, [activeId]);

  /**
   * Drops files into the inbox and writes their paths.
   *
   * A file dragged into the window has no path on the browser side: its bytes
   * are sent to the studio, which writes them and returns the path. In the
   * shell, drag and drop gives real paths (`listenFileDrop`), and the file is
   * dropped without the browser copying it.
   */
  const deposit = useCallback(async (sources: { path?: string; file?: File }[]) => {
    const written: string[] = [];
    for (const source of sources) {
      try {
        const result = source.path
          ? await api.inboxAdd(source.path)
          : await api.uploadToInbox(source.file as File, source.file?.name ?? "capture.png");
        written.push(result.relative);
      } catch (error) {
        setFailure(why(error));
      }
    }
    if (written.length) insert(written.join(" "));
  }, [insert]);

  const onPaste = async (event: React.ClipboardEvent<HTMLDivElement>) => {
    if (!activeId) return;
    const image = Array.from(event.clipboardData.items).find(
      (item) => item.kind === "file" && item.type.startsWith("image/"),
    );
    // Text is pasted into the terminal by xterm: only an image, which xterm
    // cannot write, is taken here.
    if (!image) return;
    event.preventDefault();
    const file = image.getAsFile();
    if (file) await deposit([{ file }]);
  };

  // Outside the shell, the DOM `drop` carries the file's bytes.
  const onDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDropping(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) await deposit(files.map((file) => ({ file })));
  };

  // In the shell, a file from the file manager does not reach the DOM: the
  // webview reports the drop, paths included. The whole window is the target.
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    return listenFileDrop(element, {
      hover: setDropping,
      drop: (paths) => void deposit(paths.map((path) => ({ path }))),
    });
  }, [deposit]);

  /** Opens the agent picker, preselected on the last one used. */
  const openPicker = () => {
    setChoice(meta.lastHarness ?? null);
    setPicker(true);
  };

  const toggle = async () => {
    const visible = await toggleStudio();
    if (visible !== null) setStudio(visible);
  };

  /** Toggles the window's pinning. The compositor alone decides: the state it
   *  returns is kept, not the one requested. */
  const switchPin = async () => {
    setBusyPin(true);
    try {
      const next = await toggleChatPin();
      if (next !== null) client.setQueryData(["chat", "pinned"], next);
    } catch (error) {
      setFailure(why(error));
    } finally {
      setBusyPin(false);
    }
  };

  /**
   * Asks before deleting a session for good.
   *
   * Always asked, even for a finished tab: what disappears is not only the
   * screen but the kept record -- and nothing brings it back. The list and the
   * action menu close: the dialog goes on top, and a list left open in front
   * of it would hide its question.
   */
  const askClose = (session: TerminalSession) => {
    setPick(null);
    setMenu(null);
    setClosing(session);
  };

  const close = async (id: string) => {
    if (id === activeId) setWanted(null);
    try {
      await api.terminalClose(id);
    } catch (error) {
      setFailure(why(error));
    }
    await refresh();
  };

  /** Reopens an ended tab: same agent, same directory, new session. */
  const revive = async (id: string) => {
    setFailure(null);
    try {
      const session = await api.terminalRevive(id);
      setWanted(session.id);
    } catch (error) {
      setFailure(why(error));
    }
    await refresh();
  };

  const rename = (id: string, title: string) => {
    setRenaming(null);
    const clean = title.trim();
    const current = ordered.find((session) => session.id === id);
    if (!clean || !current || clean === current.title) return;
    api
      .terminalRename(id, clean)
      .then(refresh)
      .catch((error: unknown) => setFailure(why(error)));
  };

  /** Moves a tab up or down: the whole order is then stored. */
  const move = (id: string, step: number) => {
    const ids = ordered.map((session) => session.id);
    const from = ids.indexOf(id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to]!, ids[from]!];
    setMeta((current) => ({ ...current, order: ids }));
  };

  const togglePin = (id: string) =>
    setMeta((current) => ({
      ...current,
      pinned: current.pinned.includes(id)
        ? current.pinned.filter((entry) => entry !== id)
        : [...current.pinned, id],
    }));

  const setColor = (id: string, token: string) =>
    setMeta((current) => ({ ...current, color: { ...current.color, [id]: token } }));

  const openMenu = menu ? ordered.find((session) => session.id === menu.id) : undefined;

  return (
    <div
      ref={root}
      className="chat"
      onPaste={(event) => void onPaste(event)}
      onDrop={(event) => void onDrop(event)}
      onDragOver={(event) => {
        // Otherwise the browser navigates to the dropped file: the whole window
        // would be replaced by an image.
        event.preventDefault();
        setDropping(true);
      }}
      onDragLeave={() => setDropping(false)}
    >
      {/* One bar: the mark, the session picker, then the window's commands as
          icons. A picker rather than a strip of tabs: at ten sessions a strip
          wraps onto several rows and pushes the terminal out of the window,
          while a list shows them all in one row. Working directory and state
          are in each line's tooltip. */}
      <header className="chat-head" data-tauri-drag-region>
        <span className="mark" aria-label="Chats">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 7l5 5-5 5M12.5 17.5H19" />
          </svg>
        </span>
        <button
          className="chat-pick"
          aria-haspopup="menu"
          aria-expanded={pick !== null}
          disabled={!ordered.length}
          title={active
            ? `${active.title} — ${active.command} — ${active.cwd}${stateWord(active)}`
            : t("No open session")}
          onClick={(event) => {
            if (pick) {
              setPick(null);
              return;
            }
            const box = event.currentTarget.getBoundingClientRect();
            setPick({ x: box.left, y: box.bottom + 6 });
          }}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
          <span className="label">{active?.title ?? t("No session")}</span>
          {active?.state === "interrupted" && <span className="end">{t("interrupted")}</span>}
          {active?.state === "exited" && <span className="end">{t("finished")}</span>}
          {waitingCount > 0 && <span className="count num">{waitingCount}</span>}
          <svg className="chev" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        <button
          className="btn btn-ghost btn-icon chat-new"
          onClick={openPicker}
          aria-label={t("New session")}
          title={t("New session — open a tab with the agent of your choice")}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <div className="chat-tools">
          <button
            className="btn btn-ghost btn-icon"
            onClick={() => setInboxOpen(true)}
            aria-label={t("Inbox")}
            title={t("Inbox — what was dropped for the agent: screenshots, dragged files")}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 13.5L6.5 6h11l2.5 7.5v5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z" />
              <path d="M4 13.5h4.5l1 2h5l1-2H20" />
            </svg>
          </button>
          {/* The studio window, not a page: no `aria-current`. The state is
              carried by `aria-pressed`, which the style shows as accent. */}
          <button
            className="btn btn-ghost btn-icon"
            aria-pressed={studio === true}
            disabled={studio === null}
            onClick={() => void toggle()}
            aria-label={t("Studio window")}
            title={
              studio === null
                ? t("No separate window in a browser")
                : studio
                  ? t("Hide the studio window — it keeps running")
                  : t("Bring the studio window back")
            }
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="3" y="4.5" width="18" height="15" rx="2" />
              <path d="M8.5 4.5v15" />
            </svg>
          </button>
          {/* Two kinds of window: pinned, it floats above a tiling that takes
              the full width and follows every workspace of the screen; free,
              it is tiled like the others and lives on its own workspace only.
              It starts free. */}
          <button
            className="btn btn-ghost btn-icon"
            aria-pressed={pinned === true}
            disabled={pinned == null || busyPin}
            onClick={() => void switchPin()}
            aria-label={pinned ? t("Pinned") : t("Pin")}
            title={
              pinned == null
                ? t("The window manager does not answer this question")
                : pinned
                  ? t("Pinned — click to release it: it joins the tiling of this workspace, and lives on it alone")
                  : t("Free — click to pin it: floating above the tiling, present on every workspace of the screen")
            }
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M9 3.5h6l-1 6 3.5 3.5v1.5h-11V13L10 9.5z" />
              <path d="M12 14.5v6" />
            </svg>
          </button>
        </div>
      </header>

      <div className="chat-stage">
        {ordered.map((session) => (
          <TerminalPane
            key={session.id}
            session={session}
            active={session.id === activeId}
            onRevive={() => void revive(session.id)}
            onInsertReady={(write) => {
              if (write) inserters.current.set(session.id, write);
              else inserters.current.delete(session.id);
            }}
          />
        ))}
        {!active && (
          <div className="empty">
            <div className="what">
              {error
                ? t("The studio server is not responding.")
                : here
                  ? t("No session in {folderName}", { folderName: folderName(here) })
                  : t("No tab open.")}
            </div>
            {!error && (
              <button className="btn btn-primary" onClick={openPicker}>
                {t("Open a session")}
              </button>
            )}
          </div>
        )}
      </div>

      {failure && (
        <div className="callout chat-alert">
          <span className="grow">{tr(failure)}</span>
          <button className="action" onClick={() => setFailure(null)}>
            {t("close")}
          </button>
        </div>
      )}

      {/* The session list behind the picker: all of them, in the chosen order,
          each with the trash button that deletes it for good. */}
      {pick && (
        <Menu className="chat-menu" x={pick.x} y={pick.y} onClose={() => setPick(null)}>
          <div className="chat-list">
            {[...local, ...elsewhere].map((session, index) => {
              const token = meta.color[session.id];
              const pinned = meta.pinned.includes(session.id);
              const unread = session.id !== activeId && waiting(session);
              const other = index >= local.length;
              return (
                <div key={session.id} className="chat-row">
                {other && index === local.length && (
                  <p className="chat-group">{t("Other folders")}</p>
                )}
                <div
                  className={session.id === activeId ? "chat-item is-on" : "chat-item"}
                  /* The action menu is on right click: rename, pin, move, tint. */
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setMenu({ id: session.id, x: event.clientX, y: event.clientY });
                  }}
                >
                  {renaming === session.id ? (
                    <input
                      className="rename"
                      defaultValue={session.title}
                      autoFocus
                      onBlur={(event) => rename(session.id, event.currentTarget.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") rename(session.id, event.currentTarget.value);
                        // Cancelling restores the original value before losing focus:
                        // otherwise `blur` would commit what was just discarded.
                        else if (event.key === "Escape") {
                          event.currentTarget.value = session.title;
                          setRenaming(null);
                        }
                      }}
                    />
                  ) : (
                    <button
                      className="pick"
                      role="menuitem"
                      /* State and directory are in the tooltip: the line keeps
                         its whole width for the title. */
                      title={`${session.command} — ${session.cwd}${stateWord(session)}`}
                      onClick={() => {
                        setWanted(session.id);
                        setPick(null);
                      }}
                      onDoubleClick={() => setRenaming(session.id)}
                    >
                      {token && <span className="swatch" style={{ background: `var(${token})` }} />}
                      <span className="label">{session.title}</span>
                      {other && <span className="where mono">{folderName(session.cwd)}</span>}
                      {pinned && (
                        <span className="pin" aria-label={t("Pinned")}>
                          <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M9 3.5h6l-1 6 3.5 3.5v1.5h-11V13L10 9.5z" />
                            <path d="M12 14.5v6" />
                          </svg>
                        </span>
                      )}
                      {/* "finished" and "interrupted" differ: one returned its exit
                          code, the other died with the studio. */}
                      {session.state === "interrupted" && <span className="end">{t("interrupted")}</span>}
                      {session.state === "exited" && <span className="end">{t("finished")}</span>}
                      {unread && <span className="unread" aria-label={t("waiting for a reply")} />}
                    </button>
                  )}
                  <button
                    className="drop"
                    aria-label={t("Delete “{title}” for good", { title: session.title })}
                    title={t("Delete for good — the kept screen is erased")}
                    onClick={() => askClose(session)}
                  >
                    <TrashIcon />
                  </button>
                </div>
                </div>
              );
            })}
          </div>
        </Menu>
      )}

      {menu && openMenu && (
        <Menu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          {openMenu.revivable && (
            <button
              onClick={() => {
                void revive(menu.id);
                setMenu(null);
              }}
            >
              {t("Resume")}
            </button>
          )}
          <button
            onClick={() => {
              setRenaming(menu.id);
              setMenu(null);
            }}
          >
            {t("Rename")}
          </button>
          <button
            onClick={() => {
              togglePin(menu.id);
              setMenu(null);
            }}
          >
            {meta.pinned.includes(menu.id) ? t("Unpin") : t("Pin")}
          </button>
          <button
            onClick={() => {
              move(menu.id, -1);
              setMenu(null);
            }}
          >
            {t("Move up")}
          </button>
          <button
            onClick={() => {
              move(menu.id, 1);
              setMenu(null);
            }}
          >
            {t("Move down")}
          </button>
          <div className="sep" />
          <div className="chat-swatches">
            {TAB_COLORS.map((token) => (
              <button
                key={token}
                style={{ background: `var(${token})` }}
                aria-label={t("Tint {token}", { token })}
                onClick={() => {
                  setColor(menu.id, token);
                  setMenu(null);
                }}
              />
            ))}
          </div>
          <div className="sep" />
          <button
            className="danger"
            onClick={() => {
              askClose(openMenu);
              setMenu(null);
            }}
          >
            {t("Delete for good…")}
          </button>
        </Menu>
      )}

      {picker && (
        <HarnessDialog
          harnesses={harnesses}
          choice={choice ?? harnesses?.find((entry) => entry.available)?.id ?? null}
          last={meta.lastHarness}
          efforts={meta.effort ?? {}}
          onChoose={setChoice}
          onEffort={setEffort}
          onOpen={(id) => {
            const entry = harnesses?.find((candidate) => candidate.id === id);
            const level = entry ? validEffort(entry, (meta.effort ?? {})[id]) : "";
            setPicker(false);
            void create(id, level);
          }}
          onClose={() => setPicker(false)}
        />
      )}

      {inboxOpen && (
        <InboxDialog
          onClose={() => setInboxOpen(false)}
          onTake={(file) => insert(file.relative)}
        />
      )}

      {dropping && (
        <div className="chat-drop">
          <span>{t("Drop here")}</span>
        </div>
      )}

      {closing && (
        <Dialog
          eyebrow={t("Delete the session")}
          title={t("Delete “{title}”?", { title: closing.title })}
          onClose={() => setClosing(null)}
          foot={
            <>
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setClosing(null)}>{t("Cancel")}</button>
              <button
                className="btn btn-danger"
                onClick={() => {
                  void close(closing.id);
                  setClosing(null);
                }}
              >
                {t("Delete for good")}
              </button>
            </>
          }
        >
          <p className="hint" style={{ margin: 0 }}>
            {t("The agent stops and the kept screen is erased: nothing can be recovered. The files it produced stay in the Library.")}
          </p>
        </Dialog>
      )}
    </div>
  );
}

/**
 * The inbox: what was dropped for the agent.
 *
 * It shows only paths, because that is what is handed over: clicking a line
 * writes the path into the conversation, and the agent opens the file.
 */
function InboxDialog({ onClose, onTake }: {
  onClose: () => void;
  onTake: (file: InboxFile) => void;
}) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["inbox"],
    queryFn: () => api.inbox(80),
  });

  const pick = async () => {
    const chosen = await pickFile([{ name: t("File"), extensions: ["*"] }]);
    if (!chosen) return;
    try {
      const result = await api.inboxAdd(chosen);
      onTake(result);
      await refetch();
    } catch {
      // A failed drop shows as a missing line: the studio already refuses, with
      // its reason, the cases that matter (empty, too big, unreadable).
    }
  };

  return (
    <Dialog
      eyebrow={data ? t("{count} file(s) · {bytes}", { count: data.count, bytes: bytes(data.bytes) }) : "inbox"}
      title={t("Dropped for the agent")}
      hint={data?.relative}
      onClose={onClose}
      wide
      foot={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={() => void pick()}>
            {t("Drop a file…")}
          </button>
          <button className="btn btn-ghost" onClick={onClose}>{t("Close")}</button>
        </>
      }
    >
      {isLoading ? (
        <div className="skeleton" style={{ height: 160 }} />
      ) : (data?.attachments.length ?? 0) === 0 ? (
        <Empty title={t("Empty inbox")} />
      ) : (
        <div className="stack-3">
          {data?.attachments.map((file) => (
            <button key={file.name} className="doc-row" onClick={() => onTake(file)}>
              <span className="grow">
                <b className="mono">{file.name}</b>
                <span>{file.relative}</span>
              </span>
              <span className="meta num">
                {file.width ? `${file.width}×${file.height}` : bytes(file.size_bytes)}
              </span>
            </button>
          ))}
        </div>
      )}
    </Dialog>
  );
}

/** The stored level if the agent's model accepts it, else its default (""). */
function validEffort(harness: Harness, level: string | undefined): string {
  return level && harness.effort_levels.includes(level) ? level : "";
}

/**
 * A tab's state in one word, for the tooltip: "finished" and "interrupted"
 * differ, and the difference must survive a window too narrow to show it.
 */
function stateWord(session: TerminalSession): string {
  if (session.state === "interrupted") return t(" — interrupted");
  if (session.state === "exited") return t(" — finished");
  return "";
}

/**
 * The trash icon of a session line.
 *
 * A trash can rather than `×`, which says "close": this erases the kept
 * record, so the shape must say destruction.
 */
function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5l1 13.5h9l1-13.5" />
      <path d="M10.5 10v6.5M13.5 10v6.5" />
    </svg>
  );
}

/**
 * Which agent to open the session with. An unavailable entry stays readable --
 * grayed out, with its reason: hiding it would suggest the agent does not exist.
 */
function HarnessDialog({ harnesses, choice, last, efforts, onChoose, onEffort,
                       onOpen, onClose }: {
  harnesses: Harness[] | undefined;
  choice: string | null;
  last: string | undefined;
  efforts: Record<string, string>;
  onChoose: (id: string) => void;
  onEffort: (id: string, level: string) => void;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const chosen = harnesses?.find((entry) => entry.id === choice && entry.available);
  const levels = chosen?.effort_levels ?? [];
  // A stored level the model no longer accepts (catalog changed, other model)
  // falls back to the default rather than being sent and refused.
  const level = chosen ? validEffort(chosen, efforts[chosen.id]) : "";
  return (
    <Dialog
      eyebrow={t("New session")}
      title={t("With which agent?")}
      onClose={onClose}
      foot={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!chosen} onClick={() => chosen && onOpen(chosen.id)}>
            {chosen ? t("Open with {label}", { label: chosen.label }) : t("Open")}
          </button>
        </>
      }
    >
      <div className="harness-list" role="radiogroup" aria-label={t("Available agents")}>
        {(harnesses ?? []).map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="choice"
            role="radio"
            aria-checked={entry.id === chosen?.id}
            disabled={!entry.available}
            title={entry.available ? entry.detail : entry.reason}
            onClick={() => onChoose(entry.id)}
            onDoubleClick={() => entry.available && onOpen(entry.id)}
          >
            <span className="tick" />
            <span className="harness-logo" aria-hidden="true">
              {HARNESS_LOGOS[entry.id] ?? <span className="mono">{entry.label.slice(0, 1)}</span>}
            </span>
            <span className="who">
              <b>{entry.label}</b>
              <span>{entry.available ? entry.detail : entry.reason}</span>
            </span>
            {entry.id === last && <Badge>{t("last used")}</Badge>}
          </button>
        ))}
        {!harnesses && <div className="skeleton" style={{ height: 180 }} />}
      </div>

      {/* Effort is not guessed: only the levels the agent announces are
          offered, with the reason when it announces none. Leaving the default
          is a choice in itself -- the agent's own. */}
      <div className="setting-row">
        <span className="grow">
          <b>{t("Effort")}</b>
          {!chosen && <span className="hint">{t("Choose an agent first.")}</span>}
          {chosen && !levels.length && <span className="hint">{chosen.effort_reason}</span>}
        </span>
        <div className="seg" role="group" aria-label={t("Effort level")}>
          <button
            type="button"
            aria-pressed={level === ""}
            disabled={!chosen || levels.length === 0}
            onClick={() => chosen && onEffort(chosen.id, "")}
          >
            {t("default")}
          </button>
          {levels.map((entry) => (
            <button
              key={entry}
              type="button"
              aria-pressed={level === entry}
              disabled={!chosen}
              onClick={() => chosen && onEffort(chosen.id, entry)}
            >
              {entry}
            </button>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
