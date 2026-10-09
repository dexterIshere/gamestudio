/**
 * What the Chats window remembers about a tab and the server does not know.
 *
 * The server holds whether sessions exist and what they contain; how they are
 * presented -- order, tint, pinning -- and what the user has already seen
 * concern this window only. Hence `localStorage` rather than a database field,
 * for choices that would not carry over to another interface.
 */

import type { Harness } from "../api";

export const META_KEY = "gamestudio.chat";

/**
 * The tints offered for a tab's dot.
 *
 * They are labels the user picks, placed where nothing else carries color: the
 * active tab is marked by its background, not by a tint. They stay design
 * system variables -- no hard-coded value.
 */
export const TAB_COLORS = ["--accent", "--gold", "--success", "--warn", "--muted"] as const;

export interface ChatMeta {
  /** Chosen order, by id. A tab missing from it follows, by creation date. */
  order: string[];
  color: Record<string, string>;
  pinned: string[];
  /** Last `seq` looked at: beyond it, something happened unseen. */
  seen: Record<string, number>;
  /**
   * Time of the last end of turn looked at, for tabs whose transcript can be
   * read. This is the real signal: "the agent is done and waiting for you".
   * A tab without one falls back to `seen`.
   */
  seenTurn: Record<string, number>;
  /**
   * Last harness picked. It is not a default applied silently: the menu
   * remembers and marks it, so one sees at a glance what is about to open --
   * and can change it.
   */
  lastHarness?: string;
  /** The effort level chosen, per agent; remembered and shown the same way. */
  effort?: Record<string, string>;
}

const blank = (): ChatMeta => ({
  order: [],
  color: {},
  pinned: [],
  seen: {},
  seenTurn: {},
});

/**
 * The stored effort levels: string values only, per agent. A level the agent
 * no longer accepts is dropped later, when picking (`validEffort`).
 */
function efforts(raw: unknown): Record<string, string> | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const kept = Object.entries(raw).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string",
  );
  return kept.length ? Object.fromEntries(kept) : undefined;
}

export function readMeta(): ChatMeta {
  try {
    const raw = localStorage.getItem(META_KEY);
    if (!raw) return blank();
    const parsed = JSON.parse(raw) as Partial<ChatMeta>;
    return {
      order: Array.isArray(parsed.order) ? parsed.order : [],
      color: parsed.color ?? {},
      pinned: Array.isArray(parsed.pinned) ? parsed.pinned : [],
      seen: parsed.seen ?? {},
      seenTurn: parsed.seenTurn ?? {},
      lastHarness: typeof parsed.lastHarness === "string" ? parsed.lastHarness : undefined,
      effort: efforts(parsed.effort),
    };
  } catch {
    // Unreadable or refused storage (private window, blocked data) must not
    // keep the window from opening: start from a bare presentation.
    return blank();
  }
}

export function writeMeta(meta: ChatMeta): void {
  try {
    localStorage.setItem(META_KEY, JSON.stringify(meta));
  } catch {
    /* Presentation is a convenience, never data that could be lost. */
  }
}

/** The stored level if the agent's model accepts it, else its default (""). */
export function validEffort(harness: Harness, level: string | undefined): string {
  return level && harness.effort_levels.includes(level) ? level : "";
}

/**
 * Remembers the effort picked for an agent from another window (a handoff
 * dialog): the Chats window and the dialogs share one preference, the last
 * level chosen.
 */
export function rememberEffort(harness: string, level: string): void {
  const meta = readMeta();
  writeMeta({ ...meta, effort: { ...meta.effort, [harness]: level } });
}
