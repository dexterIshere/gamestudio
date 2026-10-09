/**
 * Client of the studio API.
 *
 * The address is not known at build time: the Rust shell picks a free port at
 * startup and launches the Python server on it. It publishes the two things the
 * front needs (the address, and the token that opens the API) through the
 * `api_auth` command.
 *
 * The token is the gate of the local API: without it, every request under
 * `/api/` is refused. It travels in a header when one can be set, and as a URL
 * parameter otherwise: an `<img>` tag, an `EventSource` and a `WebSocket`
 * cannot set headers (see `withToken`).
 *
 * Outside Tauri (a browser on the Vite server, to debug the front alone) there
 * is nobody to ask: the token is then read from `localStorage`, or from
 * `VITE_API_TOKEN`. That path is for development only; the application always
 * provides the token.
 */

import { tr } from "./lib/i18n";

const FALLBACK = "http://127.0.0.1:7788";
const STORAGE_KEY = "gamestudio.token";

let base: string | null = null;
let token: string | null = null;

function stored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    // Refused storage (private window, blocked data) must not stop the API
    // from answering: the shell provides the token whenever the application
    // really runs.
    return "";
  }
}

/** Remembers the token, so that a page reload does not ask for it again. */
function remember(value: string): void {
  token = value;
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    /* The in-memory token is enough. */
  }
}

/** Resolves the address and the token. Called once, at startup, before rendering. */
export async function resolveApiBase(): Promise<string> {
  if (base !== null) return base;
  // The value goes through a local: `base` is a module variable, and
  // TypeScript drops its narrowing across an `await`.
  let resolved: string;
  if ("__TAURI_INTERNALS__" in window) {
    const { invoke } = await import("@tauri-apps/api/core");
    const auth = await invoke<{ base: string; token: string }>("api_auth");
    resolved = auth.base;
    remember(auth.token);
  } else if (import.meta.env.DEV) {
    // Development server: the page comes from Vite, the API lives elsewhere.
    resolved = import.meta.env.VITE_API_BASE ?? FALLBACK;
    token = import.meta.env.VITE_API_TOKEN || stored();
  } else {
    // Built front opened in a browser: the studio server served it, so the
    // API is on the same origin, whatever port it took.
    resolved = window.location.origin;
    token = stored();
  }
  base = resolved;
  return resolved;
}

/** The current token, or an empty string outside the shell without storage. */
export function apiToken(): string {
  return token ?? stored();
}

/**
 * Adds the token to a URL that cannot carry a header.
 *
 * `<img>`, `EventSource` and `WebSocket` offer no way to set one, so the token
 * goes in the URL. Whatever observes the page can read it there, which is
 * accepted: the API only listens on the loopback, and it keeps these three
 * behind the same gate as the rest. The server does not write these URLs to
 * an access log (`gamestudio serve`), and a served file runs no script
 * (`_file`, `api/app.py`).
 */
export function withToken(url: string): string {
  const value = apiToken();
  if (!value) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(value)}`;
}

/**
 * The resolved address. Synchronous on purpose: a thumbnail URL is computed
 * during render, and awaiting a promise there would force every image
 * component to handle a loading state for nothing.
 */
export function apiBase(): string {
  if (base === null) throw new Error("API not resolved: call resolveApiBase() at startup");
  return base;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** An operation refused for lack of explicit consent to the spending. */
  get isPayment(): boolean {
    return this.status === 402;
  }

  /** The API gate: the token is missing, or no longer valid. */
  get isDenied(): boolean {
    return this.status === 403;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const value = apiToken();
  if (value) headers.set("Authorization", `Bearer ${value}`);
  if (init?.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(apiBase() + path, { ...init, headers });
  if (!response.ok) {
    // The server answers `{detail}` for every known fault; a non-JSON answer
    // means a lower-level failure, whose status is the only useful clue.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep the status */
    }
    // The server speaks English to every interface; the window translates.
    throw new ApiError(tr(detail), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Direct URL of a file: `<img src>` and the three.js loaders follow it. */
export function assetFileUrl(assetId: string): string {
  return withToken(`${apiBase()}/api/assets/${assetId}/file`);
}

/** A workspace's logo; `version` forces a re-read after a change. */
export function workspaceLogoUrl(project: string, version: number): string {
  return withToken(`${apiBase()}/api/workspace/${encodeURIComponent(project)}/logo?v=${version}`);
}

/** A document's shelf, as a URL parameter: nothing for the root. */
function shelf(folder: string): string {
  return folder ? `?folder=${encodeURIComponent(folder)}` : "";
}

/** The route of an aspect of the art direction: its board, its thread. */
function direction(project: string, aspect: string): string {
  return `/api/projects/${encodeURIComponent(project)}/direction/${encodeURIComponent(aspect)}`;
}

/** A document's route; its shelf is added as a parameter (`shelf`). */
function documentPath(project: string, name: string): string {
  return `/api/projects/${encodeURIComponent(project)}/documents/${encodeURIComponent(name)}`;
}

/**
 * An image a document cites, by its path relative to the document: a library
 * render (`../../../library/renders/…`), an image of the game.
 */
export function documentImageUrl(project: string, folder: string, src: string): string {
  const query = `folder=${encodeURIComponent(folder)}&src=${encodeURIComponent(src)}`;
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/document-image?${query}`);
}

/** The screen preview as its branch draws it; `v` changes with every render. */
export function screenPreviewUrl(project: string, folder: string, name: string,
                                 version: string): string {
  const query = `folder=${encodeURIComponent(folder)}&v=${encodeURIComponent(version)}`;
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/documents/${
    encodeURIComponent(name)}/screen/preview?${query}`);
}

export function assetPreviewUrl(assetId: string, size = 320): string {
  return withToken(`${apiBase()}/api/assets/${assetId}/preview?size=${size}`);
}

/**
 * The thumbnail of a library file.
 *
 * An SVG goes raw: the browser renders it natively, better than a server-side
 * rasterization and without depending on the optional `cairosvg`. Everything
 * else goes through the resized preview, which avoids transferring a
 * multi-megabyte image for a 120 px cell.
 */
export function thumbUrl(file: { asset_id: string; mime: string | null; name: string }, size = 320): string {
  const vector = file.mime === "image/svg+xml" || file.name.toLowerCase().endsWith(".svg");
  return vector ? assetFileUrl(file.asset_id) : assetPreviewUrl(file.asset_id, size);
}

/** The SSE stream URL: `EventSource` consumes it directly. */
export function jobStreamUrl(project?: string): string {
  return withToken(
    `${apiBase()}/api/jobs/stream${project ? `?project=${encodeURIComponent(project)}` : ""}`,
  );
}

// ----------------------------------------------------------------- types

export interface Health {
  project_root: string | null;
  data_dir: string;
  runware_key: boolean;
  blender: string | null;
  local_rigtools: Record<string, boolean>;
  queue: Record<string, number>;
  workers: number;
  workers_running: boolean;
}

export interface Recipe {
  path: string;
  project: string;
  /** The project folder on the machine; `null` for a project hosted in the studio. */
  root: string | null;
  /** Removed from the list by the user; nothing is erased. */
  hidden?: boolean;
  style?: string;
  lora?: string | null;
  characters?: string[];
  error?: string;
}

export interface Character {
  id: string;
  name: string;
  state: string;
  pipelines: string[];
  concept_asset: string | null;
  has_rig3d: boolean;
  exports: Record<string, string>;
  errors: string[];
}

export interface Asset {
  id: string;
  kind: string | null;
  mime: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface AssetInfo extends Omit<Asset, "created_at"> {
  path: string | null;
  size_bytes: number | null;
}

export interface Job {
  id: string;
  kind: string;
  project: string;
  step: string;
  state: string;
  error: string | null;
  cost_usd: number;
  updated_at: string;
}

/** A produced file, resolved to a library path, never to a hash. */
export interface JobFile {
  asset_id: string;
  kind: string | null;
  name: string;
  folder: string;
  path: string;
}

/**
 * What a job produced, in plain terms.
 *
 * `result` is a dictionary whose keys change with every operation: the report
 * extracts what reads the same everywhere. `absent` lists what was produced
 * without any project folder claiming it; hiding it would pass an invisible
 * production off as filed.
 */
export interface JobReport {
  state: string;
  error: string | null;
  cost_usd: number;
  note: string | null;
  errors: string[];
  refused: { what: string; why: string }[];
  files: JobFile[];
  absent: { asset_id: string; kind: string | null }[];
  facts: { key: string; value: string }[];
}

export interface JobDetail extends Job {
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  report: JobReport;
  created_at: string;
}

export interface QueueStatus {
  states: Record<string, number>;
  cost_usd: number;
  recent: Job[];
}

export interface LibraryFile {
  name: string;
  folder: string;
  path: string;
  asset_id: string;
  kind: string | null;
  mime: string | null;
  size_bytes: number | null;
}

export interface LibraryFolder {
  path: string;
  name: string;
  path_on_disk: string;
  files: number;
}

export interface LibraryTree {
  project: string;
  root: string;
  folder: string;
  folders: LibraryFolder[];
  files: LibraryFile[];
  documents: { name: string; folder: string; path: string }[];
  truncated: boolean;
  manifest?: Record<string, unknown> | null;
}

export interface Outcome {
  ok: boolean;
  summary: string;
  done: string[];
  refused: Record<string, string>;
}

export interface PoseTemplate {
  name: string;
  description: string;
}

export interface SpriteStyle {
  name: string;
  label: string;
  description: string;
}

export interface Queued {
  queued: string[];
  project: string;
}

/**
 * A terminal tab opened by the server.
 *
 * `seq` counts PTY reads, not lines: it lets the Chats window say "something
 * happened since you left" without interpreting anything that was displayed.
 * It does not say *what*, though: a full-screen interface redrawing its
 * counter moves it without anything happening. Hence `turn`.
 */
export interface TerminalSession {
  id: string;
  title: string;
  /** The effort level asked at opening, empty for the agent's default. */
  effort?: string;
  /** The program alone, for the label. */
  command: string;
  /** The whole argv, to relaunch the tab identically. */
  argv: string[];
  /** The tracked harness, empty when the command was given explicitly. */
  harness: string;
  cwd: string;
  /**
   * `running`, `exited`, or `interrupted`: the last one for a tab found on
   * disk, whose process did not finish but died with the studio.
   */
  state: string;
  exit_code: number | null;
  created_at: number;
  seq: number;
  /**
   * The grid the screen was drawn for.
   *
   * The replay buffer is a stream of escapes, and a TUI positions itself
   * absolutely in it: replaying it at another width only yields scattered
   * fragments. The terminal therefore sets this size before writing, then
   * fits back into its box.
   */
  cols?: number;
  rows?: number;
  /**
   * Where the agent's turn stands, when its native log can tell.
   *
   * `waiting` means "it is done, your turn": that is what the badge shows.
   * `null` for a tab without turns: a `bash`, or a harness whose log cannot be
   * read. Nothing is guessed in its place.
   */
  turn: { state: string; at: number } | null;
  /** True for a tab left on disk: a session can be reopened, not resurrected. */
  revivable: boolean;
}

/**
 * An agent that a new tab can launch.
 *
 * Availability comes from the server, where the PATH is real: the front
 * guesses nothing, it shows "available" or the reason why not.
 */
export interface Harness {
  id: string;
  label: string;
  detail: string;
  available: boolean;
  reason: string;
  /** The model the tab will launch: the effort levels are its own. */
  effort_model: string;
  /** The effort levels this model accepts. Empty: the agent exposes none. */
  effort_levels: string[];
  /** Why it exposes none, when the list is empty. */
  effort_reason: string;
}

/**
 * A workbench prompt: the phrase that produces a usable reference. The studio
 * defines them once (`service/prompts.py`), the interface offers them.
 */
export interface StudioPrompt {
  id: string;
  label: string;
  what: string;
  positive: string;
  negative: string;
  guards: string;
  width: number;
  height: number;
  pose: string;
  transparent: boolean;
  uses: string;
}

export interface RenderedPrompt {
  id: string;
  label: string;
  subject: string;
  positive: string;
  negative: string;
  width: number;
  height: number;
  pose: string;
  transparent: boolean;
  uses: string;
  what: string;
}

/**
 * A file dropped in the inbox.
 *
 * `relative` is the path to give an agent: it works from the studio root, so a
 * relative path makes sense to it.
 */
export interface InboxFile {
  name: string;
  path: string;
  relative: string;
  size_bytes: number;
  modified_at: string;
  kind: string;
  width: number | null;
  height: number | null;
}

export interface Inbox {
  dir: string;
  relative: string;
  count: number;
  bytes: number;
  attachments: InboxFile[];
}

export interface SheetPiece {
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SheetSummary {
  source?: string;
  sheet?: string;
  strategy: string;
  count: number;
  pieces: SheetPiece[];
  [key: string]: unknown;
}

/**
 * The agent context: the hand-written notes, and the regenerated briefing.
 *
 * `generated` tells them apart: a note is edited, the briefing is recomputed.
 * The Context page shows that difference, so nobody edits a report believing
 * they fix a file.
 */
export interface ContextNote {
  name: string;
  path: string;
  generated: boolean;
  exists: boolean;
  size_bytes: number | null;
  modified_at: string | null;
}

export interface Briefing {
  generated_at: string;
  path: string;
  markdown: string;
  digest: {
    counters: { projects: number; characters: number; jobs_active: number; cost_usd: number };
    /** The active project, measured: what the Context page shows first. */
    focus_entry?: {
      project: string;
      characters_total: number;
      jobs_active: number;
      cost_usd: number;
      files: number;
    } | null;
    notes: ContextNote[];
    [key: string]: unknown;
  };
}

/**
 * The workspace: one card per project, and its steps.
 *
 * A step is something the studio produced that can be looked at: a 2D
 * character, a 3D mesh, an icon sheet, a batch of frames. It carries its
 * measurements and files; the HTML report gives it a section.
 */
export interface WorkspaceStep {
  id: string;
  kind: string;
  title: string;
  subtitle: string;
  facts: [string, string][];
  files: { name: string; folder: string }[];
}

/** What is written by hand about a workspace: its displayed name, and its logo. */
export interface WorkspaceMeta {
  project: string;
  title: string;
  description: string;
  category: string;
  status: string;
  pinned: boolean;
  declared: boolean;
  /** `version` changes with the file: appended to the URL, it busts the cache. */
  logo: { path: string; version: number } | null;
}

/** What every brief handed to an agent says about itself. */
export interface Brief {
  path: string;
  prompt: string;
  /** The game folder, when the work has one: tabs open there are listed first. */
  godot?: string;
}

/** Work written into a game: the brief says where, and whether Godot is there. */
interface GameBrief extends Brief {
  godot: string;
  godot_ready: boolean;
}

/** The brief that hands off writing a skill: the user's request. */
export interface SkillBrief extends Brief {
  request: string;
  /** The studio root, where the discussion opens. */
  root: string;
}

/** The rig and animation brief of a world card, as an agent receives it. */
export interface AnimationBrief extends GameBrief {
  project: string;
  section: string;
  name: string;
  entity: string;
  title: string;
}

/** The choice of agent to hand a brief to: a new one, or an open tab. */
export interface HandoffChoice {
  harness: string;
  effort: string;
  session: string;
  /** What the user writes to the agent, sent after the line pointing to the brief. */
  message: string;
}

/** The brief to realize a VFX concept, as an agent receives it. */
export interface VfxBrief {
  project: string;
  name: string;
  title: string;
  path: string;
  prompt: string;
  scene: string;
  godot: string;
  godot_ready: boolean;
}

/**
 * A game design card (Interface, Mechanics, Art direction, VFX) is a
 * workbench: next to its text, the game's render as it is, the user's sketch
 * and the images generated for it. The render is tied to the card by its name
 * (`render_scene(name=<card>)`).
 */
export interface CardRender {
  asset_id: string;
  /** Absolute path, in the library. */
  path: string;
  /** `res://…` */
  scene: string;
  /** The scene from the root of the project folder (`client/scenes/…`). */
  file: string;
  rendered_at: string;
  width: number | null;
  height: number | null;
  /** "Render again" can redo this render as is. */
  rerender: boolean;
}

/**
 * The screen editor of an interface card: the screen is edited on its own git
 * branch (`studio/screen-<card>`), in a copy of the game; each edit is a
 * commit (`service/screens.py`).
 */
export interface ScreenNode {
  /** The path in the screen, `.` for its root. */
  path: string;
  name: string;
  /** The Godot class (`Label`, `Button`…). */
  type: string;
  /** x, y, width, height, in preview pixels. */
  rect: [number, number, number, number];
  /** The scene that declares it (`res://…`), empty if code creates it. */
  file: string;
  local: string;
  /** The root of an instanced scene. */
  instance: boolean;
  text: string;
  editable: boolean;
  /** Declared by another scene: editing it also changes the other screens. */
  shared: boolean;
}

export interface ScreenCommit {
  sha: string;
  date: string;
  subject: string;
  /** An edit made by the studio: only those can be undone here. */
  studio: boolean;
}

/** What the studio is doing right now: the window's global progress bar. */
export interface Activity {
  /** The server's clock (epoch s): elapsed times are measured against it. */
  now: number;
  renders: { project: string; scene: string; started_at: number; expected: number }[];
  /** Sections whose screens are being drawn in the background. */
  queued: { project: string; folder: string; pending: number }[];
  /** The agent writing the game's fake data, while it works. */
  agents: { project: string; id: string; title: string; started_at: number }[];
}

/** A networked game's fake server answers, served during the studio's renders. */
export interface PreviewData {
  project: string;
  /** The game's scripts that talk to a server: the studio fills data for them by itself. */
  networked: string[];
  auto: boolean;
  /** The agent writing the data, while its tab is open. */
  agent: { id: string; title: string; working: boolean } | null;
  enabled: boolean;
  exists: boolean;
  routes: string[];
  setup: string;
  misses: string[];
  paths: { folder: string; server: string; setup: string; misses: string };
}

export interface PreviewBrief extends Brief {
  routes: number;
  misses: string[];
}

/** A comment on a screen element, and where it stands with the screen's agent. */
export interface ScreenComment {
  id: number;
  path: string;
  name: string;
  type: string;
  file: string;
  text: string;
  state: "saved" | "queued" | "sent" | "done";
  created_at: string;
  sent_at: string;
}

export interface ScreenComments {
  comments: ScreenComment[];
  /** The screen agent's tab, while it is open. */
  session: TerminalSession | null;
}

export interface ScreenState {
  project: string;
  folder: string;
  name: string;
  branch: string;
  opened: boolean;
  /** The screen's `res://…`, or the current render's until it is opened. */
  scene: string;
  /** The game's screens: scenes with an interface root. */
  scenes: { file: string; root: string }[];
  base: string;
  commits: ScreenCommit[];
  preview: { path: string; rendered_at: string; width: number; height: number } | null;
  nodes: ScreenNode[];
  checkout: string;
  /** After an edit: false when nothing changed in the scene. */
  changed?: boolean;
}

export type ScreenValue = string | number | boolean | [number, number] | null;

export interface ScreenProperty {
  key: string;
  label: string;
  kind: "text" | "int" | "float" | "bool" | "color" | "vector2" | "enum" | "texture";
  group: string;
  options?: [number, string][];
  /** The value written in the scene; missing or `null`: the default one. */
  value?: ScreenValue | string;
}

export interface ScreenNodeDetail extends ScreenNode {
  properties: ScreenProperty[];
  /** The game's icons, to set on an image property. */
  icons: { res: string; file: string; size: string }[];
}

/** A card's Excalidraw sketch, and its PNG export that agents look at. */
export interface CardSketch {
  /** The `.excalidraw`, absolute path. */
  path: string;
  /** The PNG export, absolute path, or nothing while the sketch is empty. */
  png: string | null;
  /** The export relative to the card: `documentImageUrl` serves it. */
  src: string | null;
  mtime: number;
}

export interface CardSketchScene extends CardSketch {
  /** The Excalidraw scene as saved (`serializeAsJSON`), or nothing. */
  scene: Record<string, unknown> | null;
}

/** A generation's starting image: none, the sketch, the render, or a reference (`ref:<file>`). */
export type CardReference = "" | "sketch" | "render" | `ref:${string}`;
/** Where a generation's starting image came from, as the generation remembers it. */
export type CardReferenceKind = "" | "sketch" | "render" | "reference";

/** A reference image added to the card, stored next to it (`<name>.references/`). */
export interface CardReferenceImage {
  file: string;
  /** Absolute path. */
  path: string;
  width: number | null;
  height: number | null;
  size_bytes: number;
  added_at: string;
}

export interface CardGeneration {
  asset_id: string;
  /** In the library, once it is filed there. */
  path: string | null;
  prompt: string;
  model: string;
  reference: CardReferenceKind;
  created_at: string;
  width: number | null;
  height: number | null;
}

/** A failed generation of the card: its job, its error, when. */
export interface CardFailure {
  job: string;
  error: string;
  at: string;
}

export interface CardMedia {
  project: string;
  folder: string;
  name: string;
  title: string;
  /** The section as the rail names it ("Interface"). */
  section_label: string;
  render: CardRender | null;
  sketch: CardSketch | null;
  /** In the order they were added. */
  references: CardReferenceImage[];
  /** Most recent first. */
  generations: CardGeneration[];
  /** Expected images: the sum of `count` over queued or running generations. */
  pending: number;
  /** The card's generations that failed in the last 24 h, most recent first (3 at most). */
  failures: CardFailure[];
  /** A prompt seed drawn from the card: its title and what it says first. */
  prompt_seed: string;
}

export interface CardGenerateRequest {
  prompt: string;
  model: string;
  reference: CardReference;
  strength: number;
  width: number;
  height: number;
  count: number;
  negative_prompt?: string;
  /** Applies the project's style (prompt prefix, LoRA) when it has one. */
  style?: boolean;
  /** Consent to the spending: without it, the server refuses (402). */
  confirm: boolean;
}

/**
 * A game's lookdev: its art direction shown element by element. Each shader of
 * the game is a specimen, rendered by Godot itself on an offscreen bench and
 * tunable live. No verdict: what is in the game is kept; a shader to rework is
 * discussed with an agent.
 */
export interface LookdevSpecimen {
  id: string;
  category: "shader";
  title: string;
  /** From the game root. */
  file: string;
  res_path: string;
  godot: string;
  /** `canvas_item`, `spatial`, `sky`, `particles`, `include`… */
  kind: string;
  renderable: boolean;
  uniforms: number;
  users: string[];
  /** Set on the game's real object by a staging, rather than on a template shape. */
  staged: boolean;
  updated_at: string | null;
}

/**
 * The showcase of icons and props: each element of the game shown as it is, to
 * be critiqued (`service/showcase.py`). An icon is its file; a prop is drawn by
 * Godot, alone, and a button in each of its states.
 */
export type ShowcaseKind = "icons" | "props";
export type ShowcaseState = "normal" | "hover" | "pressed" | "disabled";

export interface ShowcaseItem {
  id: string;
  /** `image`: the game's file; `scene`, `stylebox`: drawn by Godot. */
  type: "image" | "scene" | "stylebox";
  /** From the game root. */
  file: string;
  title: string;
  format?: string;
  /** Source size of an image (px, or SVG units). */
  width?: number | null;
  height?: number | null;
  res_path?: string;
  /** The root class of a scene, or the type of a StyleBox. */
  root?: string;
  script?: string;
  users: string[];
  /** A prop's drawings; `same`: identical to "normal", stroke for stroke. */
  states: { state: ShowcaseState; width: number; height: number; same: boolean }[];
  /** Not drawn yet, or drawn before the last change. */
  stale?: boolean;
  rendered_at?: string;
  error?: string;
}

export interface ShowcaseFamily {
  id: string;
  label: string;
  folder: string;
  items: ShowcaseItem[];
}

export interface Showcase {
  project: string;
  kind: ShowcaseKind;
  families: ShowcaseFamily[];
  total: number;
  stale: number;
  theme: LookdevTheme;
  /** After drawing: how many props were drawn. */
  drawn?: number;
}

/**
 * The icon forge: create one icon, a whole set, or redo one, through Runware,
 * all the way into the game folder (`service/forge.py`).
 */
export type ForgeMode = "one" | "set" | "redo";

/** How a family's icons are made, so that the next one looks like them. */
export interface ForgeProfile {
  folder: string;
  count: number;
  /** `framed`: a shared canvas and a margin; `cropped`: cropped, at a shared size. */
  mode: "framed" | "cropped";
  width: number;
  height: number;
  margin: number;
  exemplar: string;
  style: string;
}

export interface ForgeFamily {
  folder: string;
  label: string;
  profile: ForgeProfile;
}

export interface ForgePiece {
  index: number;
  name: string;
  path: string;
  width: number;
  height: number;
}

export interface ForgeRequest {
  id: string;
  mode: ForgeMode;
  folder: string;
  names: string[];
  description: string;
  style: string;
  element: string;
  element_file: string;
  model: string;
  count: number;
  grid: [number, number];
  estimate_usd: number;
  notes: string[];
  split: { asset_id: string; pieces: ForgePiece[]; warnings: string[] } | null;
  adopted: { name: string; file: string; at: string }[];
  created_at: string;
  status: "running" | "ready" | "split" | "adopted" | "failed" | "empty";
  error: string;
  candidates: { asset_id: string; path: string }[];
}

export interface ForgeRequestBody {
  mode: ForgeMode;
  folder?: string;
  names?: string[];
  description?: string;
  style?: string;
  element?: string;
  model?: string;
  count?: number;
  reference?: "family" | "element" | "none";
  confirm: boolean;
}

/** A trash batch: what the studio removed from the game, and what restores it. */
export interface TrashBatch {
  id: string;
  label: string;
  at: string;
  files: string[];
}

/** The image of a candidate, or of a piece of a split sheet. */
export function forgeImageUrl(project: string, request: string,
                              which: { asset?: string; piece?: number }): string {
  const query = which.asset !== undefined
    ? `asset=${encodeURIComponent(which.asset)}` : `piece=${which.piece ?? 0}`;
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/forge/${
    encodeURIComponent(request)}/image?${query}`);
}

/** The brief of an agent discussion about a showcase element. */
export interface ShowcaseBrief extends Brief {
  project: string;
  kind: ShowcaseKind;
  element: string;
  title: string;
  section_label: string;
  family: string;
}

/** The image of a showcase element; `version` busts the cache after a drawing. */
export function showcaseImageUrl(project: string, kind: ShowcaseKind, element: string,
                                 state = "", version = ""): string {
  const query = `state=${encodeURIComponent(state)}&v=${encodeURIComponent(version)}`;
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/showcase/${kind}/${
    encodeURIComponent(element)}/image?${query}`);
}

export interface LookdevTheme {
  background: string;
  colors: string[];
  fonts: { file: string; family: string }[];
  /** The family the page is set in -- the game's default font -- with all its faces. */
  font: { family: string; faces: { file: string; weight: number; italic: boolean }[] } | null;
  /** The game's sky specimen, used as the area's background. */
  sky: string;
}

/** A font file as its own tables describe it, and what cites it in the game. */
export interface LookdevFace {
  file: string;
  family: string;
  /** The style name the font gives itself: `Regular`, `SemiBold Italic`… */
  style: string;
  weight: number;
  italic: boolean;
  glyphs: number | null;
  format: string;
  /** Scenes and scripts citing the file, directly or through a font resource. */
  uses: number;
  /** The file behind the font every Control uses by default. */
  default: boolean;
}

/** A Godot font resource (`FontVariation`, `SystemFont`, `FontFile` saved as `.tres`). */
export interface LookdevFontResource {
  file: string;
  type: string;
  base: string;
  /** Font files or system font names, in the order Godot tries them. */
  fallbacks: string[];
  system: string[];
  features: { tag: string; value: number }[];
  settings: Record<string, string>;
}

export interface LookdevTypography {
  families: { family: string; faces: LookdevFace[] }[];
  resources: LookdevFontResource[];
  default: { resource: string; base: string; family: string; size: number | null };
  /** The sizes the game's scenes, scripts and themes set, smallest first. */
  sizes: { size: number; count: number; files: string[] }[];
  /** Texts the game's scenes show, the most frequent first: the specimens. */
  samples: string[];
}

/** A main page of the game: a screen or a world the player moves to. */
export interface GameView {
  id: string;
  /** Its interface card's title, else its file's. */
  title: string;
  file: string;
  res_path: string;
  root: string;
  kind: "screen" | "world-2d" | "world-3d";
  /** The game starts on it. */
  entry: boolean;
  /** The scripts that open it. */
  opened_from: string[];
  script: string;
  nodes: number;
  /** The interface card describing it. */
  card: { name: string; title: string } | null;
  /** Changes with its scene: its image is asked for again. */
  version: string;
}

/** The aspects of the art direction a color is written for. */
export type LookdevAspect = "interface" | "materials" | "sky" | "other";

/** Where a color is written: a file, for an aspect, and the names carrying it there. */
export interface LookdevColorUse {
  file: string;
  aspect: LookdevAspect;
  count: number;
  /** A property, a theme key, a constant, a shader setting. */
  names: string[];
}

export interface LookdevColor {
  hex: string;
  /** The times it is written in the game. */
  count: number;
  aspects: Partial<Record<LookdevAspect, number>>;
  uses: LookdevColorUse[];
}

export interface LookdevPalette {
  /** The colors written in the game (scenes, scripts, resources, shader settings), the most used first. */
  colors: LookdevColor[];
  /** Every color written, counted: the share of each one is its count over this. */
  total: number;
  /** The distinct colors, past those listed. */
  distinct: number;
}

/** The two aspects that lead the art direction, each with its parts, its influences and its thread. */
export type DirectionAspect = "style" | "game";

/** A part of an aspect: one card, shown and written in its own section. */
export interface DirectionPart {
  id: string;
  name: string;
  folder: string;
  title: string;
  /** Empty until the card exists. */
  path: string;
  text: string;
  /** What the card starts from when it is first written. */
  template: string;
  /** The card exists and says more than its template. */
  written: boolean;
}

/** An image of the board: generated for the aspect's card, or dropped with it. */
export interface DirectionImage {
  /** `asset:<id>` or `ref:<file>`. */
  key: string;
  kind: "generated" | "reference";
  asset_id?: string;
  file?: string;
  path: string;
  prompt?: string;
  model?: string;
}

export interface Influence {
  id: string;
  name: string;
  /** work, game, film, book, artist, movement, place, blend, other. */
  kind: string;
  keep: string;
  avoid: string;
  /** For a blend: the influences it crosses. */
  of: string[];
  images: DirectionImage[];
  created_at: string;
}

/** Images an agent proposes to generate for an influence: the user pays them, or not. */
export interface InfluenceProposal {
  id: string;
  influence: string;
  prompt: string;
  model: string;
  count: number;
  width: number;
  height: number;
  reference: string;
  why: string;
  status: "proposed" | "queued" | "done" | "failed" | "dismissed";
  error: string;
  images: DirectionImage[];
  created_at: string;
  cost_usd: number | null;
}

export interface DirectionBoard {
  project: string;
  aspect: DirectionAspect;
  parts: DirectionPart[];
  /** Every image generated for the aspect, the newest first; `influence` is empty for the aspect as a whole. */
  gallery: (DirectionImage & { proposal: string; influence: string })[];
  /** The board's own card, where its images are filed. */
  card: { name: string; folder: string; title: string; path: string; text: string };
  influences: Influence[];
  proposals: InfluenceProposal[];
}

/** A step of the agent's work: a tool it called, or what it said on the way. */
export interface ChatStep {
  tool?: string;
  detail?: string;
  note?: string;
  at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: string;
  steps?: ChatStep[];
  state?: "running" | "done" | "stopped" | "failed";
  error?: string;
}

export interface DirectionThread {
  project: string;
  aspect: DirectionAspect;
  running: boolean;
  session: string;
  messages: ChatMessage[];
}

export interface LookdevIndex {
  project: string;
  specimens: LookdevSpecimen[];
  palette: LookdevPalette;
  direction: Record<DirectionAspect, { influences: number; written: number; parts: number }>;
  typography: LookdevTypography;
  theme: LookdevTheme;
}

/** The brief of an agent discussion about a lookdev shader. */
export interface LookdevBrief extends Brief {
  project: string;
  specimen: string;
  title: string;
  section_label: string;
}

/** The sections of the Universe a discussion can be on as a whole. */
export type LookdevTopic = "colors" | "typography" | LookdevAspect;

/** The brief of an agent discussion about a whole section of the Universe. */
export interface LookdevAspectBrief extends Brief {
  project: string;
  aspect: LookdevTopic;
  title: string;
  section_label: string;
}

/** A shader parameter, as Godot reads it. */
export interface UniformInfo {
  name: string;
  /** `float`, `int`, `bool`, `Vector2`, `Vector3`, `Vector4`, `Color`, `Object`… */
  type: string;
  /** The Godot hint: 1 = range (`hint_string`: "min,max[,step]"). */
  hint: number;
  hint_string: string;
  default: unknown;
  /** The parameter's group (`group_uniforms`, "group/subgroup"), empty outside a group. */
  group: string;
}

/** A use of the shader in the game, and the parameters it sets there. */
export interface LookdevPreset {
  id: string;
  label: string;
  file: string;
  nodes: string[];
  params: Record<string, unknown>;
  /** The material in its file (empty: the `[resource]` of a `.tres`). */
  block: string;
}

/** The screen the game is drawn for (`project.godot`): its base size, its stretch mode. */
/** A device screen a specimen can be seen on, turned the way the game holds it. */
export interface LookdevDevice {
  id: "phone" | "tablet" | "desktop";
  width: number;
  height: number;
}

export interface LookdevScreen {
  width: number;
  height: number;
  /** `window/stretch/mode`: `canvas_items`, `viewport` or `disabled`. */
  stretch: string;
  /** `window/stretch/aspect`: `keep`, `expand`, `keep_width`, `keep_height` or `ignore`. */
  aspect: string;
  devices: LookdevDevice[];
}

/** What a save wrote into the game. */
export interface LookdevSaved {
  specimen: string;
  file: string;
  preset: string;
  label: string;
  written: string[];
  unchanged: string[];
}

export interface LookdevDetail extends LookdevSpecimen {
  setup: string;
  shape: string;
  preset: string;
  presets: LookdevPreset[];
  uniform_list: UniformInfo[];
  size?: [number, number];
  materials?: number;
  /** The shader reads time (or the game object carries shaders that do): the image moves on its own. */
  animated: boolean;
  screen: LookdevScreen;
  bench: { ok: true; engine: string } | { ok: false; error: string } | null;
}

export interface LookdevFrameQuery {
  params?: Record<string, unknown>;
  preset?: string;
  shape?: string;
  yaw?: number;
  pitch?: number;
  zoom?: number;
  scale?: number;
  /** `jpg`: fast and opaque, on `background`; `webp`: keeps transparency; `png`: exact. */
  format?: "jpg" | "webp" | "png";
  /** `#rrggbb`: the background the live image is rendered on. */
  background?: string;
  /** The whole screen of a device, the game placed in it; `scale` then does not apply. */
  device?: LookdevDevice["id"];
}

export function lookdevFrameUrl(project: string, specimen: string, query: LookdevFrameQuery): string {
  const search = new URLSearchParams();
  if (query.params && Object.keys(query.params).length) search.set("params", JSON.stringify(query.params));
  for (const key of ["preset", "shape", "yaw", "pitch", "zoom", "scale", "format", "background", "device"] as const) {
    const value = query[key];
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/lookdev/${encodeURIComponent(specimen)}/frame?${search}`);
}

export function lookdevThumbUrl(project: string, specimen: string, version = ""): string {
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/lookdev/${encodeURIComponent(specimen)}/thumb?v=${encodeURIComponent(version)}`);
}

/** A view drawn by the game's engine: drawn the first time, then kept until its scene changes. */
export function gameViewImageUrl(project: string, view: string, version: string): string {
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/views/${
    encodeURIComponent(view)}/image?v=${encodeURIComponent(version)}`);
}

export function lookdevFontUrl(project: string, file: string): string {
  return withToken(`${apiBase()}/api/projects/${encodeURIComponent(project)}/lookdev-font?file=${encodeURIComponent(file)}`);
}

/** What to create in a section, handed to an agent: the request becomes a brief. */
export interface CreateBrief extends Brief {
  project: string;
  folder: string;
  section_label: string;
  template: string;
  request: string;
  /** The images added with the request, absolute paths. */
  images: string[];
  /** The world-section group where each created card is filed. */
  axes: { axis: string; axis_label: string; value: string; value_label: string }[];
}

/** A request made in a group: the images added, and the imposed filing. */
export interface CreateExtras {
  paths: string[];
  names: string[];
  /** Axis → value: the group of a world section. */
  axes?: Record<string, string>;
}

/** The brief of a discussion about a card: its subject, its images, its game. */
export interface CardBrief extends GameBrief {
  project: string;
  folder: string;
  name: string;
  title: string;
  section_label: string;
}

/** An axis value: its id is fixed, its label can be corrected. */
export interface WorldAxisValue {
  id: string;
  label: string;
}

/**
 * A filing axis: a closed set of values, declared at the project level.
 *
 * "Race" → Human, Elf, Orc. A section cites the axes it uses, and correcting
 * them from one section corrects them everywhere. A card carries one value per
 * axis, which files it without renaming it; renaming a label detaches no card.
 */
export interface WorldAxis {
  id: string;
  label: string;
  values: WorldAxisValue[];
}

/** A project axis, and the sections that use it. */
export interface WorldProjectAxis extends WorldAxis {
  sections: { id: string; label: string }[];
}

/** A world section, declared by the user. */
export interface WorldSection {
  id: string;
  label: string;
  icon: string;
  folder: string;
  documents: number;
  axes: WorldAxis[];
}

/** What a world card gave birth to: its character, in the database. */
export interface EntityCharacter {
  id: string;
  state: string;
  concept_asset: string | null;
  has_rig3d: boolean;
  exports: Record<string, string>;
  errors: string[];
}

/** A concept generated for a card. */
export interface EntityConcept {
  id: string;
  batch: string;
  prompt: string;
  model: string;
  reference: string | null;
  created_at: string;
}

/** A card's workbench: the card, its concepts, what was produced from them. */
export interface Workbench {
  project: string;
  section: { id: string; label: string; icon: string; axes: WorldAxis[] };
  name: string;
  title: string;
  path: string;
  entity: string;
  prompt: string;
  concept: string | null;
  /** How the card is filed: a declared axis, a chosen value. */
  axes: Record<string, string>;
  concepts: EntityConcept[];
  character: EntityCharacter | null;
  pending: Job[];
}

/** A world card and its entity, to link a file to its card. */
export interface WorldEntity {
  section: string;
  section_label: string;
  icon: string;
  name: string;
  title: string;
  entity: string;
  concept: string | null;
  axes: Record<string, string>;
  concepts: number;
  cover: string | null;
  character: EntityCharacter | null;
  modified_at: string | null;
}

export interface WorkspaceCard {
  project: string;
  title: string;
  description: string;
  category: string;
  status: string;
  pinned: boolean;
  recipe: string | null;
  declared: boolean;
  library: string;
  workspace: string;
  report: string;
  generated_at: string;
  counters: {
    files: number;
    characters: number;
    to_review: number;
    jobs: number;
    jobs_active: number;
    jobs_failed: number;
    cost_usd: number;
  };
  steps: WorkspaceStep[];
}

/**
 * A project document: a hand-written text, versioned with the recipe. `name`
 * is the id (the file name without `.md`), `title` what its heading says.
 */
export interface ProjectDocument {
  project: string;
  folder: string;
  name: string;
  file: string;
  path: string;
  title: string;
  size_bytes: number;
  lines: number;
  words: number;
  modified_at: string | null;
  /** The raw timestamp: it changes as soon as an agent rewrites the file. */
  mtime: number;
}

export interface ProjectDocumentText extends ProjectDocument {
  text: string;
}

export interface DocumentTemplate {
  id: string;
  label: string;
}

/**
 * A skill: a studio procedure. `vendored` flags third-party material, not
 * maintained here.
 */
export interface Skill {
  name: string;
  declared_name: string;
  description: string;
  path: string;
  skill_file: string;
  files: number;
  bytes: number;
  vendored: boolean;
  problems: string[];
}

export interface SkillsCheck {
  generated_at: string;
  ok: boolean;
  skills: number;
  vendored: number;
  files: number;
  bytes: number;
  mirror: string;
  index: string;
  problems: { skill: string; problem: string }[];
  missing: string[];
}

/** An MCP server declared somewhere, and whether it launches on this machine. */
export interface McpServer {
  name: string;
  transport: string;
  command: string;
  args: string[];
  url?: string;
  available: boolean;
  reason: string;
  studio: boolean;
  /** A third-party server the studio can identify (Blender, Godot), and whether it is ready. */
  companion: McpCompanion | null;
}

export interface McpCompanion {
  role: string;
  label: string;
  ready: boolean;
  detail: string;
}

export interface McpSource {
  id: string;
  label: string;
  path: string;
  exists: boolean;
  detail: string;
  error: string;
  /** Readable, but ignored by its CLI (Codex without trust granted to the repository). */
  warning: string;
  servers: McpServer[];
}

export interface McpConnections {
  generated_at: string;
  root: string;
  sources: McpSource[];
  counts: { project: number; global: number; available: number; unavailable: number };
  declared_here: boolean;
  notes: string[];
}

/**
 * A line of the environment diagnosis. Three states only, and a fix on those
 * that are not `ok`.
 */
export interface DoctorCheck {
  id: string;
  label: string;
  status: "ok" | "warning" | "failed";
  detail: string;
  fix: string;
  needed: boolean;
}

export interface DoctorReport {
  generated_at: string;
  ok: boolean;
  version: string;
  counts: { checks: number; ok: number; warnings: number; failures: number };
  checks: DoctorCheck[];
  blocking: string[];
  next: string[];
}

export interface MeshImport {
  asset_id: string;
  project: string;
  entity: string;
  path: string;
  library: string | null;
  attached: boolean;
  character: string | null;
  state: string | null;
  size_bytes: number;
  next: string;
}

export interface WorkspaceIndex {
  generated_at: string;
  counters: {
    projects: number;
    pinned: number;
    to_review: number;
    jobs_active: number;
    cost_usd: number;
  };
  projects: WorkspaceCard[];
}

/**
 * A batch of concepts for a world card. PAID: without `confirm`, the server
 * refuses (402) and states the amount.
 */
export interface ConceptsRequest {
  prompt: string;
  negative_prompt?: string;
  model: string;
  count: number;
  style?: boolean;
  reference_asset_id?: string | null;
  strength?: number;
  pose?: string | null;
  width: number;
  height: number;
  transparent?: boolean;
  /** Consent to the spending, given after a confirmation that states the amount. */
  confirm: boolean;
}

// -------------------------------------------------------------------- calls

/** The address of a world card, base of its workbench. */
const card = (project: string, section: string, name: string) =>
  `/api/projects/${project}/world/${encodeURIComponent(section)}/${encodeURIComponent(name)}`;

export const api = {
  health: () => get<Health>("/api/health"),
  recipes: () => get<Recipe[]>("/api/recipes"),
  /** Opens a folder of the machine as a project (created if blank). */
  openFolder: (path: string, name = "") =>
    post<{ project: string; root: string; created: boolean }>("/api/folders", { path, name }),
  /** Removes a workspace from the list; no file is erased. */
  forgetWorkspace: (project: string) =>
    post<{ project: string; root: string | null; closed: boolean; hidden: boolean }>(
      `/api/workspaces/${encodeURIComponent(project)}/forget`,
      {},
    ),
  /** Shows again every workspace removed from the list. */
  unhideWorkspaces: () => post<string[]>("/api/workspaces/unhide", {}),
  /**
   * The briefing: the studio state as an agent reads it at startup.
   * `contextRefresh` rewrites it on disk before returning it: the write is what
   * matters, since the next discussion reads that file.
   */
  context: (project = "") =>
    get<Briefing>(`/api/context${project ? `?project=${encodeURIComponent(project)}` : ""}`),
  contextRefresh: (project = "") =>
    post<Briefing>(`/api/context/refresh${project ? `?project=${encodeURIComponent(project)}` : ""}`),
  contextNotes: () => get<ContextNote[]>("/api/context/notes"),
  contextNote: (name: string) => get<{ name: string; text: string; path: string }>(`/api/context/notes/${name}`),
  saveContextNote: (name: string, text: string) =>
    request<{ name: string; path: string }>(`/api/context/notes/${name}`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),

  /** The workspace: projects as cards. */
  workspace: () => get<WorkspaceIndex>("/api/workspace"),
  characters: (project: string) => get<Character[]>(`/api/projects/${project}/characters`),
  character: (project: string, id: string) =>
    get<Record<string, unknown>>(`/api/projects/${project}/characters/${id}`),

  assets: (kind?: string, limit = 60, project?: string) =>
    get<Asset[]>(
      `/api/assets?limit=${limit}${kind ? `&kind=${kind}` : ""}` +
        (project ? `&project=${encodeURIComponent(project)}` : ""),
    ),
  asset: (id: string) => get<AssetInfo>(`/api/assets/${id}`),

  tree: (project: string, folder = "", depth = 1, pattern = "", limit = 200) =>
    get<LibraryTree>(
      `/api/library/${project}/tree?folder=${encodeURIComponent(folder)}` +
        `&depth=${depth}&pattern=${encodeURIComponent(pattern)}&limit=${limit}`,
    ),
  syncLibrary: (project: string) => post<Record<string, unknown>>(`/api/library/${project}/sync`),
  rename: (assetId: string, name: string) =>
    post<Outcome>("/api/library/rename", { asset_ids: [assetId], name }),
  remove: (assetIds: string[]) => post<Outcome>("/api/library/delete", { asset_ids: assetIds }),

  jobs: (project?: string, state?: string, limit = 50) =>
    get<Job[]>(
      `/api/jobs?limit=${limit}${project ? `&project=${project}` : ""}${state ? `&state=${state}` : ""}`,
    ),
  queue: (project?: string) =>
    get<QueueStatus>(`/api/jobs/stats${project ? `?project=${project}` : ""}`),
  job: (id: string) => get<JobDetail>(`/api/jobs/${id}`),

  /** PAID: refused without `confirm: true`. */
  build: (body: { recipe: string; characters: string[]; force: boolean; confirm: boolean }) =>
    post<Queued>("/api/produce/build", body),
  /** PAID: refused without `confirm: true`. */
  explore: (body: { recipe: string; subject: string; count: number; confirm: boolean }) =>
    post<Queued>("/api/produce/style/explore", body),

  poses: () => get<PoseTemplate[]>("/api/poses"),

  /** A recipe's text, even invalid: that is when it is needed. */
  recipeSource: (project: string) =>
    get<{ project: string; path: string; text: string; error: string | null }>(
      `/api/recipes/${encodeURIComponent(project)}/source`,
    ),
  /** Gathers everything a character produced into a zip archive. */
  bundle: (project: string, character: string) =>
    post<{ path: string; files: string[]; size_bytes: number }>(
      `/api/projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(character)}/bundle`,
    ),

  spriteStyles: () => get<SpriteStyle[]>("/api/sprites/styles"),
  skills: () => get<Skill[]>("/api/skills"),
  skillsCheck: () => get<SkillsCheck>("/api/skills/check"),
  /** The brief that hands a new skill to an agent: the user says what they want. */
  skillBrief: (request: string) => post<SkillBrief & { text: string }>("/api/skills/brief", { request }),
  skillHandoff: (request: string, body: HandoffChoice) =>
    post<SkillBrief & { session: TerminalSession }>("/api/skills/handoff", { request, ...body }),
  skillsIndex: () => post<{ path: string }>("/api/skills/index"),
  skillsSync: () => post<{ created: string[]; removed: string[]; dir: string }>(
    "/api/skills/sync",
  ),
  skill: (name: string) => get<Skill & { text: string }>(`/api/skills/${encodeURIComponent(name)}`),
  connections: () => get<McpConnections>("/api/connections"),

  // `folder`: the shelf: empty for the Documents page, `notes`, `ideas`,
  // `devlog`, `design/...`, or `world/<section>`.
  documents: (project: string, folder = "") =>
    get<ProjectDocument[]>(`/api/projects/${project}/documents${shelf(folder)}`),
  documentTemplates: () => get<DocumentTemplate[]>("/api/documents/templates"),
  document: (project: string, name: string, folder = "") =>
    get<ProjectDocumentText>(
      `/api/projects/${project}/documents/${encodeURIComponent(name)}${shelf(folder)}`,
    ),
  saveDocument: (project: string, name: string, text: string, folder = "") =>
    request<ProjectDocumentText>(
      `/api/projects/${project}/documents/${encodeURIComponent(name)}${shelf(folder)}`,
      { method: "PUT", body: JSON.stringify({ text }) },
    ),
  createDocument: (project: string, title: string, template: string, folder = "") =>
    post<ProjectDocumentText>(`/api/projects/${project}/documents${shelf(folder)}`, {
      title,
      template,
    }),
  deleteDocument: (project: string, name: string, folder = "") =>
    request<{ name: string; deleted: boolean }>(
      `/api/projects/${project}/documents/${encodeURIComponent(name)}${shelf(folder)}`,
      { method: "DELETE" },
    ),

  vfxBrief: (project: string, name: string) =>
    get<VfxBrief & { text: string }>(
      `/api/projects/${encodeURIComponent(project)}/vfx/${encodeURIComponent(name)}/brief`,
    ),
  vfxHandoff: (project: string, name: string, body: HandoffChoice) =>
    post<VfxBrief & { session: TerminalSession }>(
      `/api/projects/${encodeURIComponent(project)}/vfx/${encodeURIComponent(name)}/handoff`,
      body,
    ),
  /** The brief that hands a card's rig and animations to an agent. */
  animationBrief: (project: string, section: string, name: string) =>
    get<AnimationBrief & { text: string }>(`${card(project, section, name)}/animation/brief`),
  animationHandoff: (project: string, section: string, name: string, body: HandoffChoice) =>
    post<AnimationBrief & { session: TerminalSession }>(
      `${card(project, section, name)}/animation/handoff`, body,
    ),

  // A game design card's workbench: `folder` is its section
  // (`design/interface`), never empty.
  cardMedia: (project: string, folder: string, name: string) =>
    get<CardMedia>(`${documentPath(project, name)}/media${shelf(folder)}`),
  cardSketch: (project: string, folder: string, name: string) =>
    get<CardSketchScene>(`${documentPath(project, name)}/sketch${shelf(folder)}`),
  /** `png`: the export as a data URL (`data:image/png;base64,…`), `null` for an empty sketch. */
  saveCardSketch: (project: string, folder: string, name: string,
                    scene: Record<string, unknown> | null, png: string | null) =>
    request<CardSketch>(`${documentPath(project, name)}/sketch${shelf(folder)}`, {
      method: "PUT", body: JSON.stringify({ scene, png }),
    }),
  /** Adds a reference image to the card: dropped, pasted or picked. */
  addCardReference: (project: string, folder: string, name: string, file: File | Blob,
                      filename: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<CardReferenceImage & { duplicate: boolean }>(
      `${documentPath(project, name)}/references${shelf(folder)}`, { method: "POST", body: form },
    );
  },
  /** Stores an image from disk with the card (a dropped file, whose path Tauri gives). */
  addCardReferencePath: (project: string, folder: string, name: string, path: string) =>
    post<CardReferenceImage & { duplicate: boolean }>(
      `${documentPath(project, name)}/references/path${shelf(folder)}`, { path },
    ),
  deleteCardReference: (project: string, folder: string, name: string, file: string) =>
    request<{ file: string; deleted: boolean }>(
      `${documentPath(project, name)}/references/${encodeURIComponent(file)}${shelf(folder)}`,
      { method: "DELETE" },
    ),
  /** Renders the card's scene again, with the engine, offscreen. Free. */
  rerenderCard: (project: string, folder: string, name: string) =>
    post<CardRender>(`${documentPath(project, name)}/render${shelf(folder)}`, {}),
  // The screen editor of an interface card.
  screenState: (project: string, folder: string, name: string) =>
    get<ScreenState>(`${documentPath(project, name)}/screen${shelf(folder)}`),
  screenOpen: (project: string, folder: string, name: string, scene = "") =>
    post<ScreenState>(`${documentPath(project, name)}/screen/open${shelf(folder)}`, { scene }),
  screenRender: (project: string, folder: string, name: string) =>
    post<ScreenState>(`${documentPath(project, name)}/screen/render${shelf(folder)}`, {}),
  screenNode: (project: string, folder: string, name: string, path: string) =>
    get<ScreenNodeDetail>(
      `${documentPath(project, name)}/screen/node${shelf(folder)}&path=${encodeURIComponent(path)}`,
    ),
  screenEdit: (project: string, folder: string, name: string, path: string,
               changes: Record<string, ScreenValue>) =>
    post<ScreenState>(`${documentPath(project, name)}/screen/edit${shelf(folder)}`,
      { path, changes }),
  screenUndo: (project: string, folder: string, name: string) =>
    post<ScreenState>(`${documentPath(project, name)}/screen/undo${shelf(folder)}`, {}),
  activity: (project: string) =>
    get<Activity>(`/api/activity?project=${encodeURIComponent(project)}`),
  screensWarm: (project: string, folder: string) =>
    post<{ pending: number; started: boolean }>(
      `/api/projects/${encodeURIComponent(project)}/screens/warm?folder=${
        encodeURIComponent(folder)}`, {}),
  previewData: (project: string) =>
    get<PreviewData>(`/api/projects/${encodeURIComponent(project)}/preview-data`),
  previewDataEnable: (project: string, enabled: boolean) =>
    post<PreviewData>(`/api/projects/${encodeURIComponent(project)}/preview-data`, { enabled }),
  previewDataBrief: (project: string) =>
    get<PreviewBrief>(`/api/projects/${encodeURIComponent(project)}/preview-data/brief`),
  previewDataHandoff: (project: string, body: HandoffChoice) =>
    post<PreviewBrief & { session: TerminalSession }>(
      `/api/projects/${encodeURIComponent(project)}/preview-data/handoff`, body),
  screenComments: (project: string, folder: string, name: string) =>
    get<ScreenComments>(`${documentPath(project, name)}/screen/comments${shelf(folder)}`),
  screenCommentAdd: (project: string, folder: string, name: string, path: string, text: string,
                     send: boolean) =>
    post<ScreenComments>(`${documentPath(project, name)}/screen/comments${shelf(folder)}`,
                         { path, text, send }),
  screenCommentsSend: (project: string, folder: string, name: string, ids: number[]) =>
    post<ScreenComments>(`${documentPath(project, name)}/screen/comments/send${shelf(folder)}`,
                         { ids }),
  screenCommentsRemove: (project: string, folder: string, name: string, ids: number[]) =>
    post<ScreenComments>(`${documentPath(project, name)}/screen/comments/remove${shelf(folder)}`,
                         { ids }),
  // The showcase of icons and props.
  showcase: (project: string, kind: ShowcaseKind) =>
    get<Showcase>(`/api/projects/${encodeURIComponent(project)}/showcase/${kind}`),
  renderShowcase: (project: string, force = false) =>
    post<Showcase>(`/api/projects/${encodeURIComponent(project)}/showcase/props/render${
      force ? "?force=true" : ""}`, {}),
  deleteShowcase: (project: string, kind: ShowcaseKind, element: string) =>
    request<{ batch: TrashBatch; users: string[] }>(`/api/projects/${encodeURIComponent(project)}/showcase/${
      kind}/${encodeURIComponent(element)}`, { method: "DELETE" }),
  deleteShowcaseFamily: (project: string, kind: ShowcaseKind, family: string) =>
    request<{ batch: TrashBatch; users: string[]; count: number }>(`/api/projects/${
      encodeURIComponent(project)}/showcase/${kind}/family/${encodeURIComponent(family)}`,
      { method: "DELETE" }),
  trash: (project: string) =>
    get<TrashBatch[]>(`/api/projects/${encodeURIComponent(project)}/trash`),
  restoreTrash: (project: string, batch: string) =>
    post<{ restored: string[] }>(`/api/projects/${encodeURIComponent(project)}/trash/${
      encodeURIComponent(batch)}/restore`, {}),
  forge: (project: string) => get<ForgeRequest[]>(`/api/projects/${encodeURIComponent(project)}/forge`),
  forgeFamilies: (project: string) =>
    get<ForgeFamily[]>(`/api/projects/${encodeURIComponent(project)}/forge-families`),
  /** PAID: refused without `confirm: true`. */
  forgeRequest: (project: string, body: ForgeRequestBody) =>
    post<ForgeRequest>(`/api/projects/${encodeURIComponent(project)}/forge`, body),
  forgeSplit: (project: string, request: string, assetId: string) =>
    post<ForgeRequest>(`/api/projects/${encodeURIComponent(project)}/forge/${
      encodeURIComponent(request)}/split`, { asset_id: assetId }),
  forgeAdopt: (project: string, request: string, picks: { source: string; name: string }[]) =>
    post<ForgeRequest & { written: string[]; notes: string[] }>(`/api/projects/${
      encodeURIComponent(project)}/forge/${encodeURIComponent(request)}/adopt`, { picks }),
  forgeClose: (project: string, request: string) =>
    post<ForgeRequest>(`/api/projects/${encodeURIComponent(project)}/forge/${
      encodeURIComponent(request)}/close`, {}),
  showcaseBrief: (project: string, kind: ShowcaseKind, element: string) =>
    get<ShowcaseBrief & { text: string }>(`/api/projects/${encodeURIComponent(project)}/showcase/${
      kind}/${encodeURIComponent(element)}/brief`),
  showcaseHandoff: (project: string, kind: ShowcaseKind, element: string, body: HandoffChoice) =>
    post<ShowcaseBrief & { session: TerminalSession }>(`/api/projects/${
      encodeURIComponent(project)}/showcase/${kind}/${encodeURIComponent(element)}/handoff`, body),
  /** PAID: refused without `confirm: true`. */
  generateCard: (project: string, folder: string, name: string, body: CardGenerateRequest) =>
    post<Queued & { reference_asset_id: string | null }>(
      `${documentPath(project, name)}/generate${shelf(folder)}`, body,
    ),
  gameViews: (project: string) =>
    get<{ project: string; views: GameView[] }>(`/api/projects/${encodeURIComponent(project)}/views`),
  lookdev: (project: string) => get<LookdevIndex>(`/api/projects/${encodeURIComponent(project)}/lookdev`),
  lookdevSpecimen: (project: string, specimen: string) =>
    get<LookdevDetail>(`/api/projects/${encodeURIComponent(project)}/lookdev/${encodeURIComponent(specimen)}`),
  setLookdevState: (project: string, specimen: string, body: Partial<{
    setup: string; shape: string; preset: string;
  }>) =>
    request<LookdevSpecimen>(
      `/api/projects/${encodeURIComponent(project)}/lookdev/${encodeURIComponent(specimen)}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  /** Writes settings into the game: the use's material, or the shader's defaults (`default`). */
  saveLookdev: (project: string, specimen: string, body: { params: Record<string, unknown>; preset: string }) =>
    post<LookdevSaved>(`/api/projects/${encodeURIComponent(project)}/lookdev/${
      encodeURIComponent(specimen)}/save`, body),
  lookdevBrief: (project: string, specimen: string) =>
    get<LookdevBrief & { text: string }>(`/api/projects/${encodeURIComponent(project)}/lookdev/${
      encodeURIComponent(specimen)}/brief`),
  lookdevHandoff: (project: string, specimen: string, body: HandoffChoice) =>
    post<LookdevBrief & { session: TerminalSession }>(`/api/projects/${
      encodeURIComponent(project)}/lookdev/${encodeURIComponent(specimen)}/handoff`, body),
  directionBoard: (project: string, aspect: DirectionAspect) =>
    get<DirectionBoard>(`${direction(project, aspect)}`),
  setInfluence: (project: string, aspect: DirectionAspect, body: {
    name: string; influence?: string; kind?: string; keep?: string; avoid?: string; of?: string[];
  }) => post<Influence>(`${direction(project, aspect)}/influences`, body),
  /** A human gesture: the influence leaves the board, its images stay with the card. */
  removeInfluence: (project: string, aspect: DirectionAspect, influence: string) =>
    request<DirectionBoard>(`${direction(project, aspect)}/influences/${encodeURIComponent(influence)}`,
      { method: "DELETE" }),
  /** A human gesture: a dropped image no other influence shows is deleted. */
  removeInfluenceImage: (project: string, aspect: DirectionAspect, influence: string, key: string) =>
    request<DirectionBoard>(`${direction(project, aspect)}/influences/${encodeURIComponent(influence)}/images?key=${
      encodeURIComponent(key)}`, { method: "DELETE" }),
  addInfluenceImage: (project: string, aspect: DirectionAspect, influence: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DirectionBoard>(
      `${direction(project, aspect)}/influences/${encodeURIComponent(influence)}/references`,
      { method: "POST", body: form });
  },
  addInfluenceImagePath: (project: string, aspect: DirectionAspect, influence: string, path: string) =>
    post<DirectionBoard>(
      `${direction(project, aspect)}/influences/${encodeURIComponent(influence)}/references/path`, { path }),
  /** PAID: refused without `confirm: true`. */
  payProposal: (project: string, aspect: DirectionAspect, proposal: string, body: {
    prompt?: string; model?: string; count?: number; confirm: boolean;
  }) => post<InfluenceProposal>(`${direction(project, aspect)}/proposals/${encodeURIComponent(proposal)}/pay`, body),
  dismissProposal: (project: string, aspect: DirectionAspect, proposal: string) =>
    post<InfluenceProposal>(`${direction(project, aspect)}/proposals/${encodeURIComponent(proposal)}/dismiss`),
  directionThread: (project: string, aspect: DirectionAspect) =>
    get<DirectionThread>(`${direction(project, aspect)}/chat`),
  directionSend: (project: string, aspect: DirectionAspect, text: string, lang: string, model = "") =>
    post<DirectionThread>(`${direction(project, aspect)}/chat`, { text, lang, model }),
  /** A model looks at the influences' images and writes the prompt: a proposal, never paid. */
  draftImages: (project: string, aspect: DirectionAspect, body: {
    request: string; influences: string[] | null; count: number; model: string; lang: string;
  }) => post<InfluenceProposal>(`${direction(project, aspect)}/draft`, body),
  directionStop: (project: string, aspect: DirectionAspect) =>
    post<DirectionThread>(`${direction(project, aspect)}/chat/stop`),
  directionReset: (project: string, aspect: DirectionAspect) =>
    post<DirectionThread>(`${direction(project, aspect)}/chat/reset`),
  lookdevAspectBrief: (project: string, aspect: LookdevTopic) =>
    get<LookdevAspectBrief & { text: string }>(`/api/projects/${encodeURIComponent(project)}/lookdev-aspects/${
      encodeURIComponent(aspect)}/brief`),
  lookdevAspectHandoff: (project: string, aspect: LookdevTopic, body: HandoffChoice) =>
    post<LookdevAspectBrief & { session: TerminalSession }>(`/api/projects/${
      encodeURIComponent(project)}/lookdev-aspects/${encodeURIComponent(aspect)}/handoff`, body),
  /**
   * The brief that hands an agent what to create in the section. Free.
   * `extras.paths`: inbox (or disk) paths; `extras.names`: the names they had.
   */
  createBrief: (project: string, folder: string, request: string,
                extras: CreateExtras = { paths: [], names: [] }) =>
    post<CreateBrief & { text: string }>(
      `/api/projects/${encodeURIComponent(project)}/document-brief${shelf(folder)}`,
      { request, images: extras.paths, names: extras.names, axes: extras.axes ?? {} },
    ),
  createHandoff: (project: string, folder: string, request: string,
                  extras: CreateExtras, body: HandoffChoice) =>
    post<CreateBrief & { session: TerminalSession }>(
      `/api/projects/${encodeURIComponent(project)}/document-handoff${shelf(folder)}`,
      { request, images: extras.paths, names: extras.names, axes: extras.axes ?? {}, ...body },
    ),
  cardBrief: (project: string, folder: string, name: string) =>
    get<CardBrief & { text: string }>(`${documentPath(project, name)}/brief${shelf(folder)}`),
  cardHandoff: (project: string, folder: string, name: string, body: HandoffChoice) =>
    post<CardBrief & { session: TerminalSession }>(
      `${documentPath(project, name)}/handoff${shelf(folder)}`, body,
    ),

  world: (project: string) => get<WorldSection[]>(`/api/projects/${project}/world`),
  createWorldSection: (project: string, label: string, icon: string) =>
    post<WorldSection>(`/api/projects/${project}/world`, { label, icon }),
  updateWorldSection: (project: string, id: string, body: { label?: string; icon?: string }) =>
    request<WorldSection>(`/api/projects/${project}/world/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** The project's axes: those a section reuses instead of rewriting them. */
  worldAxes: (project: string) =>
    get<WorldProjectAxis[]>(`/api/projects/${encodeURIComponent(project)}/world-axes`),
  /** Forgets an axis that no section uses any more. */
  forgetWorldAxis: (project: string, axis: string) =>
    request<WorldAxis & { forgotten: boolean }>(
      `/api/projects/${encodeURIComponent(project)}/world-axes/${encodeURIComponent(axis)}`,
      { method: "DELETE" },
    ),
  /**
   * The axes a section uses: a project axis id, or an axis corrected at the
   * project level. Renaming detaches no card; removing a value removes it
   * wherever the axis is used.
   */
  setWorldAxes: (project: string, section: string, axes: (WorldAxis | string)[]) =>
    request<WorldSection & { detached: number; detached_in: Record<string, number> }>(
      `/api/projects/${project}/world/${encodeURIComponent(section)}/axes`,
      { method: "PUT", body: JSON.stringify({ axes }) },
    ),
  /** Removes a section; `force` takes its cards and their workbenches along. */
  deleteWorldSection: (project: string, id: string, force = false) =>
    request<WorldSection & { deleted: boolean; cards: string[] }>(
      `/api/projects/${project}/world/${encodeURIComponent(id)}${force ? "?force=true" : ""}`,
      { method: "DELETE" },
    ),

  entities: (project: string) => get<WorldEntity[]>(`/api/projects/${project}/entities`),
  workbench: (project: string, section: string, name: string) =>
    get<Workbench>(`${card(project, section, name)}/workbench`),
  /** PAID: refused without `confirm: true`. */
  concepts: (project: string, section: string, name: string, body: ConceptsRequest) =>
    post<Queued>(`${card(project, section, name)}/concepts`, body),
  chooseConcept: (project: string, section: string, name: string, assetId: string | null) =>
    request<Workbench>(`${card(project, section, name)}/concept`, {
      method: "PUT",
      body: JSON.stringify({ asset_id: assetId }),
    }),
  /** Files a card along its section's axes; only the axes given change. */
  setEntityAxes: (project: string, section: string, name: string,
                  values: Record<string, string>) =>
    request<Workbench>(`${card(project, section, name)}/axes`, {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  /** PAID: refused without `confirm: true`. */
  realize: (project: string, section: string, name: string,
            body: { mesh_model: string; face_limit: number; confirm: boolean }) =>
    post<Queued>(`${card(project, section, name)}/realize`, body),
  attachMesh: (project: string, section: string, name: string, path: string, replace = false) =>
    post<MeshImport>(`${card(project, section, name)}/mesh`, { path, replace }),

  workspaceDeclaration: (project: string) =>
    get<WorkspaceMeta>(`/api/workspace/${encodeURIComponent(project)}/meta`),
  setWorkspaceLogo: (project: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<WorkspaceMeta>(`/api/workspace/${encodeURIComponent(project)}/logo`, {
      method: "PUT",
      body: form,
    });
  },
  clearWorkspaceLogo: (project: string) =>
    request<WorkspaceMeta>(`/api/workspace/${encodeURIComponent(project)}/logo`, {
      method: "DELETE",
    }),
  renderSprites: (body: Record<string, unknown>) => post<Queued>("/api/produce/sprites", body),

  inspectSheet: (body: Record<string, unknown>) => post<SheetSummary>("/api/sheet/inspect", body),
  importSheet: (body: Record<string, unknown>) => post<SheetSummary>("/api/import/sheet", body),

  doctor: () => get<DoctorReport>("/api/doctor"),
  prompts: () => get<StudioPrompt[]>("/api/prompts"),
  renderPrompt: (id: string, subject: string, stylePrefix = "", styleNegative = "") =>
    get<RenderedPrompt>(
      `/api/prompts/${encodeURIComponent(id)}?subject=${encodeURIComponent(subject)}` +
        `&style_prefix=${encodeURIComponent(stylePrefix)}` +
        `&style_negative=${encodeURIComponent(styleNegative)}`,
    ),

  inbox: (limit = 50) => get<Inbox>(`/api/inbox?limit=${limit}`),
  inboxAdd: (path: string) =>
    post<InboxFile & { duplicate: boolean }>("/api/inbox/add", { path }),
  uploadToInbox: async (file: File | Blob, filename = "capture.png") => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<InboxFile & { duplicate: boolean }>("/api/inbox/attachment", {
      method: "POST",
      body: form,
    });
  },

  terminalHarnesses: () => get<Harness[]>("/api/terminal/harnesses"),
  terminalSessions: () => get<TerminalSession[]>("/api/terminal/sessions"),
  terminalCreate: (body: Record<string, unknown> = {}) =>
    post<TerminalSession>("/api/terminal/sessions", body),
  terminalRename: (id: string, title: string) =>
    request<TerminalSession>(`/api/terminal/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  terminalClose: (id: string) =>
    request<{ id: string; closed: boolean }>(`/api/terminal/sessions/${id}`, { method: "DELETE" }),
  /** Reopens a tab left on disk: same command, same directory, new session. */
  terminalRevive: (id: string) =>
    post<TerminalSession>(`/api/terminal/sessions/${id}/revive`),
  /** A tab's stream URL: a WebSocket, not an `EventSource`. */
  terminalStreamUrl: (id: string) =>
    withToken(`${apiBase()}/api/terminal/sessions/${id}/stream`).replace(/^http/, "ws"),
};
