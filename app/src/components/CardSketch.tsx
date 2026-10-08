/**
 * A card's sketch: an Excalidraw, saved with it, exported as PNG for agents.
 *
 * The user draws what they are talking about: a screen, a layout, an effect.
 * Each change is saved on its own once editing pauses, in two forms stored
 * next to the card: the scene (`<card>.sketch.excalidraw`), which the editor
 * reopens, and its image (`<card>.sketch.png`, on white), which the agent of
 * the discussion looks at.
 *
 * Excalidraw weighs several megabytes: it only loads when a sketch opens, in a
 * separate chunk, with its stylesheet. Its drawing fonts are served by the
 * application (`excalidraw/fonts/`, see `vite.config.ts`): the shell's CSP
 * refuses the CDN it would otherwise fetch them from.
 */

import {
  Component,
  Suspense,
  lazy,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import type {
  AppState,
  BinaryFiles,
  DataURL,
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
  ExcalidrawProps,
} from "@excalidraw/excalidraw/types";
import type { ExcalidrawElement, FileId, NonDeleted } from "@excalidraw/excalidraw/element/types";
import { assetFileUrl, type CardRender, type CardSketchScene } from "../api";
import { useCardSketch, useSaveCardSketch } from "../lib/queries";
import { Badge, Empty } from "./ui";
import { lang, t } from "../lib/i18n";

declare global {
  interface Window {
    /** Where Excalidraw looks for its canvas fonts (see `vite.config.ts`). */
    EXCALIDRAW_ASSET_PATH?: string | string[];
  }
}

/** Time without element changes before writing, in milliseconds. */
const SAVE_DELAY = 1000;
/** The export the agent reads: white background, never the editor's dark mode. */
const EXPORT_BACKGROUND = "#ffffff";
/** Its longest side, in pixels. */
const EXPORT_MAX_SIDE = 2048;
/** A small sketch is exported enlarged, up to twice, to stay readable. */
const EXPORT_MAX_SCALE = 2;
/** The margin around the drawing, in scene units. */
const EXPORT_PADDING = 24;
/** The traced render, shrunk to keep the scene light: long side in pixels, JPEG quality. */
const TRACE_MAX_SIDE = 1280;
const TRACE_QUALITY = 0.85;
/** The trace's mark in the scene (`customData`): one at a time. */
const TRACE_KEY = "renderTrace";

type ExcalidrawModule = typeof import("@excalidraw/excalidraw");

/**
 * `exportToBlob`, typed. Excalidraw's types declare it in a package they cite
 * without publishing it (`@excalidraw/utils`), so TypeScript reads it as
 * `any`. Here is its signature, as 0.18 declares it.
 */
type ExportToBlob = (options: {
  elements: readonly NonDeleted<ExcalidrawElement>[];
  appState?: Partial<AppState>;
  files: BinaryFiles | null;
  mimeType?: string;
  exportPadding?: number;
  getDimensions?: (width: number, height: number) => { width: number; height: number; scale?: number };
}) => Promise<Blob>;

/** A scene as `serializeAsJSON` writes it, and as the server returns it. */
interface SerializedScene {
  elements: NonDeleted<ExcalidrawElement>[];
  appState: Partial<AppState>;
  files: BinaryFiles;
  [key: string]: unknown;
}

/** What Excalidraw reports on each change, with the hash of its elements. */
type SceneListener = (
  version: number,
  elements: readonly ExcalidrawElement[],
  appState: AppState,
  files: BinaryFiles,
) => void;

interface CanvasProps {
  initialData: ExcalidrawInitialDataState | null;
  onReady: (api: ExcalidrawImperativeAPI) => void;
  onScene: SceneListener;
}

/* ------------------------------------------------------ the module, on demand */

let loading: Promise<ExcalidrawModule> | null = null;
/** The module once loaded: the editor only exists after it. */
let loaded: ExcalidrawModule | null = null;

/** The module and its stylesheet, loaded once, when a sketch first opens. */
function loadExcalidraw(): Promise<ExcalidrawModule> {
  if (!loading) {
    // Before the module: it builds its font URLs from this path. Resolved
    // against the page, it works for the shell, the Vite server and the
    // studio server when it serves the built front.
    window.EXCALIDRAW_ASSET_PATH = new URL("excalidraw/", document.baseURI).href;
    loading = Promise.all([
      import("@excalidraw/excalidraw"),
      import("@excalidraw/excalidraw/index.css"),
    ]).then(([module]) => (loaded = module));
    // A chunk not found (the application was rebuilt meanwhile) is requested
    // again on the next attempt, instead of staying failed.
    loading.catch(() => {
      loading = null;
    });
  }
  return loading;
}

/** Menus reduced to what is useful here: no file, no export, no theme. */
const UI_OPTIONS: ExcalidrawProps["UIOptions"] = {
  canvasActions: {
    changeViewBackgroundColor: false,
    clearCanvas: true,
    export: false,
    loadScene: false,
    saveAsImage: false,
    saveToActiveFile: false,
    toggleTheme: false,
  },
};

/**
 * The canvas, built once the module is loaded. Memoized: its props are stable,
 * and the save state changing above it does not redraw it.
 */
function lazyCanvas() {
  return lazy(async () => {
    const excalidraw = await loadExcalidraw();
    const { Excalidraw, MainMenu, hashElementsVersion } = excalidraw;
    const Canvas = memo(function Canvas({ initialData, onReady, onScene }: CanvasProps) {
      return (
        <Excalidraw
          initialData={initialData}
          excalidrawAPI={onReady}
          onChange={(elements, appState, files) =>
            onScene(hashElementsVersion(elements), elements, appState, files)
          }
          theme="dark"
          langCode={lang === "fr" ? "fr-FR" : "en"}
          UIOptions={UI_OPTIONS}
          aiEnabled={false}
          // The keyboard is its own only while it has focus: no global
          // shortcut, typing elsewhere in the studio stays with the studio.
          handleKeyboardGlobally={false}
        >
          <MainMenu>
            <MainMenu.DefaultItems.ClearCanvas />
            <MainMenu.DefaultItems.Help />
          </MainMenu>
        </Excalidraw>
      );
    });
    return { default: Canvas };
  });
}

let SketchCanvas = lazyCanvas();

/* ---------------------------------------------------------------------- saving */

type Card = { project: string; folder: string; name: string };
type Saving = "saved" | "saving" | "error";
type Scene = { version: number; elements: readonly ExcalidrawElement[]; appState: AppState; files: BinaryFiles };

const cardId = ({ project, folder, name }: Card) => JSON.stringify([project, folder, name]);

/**
 * Each card's writes, queued: a write never overtakes the previous one, even
 * from an unmounted editor (a tab left) to the next.
 */
const writes = new Map<string, Promise<unknown>>();
/** Each card's last write failure: the reopened editor shows it, and writes again. */
const failures = new Map<string, string>();

function queue<T>(card: string, job: () => Promise<T>): Promise<T> {
  const run = (writes.get(card) ?? Promise.resolve()).then(job);
  const settled = run.catch(() => undefined);
  writes.set(card, settled);
  void settled.then(() => {
    if (writes.get(card) === settled) writes.delete(card);
  });
  return run;
}

const messageOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** The saved scene, ready to reopen: centered on what it contains. */
function openScene(scene: Record<string, unknown> | null): ExcalidrawInitialDataState | null {
  return scene ? { ...(scene as ExcalidrawInitialDataState), scrollToContent: true } : null;
}

/** The sketch image, as a data URL: white background, a margin, 2048 px at most. */
async function exportPng(excalidraw: ExcalidrawModule, scene: SerializedScene): Promise<string> {
  const exportToBlob = excalidraw.exportToBlob as ExportToBlob;
  const blob = await exportToBlob({
    elements: scene.elements,
    appState: {
      ...scene.appState,
      exportBackground: true,
      exportEmbedScene: false,
      exportWithDarkMode: false,
      viewBackgroundColor: EXPORT_BACKGROUND,
    },
    files: scene.files,
    mimeType: "image/png",
    exportPadding: EXPORT_PADDING,
    getDimensions: (width, height) => {
      const scale = Math.min(EXPORT_MAX_SCALE, EXPORT_MAX_SIDE / Math.max(width, height, 1));
      return { width: Math.round(width * scale), height: Math.round(height * scale), scale };
    },
  });
  return excalidraw.getDataURL(blob);
}

/** The game's render, shrunk in a canvas and recompressed, to serve as a trace. */
async function shrinkRender(url: string): Promise<{ dataURL: DataURL; width: number; height: number }> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(t("unreadable render ({status})", { status: `${response.status} ${response.statusText}` }));
  }
  const bitmap = await createImageBitmap(await response.blob());
  const scale = Math.min(1, TRACE_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) {
    bitmap.close();
    throw new Error(t("canvas unavailable"));
  }
  // JPEG has no transparency: what would be transparent takes the export background.
  context.fillStyle = EXPORT_BACKGROUND;
  context.fillRect(0, 0, width, height);
  context.imageSmoothingQuality = "high";
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  return { dataURL: canvas.toDataURL("image/jpeg", TRACE_QUALITY) as DataURL, width, height };
}

const isTrace = (element: ExcalidrawElement) => Boolean(element.customData?.[TRACE_KEY]);

/* ------------------------------------------------------------------- component */

export default function CardSketch({ project, folder, name, render }: Card & {
  /** The card's current render, to trace in the sketch. */
  render: CardRender | null;
}) {
  const sketch = useCardSketch(project, folder, name);
  return (
    <div className="card-sketch">
      {sketch.isError ? (
        <Empty
          title={t("Unreadable sketch")}
          hint={messageOf(sketch.error)}
          action={
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void sketch.refetch()}>
              {t("Retry")}
            </button>
          }
        />
      ) : !sketch.data ? (
        <Empty title={t("Loading…")} />
      ) : (
        // One card, one editor: switching unmounts the old one, which saves
        // its last stroke, before opening the next, and forgets its crash.
        <SketchBoundary key={cardId({ project, folder, name })}>
          <SketchEditor
            project={project}
            folder={folder}
            name={name}
            scene={sketch.data}
            render={render}
          />
        </SketchBoundary>
      )}
    </div>
  );
}

function SketchEditor({ project, folder, name, scene, render }: Card & {
  scene: CardSketchScene;
  render: CardRender | null;
}) {
  const id = cardId({ project, folder, name });
  const client = useQueryClient();
  const { mutateAsync: write } = useSaveCardSketch();
  const [api, setApi] = useState<ExcalidrawImperativeAPI | null>(null);
  // Read once: the cached scene then follows the editor, never the reverse.
  const [initialData] = useState(() => openScene(scene.scene));
  const [saving, setSaving] = useState<Saving>(() => (failures.has(id) ? "error" : "saved"));
  const [failure, setFailure] = useState(() => failures.get(id) ?? "");
  const [tracing, setTracing] = useState(false);
  const [traceFailure, setTraceFailure] = useState("");

  /** The scene as Excalidraw last reported it. */
  const latest = useRef<Scene | null>(null);
  /** The elements' hash at the last report: a moving pointer does not change it. */
  const seen = useRef<number | null>(null);
  /**
   * The hash the server has: the opened scene's, then each successful write's.
   * `undefined` until the editor has reported; `null` after a known failure, so
   * that the first write goes out regardless.
   */
  const stored = useRef<number | null | undefined>(failures.has(id) ? null : undefined);
  /** The hash of the last requested write. */
  const asked = useRef<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const alive = useRef(true);

  // Writes the scene as it is, unless the server already has it.
  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    const current = latest.current;
    const excalidraw = loaded;
    if (!current || !excalidraw || current.version === asked.current) return;
    // A write requested and not yet successful: this one queues behind it.
    const pending = asked.current !== null && asked.current !== stored.current;
    if (current.version === stored.current && !pending) {
      // Back to what the server has (an undo): nothing to write.
      failures.delete(id);
      if (alive.current) setSaving("saved");
      return;
    }
    const version = current.version;
    asked.current = version;
    // Frozen now: Excalidraw mutates its elements in place. The PNG is drawn
    // from this copy, and the cached scene follows it, so a reopened editor
    // starts from the last stroke.
    const data = JSON.parse(
      excalidraw.serializeAsJSON(current.elements, current.appState, current.files, "local"),
    ) as SerializedScene;
    const empty = data.elements.length === 0;
    // The key of `useCardSketch` (lib/queries.ts).
    client.setQueryData<CardSketchScene>(["cardSketch", project, folder, name], (old) =>
      old ? { ...old, scene: empty ? null : data } : old,
    );
    if (alive.current) setSaving("saving");
    queue(id, async () => {
      // Overtaken by a newer request, already queued behind it.
      if (asked.current !== version) return false;
      const png = empty ? null : await exportPng(excalidraw, data);
      await write({ project, folder, name, scene: empty ? null : data, png });
      return true;
    }).then(
      (written) => {
        if (!written) return;
        stored.current = version;
        failures.delete(id);
        if (alive.current && latest.current?.version === version) {
          setSaving("saved");
          setFailure("");
        }
      },
      (error: unknown) => {
        const message = messageOf(error);
        failures.set(id, message);
        // The next request writes again, even without a new stroke.
        if (asked.current === version) asked.current = null;
        if (alive.current) {
          setSaving("error");
          setFailure(message);
        }
      },
    );
  }, [client, write, id, project, folder, name]);

  const onScene = useCallback<SceneListener>((version, elements, appState, files) => {
    latest.current = { version, elements, appState, files };
    if (version === seen.current) return;
    const opening = seen.current === null && stored.current === undefined;
    seen.current = version;
    if (opening) {
      // The first report: the scene as opened, which the server already has.
      stored.current = version;
      return;
    }
    setSaving("saving");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, SAVE_DELAY);
  }, [flush]);

  useEffect(() => {
    alive.current = true;
    // A window being hidden (another desktop, the application minimized)
    // writes at once: nothing says it will come back.
    const hidden = () => document.visibilityState === "hidden" && flush();
    document.addEventListener("visibilitychange", hidden);
    return () => {
      document.removeEventListener("visibilitychange", hidden);
      alive.current = false;
      // The last stroke leaves with the editor being closed.
      flush();
    };
  }, [flush]);

  const retry = () => {
    asked.current = null;
    flush();
  };

  // The current render, under the strokes, half transparent and locked, to
  // draw over. One trace only: the next replaces the previous one, at its
  // position and width.
  async function trace() {
    const excalidraw = loaded;
    if (!api || !render || !excalidraw) return;
    setTracing(true);
    setTraceFailure("");
    try {
      const image = await shrinkRender(assetFileUrl(render.asset_id));
      const fileId = `render-${render.asset_id}` as FileId;
      api.addFiles([{ id: fileId, dataURL: image.dataURL, mimeType: "image/jpeg", created: Date.now() }]);
      const elements = api.getSceneElementsIncludingDeleted();
      const previous = elements.find((element) => !element.isDeleted && isTrace(element));
      const view = api.getAppState();
      const width = previous ? previous.width : image.width;
      const height = (width * image.height) / image.width;
      // Without a previous trace: in the middle of the view.
      const centerX = previous ? previous.x + previous.width / 2 : view.width / 2 / view.zoom.value - view.scrollX;
      const centerY = previous ? previous.y + previous.height / 2 : view.height / 2 / view.zoom.value - view.scrollY;
      const [traced] = excalidraw.convertToExcalidrawElements([
        {
          type: "image",
          fileId,
          status: "saved",
          x: centerX - width / 2,
          y: centerY - height / 2,
          width,
          height,
          opacity: 50,
          locked: true,
          customData: { [TRACE_KEY]: render.asset_id },
        },
      ]);
      if (!traced) return;
      api.updateScene({
        // First in the list, i.e. at the back. Its rank is computed on
        // insertion (`index` null) without renumbering the rest.
        elements: [
          { ...traced, index: null },
          ...elements.map((element) =>
            !element.isDeleted && isTrace(element) ? excalidraw.newElementWith(element, { isDeleted: true }) : element,
          ),
        ],
        captureUpdate: excalidraw.CaptureUpdateAction.IMMEDIATELY,
      });
      if (!previous) api.scrollToContent(traced, { fitToViewport: true, viewportZoomFactor: 0.9, animate: true });
    } catch (error) {
      setTraceFailure(messageOf(error));
    } finally {
      setTracing(false);
    }
  }

  return (
    <>
      <div className="card-sketch-bar">
        {saving === "error" ? (
          <>
            <span className="card-sketch-failure" title={failure}>
              <Badge tone="danger">{t("Not saved · {failure}", { failure })}</Badge>
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={retry}>
              {t("Retry")}
            </button>
          </>
        ) : (
          <span className={`card-sketch-state mono is-${saving}`} role="status">
            {saving === "saving" ? t("Saving…") : t("Saved")}
          </span>
        )}
        <span className="spacer" />
        {traceFailure && (
          <span className="card-sketch-failure" title={traceFailure}>
            <Badge tone="danger">{t("Tracing failed · {traceFailure}", { traceFailure })}</Badge>
          </span>
        )}
        {render && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={!api || tracing}
            onClick={() => void trace()}
          >
            {tracing ? t("Tracing…") : t("Trace the render")}
          </button>
        )}
      </div>
      <div
        className="card-sketch-stage"
        // Excalidraw measures its box once, then on each resize or scroll: a
        // layout shift above it would offset the stroke from the pointer. So
        // it measures again whenever the pointer enters.
        onPointerEnter={() => api?.refresh()}
        // Its typing does not bubble up to the window's shortcuts.
        onKeyDown={(event) => event.stopPropagation()}
      >
        <div className="card-sketch-canvas">
          <Suspense fallback={<Empty title={t("Loading…")} />}>
            <SketchCanvas initialData={initialData} onReady={setApi} onScene={onScene} />
          </Suspense>
        </div>
      </div>
    </>
  );
}

/**
 * An Excalidraw crash stays inside its frame: otherwise an editor error would
 * take down the whole studio window. The unmounted editor wrote its last
 * state; "Reopen" starts from there.
 */
class SketchBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  reopen = () => {
    // A chunk that failed to load would stay failed inside `lazy`.
    if (!loaded) SketchCanvas = lazyCanvas();
    this.setState({ error: null });
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Empty
        title={t("Sketch interrupted")}
        hint={this.state.error.message}
        action={
          <button type="button" className="btn btn-secondary btn-sm" onClick={this.reopen}>
            {t("Reopen")}
          </button>
        }
      />
    );
  }
}
