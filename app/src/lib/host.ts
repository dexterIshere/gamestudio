/**
 * What the shell can do and a web page cannot.
 *
 * The front also runs in a plain browser while developing the front alone
 * (`npm run dev`, no Tauri window). These functions degrade cleanly there
 * instead of failing on a missing import.
 */

import { lang, t } from "./i18n";

export const inTauri = (): boolean => "__TAURI_INTERNALS__" in window;

/** Tells the shell the interface language: its menu and notifications follow it. */
export async function syncShellLang(): Promise<void> {
  if (!inTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("set_language", { lang });
}

/** Picks a file. Returns `null` outside the shell, where typing the path remains. */
export async function pickFile(
  filters: { name: string; extensions: string[] }[],
): Promise<string | null> {
  if (!inTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const chosen = await open({ multiple: false, filters });
  return typeof chosen === "string" ? chosen : null;
}

/** Picks a folder. Returns `null` outside the shell, where typing the path remains. */
export async function pickFolder(title: string): Promise<string | null> {
  if (!inTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const chosen = await open({ directory: true, multiple: false, title });
  return typeof chosen === "string" ? chosen : null;
}

/**
 * Shows the Chats window.
 *
 * Without the shell there is no second window: the same page opens in a
 * browser tab, which is enough to develop the Chats front without Tauri.
 */
export async function showChat(): Promise<void> {
  if (!inTauri()) {
    window.open("./chat.html", "_blank");
    return;
  }
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("show_chat");
}

/** The storage key through which the studio tells the Chats window which tab to show. */
export const CHAT_FOCUS_KEY = "gamestudio.chat.focus";

/**
 * Shows the Chats window on a given tab.
 *
 * Both windows share their storage: the wanted tab is left there, and the
 * Chats window reads it on the `storage` event or when it comes to the front.
 */
export async function focusChat(sessionId: string): Promise<void> {
  try {
    localStorage.setItem(CHAT_FOCUS_KEY, sessionId);
  } catch {
    /* Without storage the window opens on its current tab. */
  }
  await showChat();
}

/**
 * Hides or shows the studio window again. Returns its new state.
 *
 * `null` outside the shell: there is no second window to toggle, and the
 * button is disabled rather than lying about what it does.
 *
 * Hiding the studio does not stop it: "Quit", in the system tray, stops the
 * application and its server. The button makes room for the Chats, it quits
 * nothing.
 */
export async function toggleStudio(): Promise<boolean | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("toggle_studio");
}

/**
 * Whether the Chats window is pinned, as the compositor sees it.
 *
 * `null` when there is nobody to ask: outside the shell, or on a machine whose
 * window manager knows no pinning. The button is then disabled, since it could
 * not keep its promise.
 */
export async function chatPinned(): Promise<boolean | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  try {
    return await invoke<boolean | null>("chat_pinned");
  } catch {
    return null;
  }
}

/**
 * Pins or unpins the Chats window. Returns its new state.
 *
 * Pinned, it floats above a tiling that takes back the full width, and follows
 * every workspace of its screen. Unpinned, it is tiled like any other window
 * of its workspace (its share of the layout, no imposed width) and lives on
 * that workspace only. It starts unpinned: only this button pins it. No
 * compositor rule describes it, so a config reload does not reset the state,
 * which holds as long as the application runs.
 */
export async function toggleChatPin(): Promise<boolean | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("toggle_chat_pin");
}

/**
 * Shows a path in the system file manager.
 *
 * Never rejects: returns `null` when the file manager opened, otherwise the
 * reason it refused (outside the shell, missing permission, path gone), which
 * the caller reports to the user. A button that silently does nothing looks
 * broken.
 */
export async function revealPath(path: string): Promise<string | null> {
  if (!inTauri()) return t("Outside the app: no file manager.");
  try {
    const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
    await revealItemInDir(path);
    return null;
  } catch (error) {
    // The shell rejects with a string, not an `Error`.
    return error instanceof Error ? error.message : String(error);
  }
}

/** An area that accepts files dropped from the file manager. */
export interface FileDropZone {
  /** The pointer carries files over the area (`true`), or has left it. */
  hover: (over: boolean) => void;
  /** The paths dropped on the area. */
  drop: (paths: string[]) => void;
}

const dropZones = new Map<Element, FileDropZone>();
let dropListening: Promise<unknown> | null = null;
let hoveredZone: Element | null = null;
/** The last point of a DOM `dragover`, in CSS pixels, for the length of a drag. */
let dragPoint: { x: number; y: number } | null = null;

/**
 * The area under the pointer: the deepest of those containing the point (a
 * drop panel inside another).
 *
 * The DOM point comes first: under WebKitGTK, wry lets the motion through
 * (`drag-motion`), so `dragover` gives it exactly, in CSS pixels. The shell's
 * position is only a fallback: it claims to be physical, but under GTK wry
 * fills it with the widget's logical coordinates, so it is only used where
 * the DOM sees nothing (WebView2, in physical pixels).
 */
function zoneAt(position: { x: number; y: number }): Element | null {
  const ratio = window.devicePixelRatio || 1;
  const point = dragPoint ?? { x: position.x / ratio, y: position.y / ratio };
  const hit = document.elementFromPoint(point.x, point.y);
  if (!hit) return null;
  let found: Element | null = null;
  for (const element of dropZones.keys()) {
    if (element.contains(hit) && (!found || found.contains(element))) found = element;
  }
  return found;
}

function hoverZone(element: Element | null): void {
  if (element === hoveredZone) return;
  if (hoveredZone) dropZones.get(hoveredZone)?.hover(false);
  hoveredZone = element;
  if (element) dropZones.get(element)?.hover(true);
}

/** A drag is over: no hovered area, no point. */
function endDrag(): void {
  hoverZone(null);
  dragPoint = null;
}

const trackDrag = (event: DragEvent) => {
  dragPoint = { x: event.clientX, y: event.clientY };
};

// A drag inside the page ends in the DOM: its point must not serve the next
// drop.
const forgetDrag = () => {
  dragPoint = null;
};

/**
 * Receives on `element` the files dragged from the file manager. Returns a
 * function that stops listening.
 *
 * In the shell this drop never reaches the page: wry takes it on the GTK
 * `drag-drop` signal, and neither `drop` nor its paths go through the DOM. The
 * webview reports it, with real paths: one listener serves every area of the
 * window, and the drop goes to the one under the pointer. Outside the shell
 * there is nothing to listen to: the DOM `drop` carries the bytes, and the
 * caller keeps handling it.
 */
export function listenFileDrop(element: Element, zone: FileDropZone): () => void {
  if (!inTauri()) return () => {};
  dropZones.set(element, zone);
  dropListening ??= import("@tauri-apps/api/webview")
    .then(({ getCurrentWebview }) => {
      window.addEventListener("dragover", trackDrag, true);
      window.addEventListener("drop", forgetDrag, true);
      window.addEventListener("dragend", forgetDrag, true);
      return getCurrentWebview().onDragDropEvent(({ payload }) => {
        if (payload.type === "enter" || payload.type === "over") {
          hoverZone(zoneAt(payload.position));
        } else if (payload.type === "drop") {
          const target = zoneAt(payload.position);
          endDrag();
          if (target && payload.paths.length) dropZones.get(target)?.drop(payload.paths);
        } else {
          endDrag();
        }
      });
    })
    .catch(() => {
      // Listening is retried with the next area: a lost drop is visible.
      dropListening = null;
    });
  return () => {
    if (dropZones.get(element) !== zone) return;
    if (hoveredZone === element) {
      hoveredZone = null;
      zone.hover(false);
    }
    dropZones.delete(element);
  };
}

/**
 * The clipboard text.
 *
 * In the shell GTK reads it: under WebKitGTK, `navigator.clipboard` opens a
 * "Paste" menu to confirm on every read. Outside the shell, or if the native
 * read fails, the browser API takes over.
 */
export async function readClipboard(): Promise<string> {
  if (inTauri()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return await invoke<string>("clipboard_read");
    } catch {
      // Platform without a native read: the browser API takes over.
    }
  }
  return navigator.clipboard.readText();
}

/** Writes to the clipboard, the same way as `readClipboard`. */
export async function writeClipboard(text: string): Promise<void> {
  if (inTauri()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("clipboard_write", { text });
      return;
    } catch {
      // Same: the browser API takes over.
    }
  }
  await navigator.clipboard.writeText(text);
}

/** How far the studio lags behind its code. */
export interface UpdateStatus {
  /** The application needs a rebuild, or was rebuilt without a restart. */
  app: boolean;
  /** The server runs Python code changed since it started. */
  server: boolean;
}

/**
 * How far the studio lags behind its code, as the shell measures it.
 *
 * `null` outside the shell or when the measure fails: there is then nothing to
 * offer, and the button stays hidden rather than announce an update it could
 * not perform.
 */
export async function updateStatus(): Promise<UpdateStatus | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  try {
    return await invoke<UpdateStatus>("update_status");
  } catch {
    return null;
  }
}

/** A rebuild phase: Python dependencies, interface, application. */
export interface UpdatePhase {
  id: string;
  label: string;
  state: "pending" | "running" | "done";
}

/** The running rebuild, as the shell tracks it. */
export interface UpdateProgress {
  state: "idle" | "running" | "done" | "failed";
  /** From 0 to 1: each phase weighs the time it took last time. */
  progress: number;
  phases: UpdatePhase[];
  /** The last line the build wrote. */
  line: string;
  /** The last lines, to understand a failure. */
  tail: string[];
  /** Seconds since the start. */
  elapsed: number;
  error: string | null;
}

/** Starts the rebuild; what follows arrives through `onUpdateProgress`. */
export async function updateBuild(): Promise<UpdateProgress | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<UpdateProgress>("update_build");
}

/** The rebuild state, for a window that opens midway. */
export async function updateProgress(): Promise<UpdateProgress | null> {
  if (!inTauri()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<UpdateProgress>("update_progress");
}

/** Listens to the rebuild progress. Returns a function that stops listening. */
export async function onUpdateProgress(listener: (progress: UpdateProgress) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<UpdateProgress>("update-progress", (event) => listener(event.payload));
}

/**
 * Restarts the studio and its server on what was just built. The windows close
 * at once, and the new ones open a few seconds later.
 */
export async function updateNow(): Promise<void> {
  if (!inTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("update_now");
}

/** Opens the studio as it is, without the update that failed. */
export async function updateSkip(): Promise<void> {
  if (!inTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("update_skip");
}
