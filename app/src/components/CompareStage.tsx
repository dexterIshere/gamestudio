/**
 * Visual comparator: the four ways to judge two images.
 *
 * Every mode shares the same zoom / pan pair: changing mode must not change
 * the framing, or the comparison would be about two things at once. Zoom
 * follows the cursor: the point being looked at does not move.
 *
 * The difference is computed in a canvas, at the images' native size.
 * Computing it on the displayed image would measure the browser's
 * antialiasing, not the difference between the two files. Two images of
 * different sizes are fitted into a shared box, and the screen says so rather
 * than letting them drift apart silently.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { Callout } from "./ui";
import { num, t } from "../lib/i18n";

/** A chosen image: where it comes from, and what to display it with. */
export interface CompareSource {
  assetId: string;
  name: string;
  origin: string;
  url: string;
}

export type CompareMode = "side" | "overlay" | "blink" | "diff";
export type CompareBackdrop = "dark" | "checker" | "light";

interface View {
  /** Screen pixels per image pixel. */
  s: number;
  /** Offset of the image center from the pane center. */
  tx: number;
  ty: number;
}

const MIN_SCALE = 0.02;
const MAX_SCALE = 40;
const ZOOM_STEP = 1.12;
/** Beyond this, the browser canvas hits a wall: scale down and say so. */
const MAX_PIXELS = 4_000_000;
/** Below this, two pixels count as identical (compression noise). */
const DEFAULT_THRESHOLD = 16;

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/* --------------------------------------------------------- difference */

export interface DiffResult {
  /** The difference canvas, as a data URL. */
  url: string;
  width: number;
  height: number;
  /** Pixels that differ, and pixels covered by either image. */
  changed: number;
  covered: number;
  notes: string[];
}

/** A design system variable: the canvas inherits no stylesheet. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function rgb(value: string, fallback: [number, number, number]): [number, number, number] {
  const text = value.trim();
  const hex = text.startsWith("#") ? text.slice(1) : "";
  if (hex.length === 3 || hex.length === 6) {
    const full = hex.length === 3 ? hex.split("").map((char) => char + char).join("") : hex;
    const parsed = Number.parseInt(full, 16);
    if (!Number.isNaN(parsed)) {
      return [(parsed >> 16) & 255, (parsed >> 8) & 255, parsed & 255];
    }
  }
  const match = /rgba?\(([^)]+)\)/.exec(text);
  const parts = match?.[1]?.split(",").map((part) => Number.parseFloat(part));
  if (parts && parts.length >= 3 && parts[0] !== undefined && parts[1] !== undefined && parts[2] !== undefined) {
    return [parts[0], parts[1], parts[2]];
  }
  return fallback;
}

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error(t("2D canvas unavailable"));
  return context;
}

/**
 * An image's pixels, fetched from the network rather than from a tag.
 *
 * Reusing the displayed URL does not work: the image is already in the
 * browser cache, and that entry was not validated for CORS. Reading it again
 * with `crossOrigin` fails although the file is perfectly readable ("Missing
 * Allow Origin Header").
 *
 * So the bytes are fetched, without cache, and decoded from a blob: a blob has
 * the page's origin, so the canvas stays readable and the difference can be
 * computed.
 */
async function loadImage(url: string): Promise<HTMLImageElement> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(t("unreadable image ({status})", { status: response.status }));
  const source = URL.createObjectURL(await response.blob());
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(t("unreadable image")));
      image.src = source;
    });
  } finally {
    URL.revokeObjectURL(source);
  }
}

/** An image drawn in a canvas of the requested box, fitted and centered. */
function fitted(image: HTMLImageElement, width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = context2d(canvas);
  const source = { w: image.naturalWidth || 512, h: image.naturalHeight || 512 };
  const ratio = Math.min(width / source.w, height / source.h);
  const w = source.w * ratio;
  const h = source.h * ratio;
  context.drawImage(image, (width - w) / 2, (height - h) / 2, w, h);
  return canvas;
}

/**
 * The difference between two images: grey where nothing moved, orange where it
 * changes, brighter as the difference grows.
 */
export async function computeDiff(urlA: string, urlB: string, threshold: number): Promise<DiffResult> {
  const [a, b] = await Promise.all([loadImage(urlA), loadImage(urlB)]);
  const notes: string[] = [];

  const aw = a.naturalWidth || 512;
  const ah = a.naturalHeight || 512;
  const bw = b.naturalWidth || 512;
  const bh = b.naturalHeight || 512;

  const width = Math.max(aw, bw);
  const height = Math.max(ah, bh);
  const ratio = Math.min(1, Math.sqrt(MAX_PIXELS / (width * height)));
  const canvasW = Math.max(1, Math.round(width * ratio));
  const canvasH = Math.max(1, Math.round(height * ratio));

  if (aw !== bw || ah !== bh) {
    notes.push(
      t("The two images differ in size (A {aw}×{ah}, B {bw}×{bh}). They are fitted and centered in "
        + "a shared {width}×{height} box: the difference measures shapes, not pixel against "
        + "pixel.", { aw, ah, bw, bh, width, height }),
    );
  }
  if (ratio < 1) {
    notes.push(
      t("Images too large for a full-size difference: it is computed at {percent}% "
        + "(limit of {millions} million pixels).",
        { percent: Math.round(ratio * 100), millions: (MAX_PIXELS / 1e6).toFixed(0) }),
    );
  }

  const left = context2d(fitted(a, canvasW, canvasH)).getImageData(0, 0, canvasW, canvasH);
  const right = context2d(fitted(b, canvasW, canvasH)).getImageData(0, 0, canvasW, canvasH);

  const canvas = document.createElement("canvas");
  canvas.width = canvasW;
  canvas.height = canvasH;
  const context = context2d(canvas);
  const out = context.createImageData(canvasW, canvasH);

  const low = rgb(token("--accent", "#ff7a45"), [255, 122, 69]);
  const high = rgb(token("--gold", "#ffd260"), [255, 210, 96]);

  const one = left.data;
  const two = right.data;
  const to = out.data;
  let changed = 0;
  let covered = 0;

  for (let i = 0; i < one.length; i += 4) {
    const ar = one[i]!;
    const ag = one[i + 1]!;
    const ab = one[i + 2]!;
    const aa = one[i + 3]!;
    const br = two[i]!;
    const bg = two[i + 1]!;
    const bb = two[i + 2]!;
    const ba = two[i + 3]!;

    // Two transparent pixels have no color to compare: what they hold is not
    // visible, and counting it could make the changed pixels outnumber the
    // covered ones.
    const solid = aa > 8 || ba > 8;
    if (!solid) {
      to[i + 3] = 0;
      continue;
    }
    covered += 1;

    const delta = Math.max(
      Math.abs(ar - br),
      Math.abs(ag - bg),
      Math.abs(ab - bb),
      Math.abs(aa - ba),
    );

    if (delta > threshold) {
      changed += 1;
      const mix = (delta - threshold) / Math.max(1, 255 - threshold);
      to[i] = low[0] + (high[0] - low[0]) * mix;
      to[i + 1] = low[1] + (high[1] - low[1]) * mix;
      to[i + 2] = low[2] + (high[2] - low[2]) * mix;
      to[i + 3] = 255;
    } else {
      // The ghost of what did not move: light enough to place the shape, dim
      // enough not to compete with the difference color.
      const grey = Math.round(46 + (0.2126 * ar + 0.7152 * ag + 0.0722 * ab) * 0.35);
      to[i] = grey;
      to[i + 1] = grey;
      to[i + 2] = grey;
      to[i + 3] = 255;
    }
  }

  context.putImageData(out, 0, 0);
  return { url: canvas.toDataURL("image/png"), width: canvasW, height: canvasH, changed, covered, notes };
}

/* ------------------------------------------------------------- measures */

function useImageSize(url: string | null): { width: number; height: number; failed: boolean } {
  const [size, setSize] = useState({ width: 0, height: 0, failed: false });
  useEffect(() => {
    if (!url) {
      setSize({ width: 0, height: 0, failed: false });
      return;
    }
    let live = true;
    const image = new Image();
    image.onload = () => {
      // An SVG without intrinsic dimensions announces nothing: frame on a
      // default box rather than divide by zero.
      if (live) setSize({ width: image.naturalWidth || 512, height: image.naturalHeight || 512, failed: false });
    };
    image.onerror = () => {
      if (live) setSize({ width: 0, height: 0, failed: true });
    };
    image.src = url;
    return () => {
      live = false;
    };
  }, [url]);
  return size;
}

/* --------------------------------------------------------------- layers */

function Layer({ url, width, height, view, opacity }: {
  url: string;
  width: number;
  height: number;
  view: View;
  opacity: number;
}) {
  return (
    <div
      className="cmp-layer"
      style={{
        width,
        height,
        opacity,
        // The image center stays at the pane center: two images of different
        // sizes overlap by their middle, the only origin that makes them
        // comparable.
        transform: `translate(-50%, -50%) translate(${view.tx}px, ${view.ty}px) scale(${view.s})`,
      }}
    >
      <img
        className={view.s >= 2 ? "pixelated" : undefined}
        src={url}
        alt=""
        width={width}
        height={height}
        draggable={false}
      />
    </div>
  );
}

/**
 * A pane: a box that holds layers and carries the gestures.
 *
 * Zoom and pan go up to the comparator's shared state, which is why dragging
 * in one pane moves both.
 */
function Pane({ attach, setView, onTouched, tag, children }: {
  attach: (element: HTMLDivElement | null) => void;
  setView: Dispatch<SetStateAction<View>>;
  onTouched: () => void;
  tag?: ReactNode;
  children?: ReactNode;
}) {
  const local = useRef<HTMLDivElement | null>(null);
  const dragging = useRef<{ x: number; y: number } | null>(null);

  const ref = useCallback(
    (element: HTMLDivElement | null) => {
      local.current = element;
      attach(element);
    },
    [attach],
  );

  // The wheel is a native non-passive listener: when passive, `preventDefault`
  // is ignored and the page would scroll while zooming.
  useEffect(() => {
    const element = local.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const factor = event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      // Zoom centered on the cursor: the point under the mouse does not move.
      const ux = event.clientX - rect.left - rect.width / 2;
      const uy = event.clientY - rect.top - rect.height / 2;
      onTouched();
      setView((current) => {
        const s = clamp(current.s * factor, MIN_SCALE, MAX_SCALE);
        const k = s / current.s;
        return { s, tx: ux - k * (ux - current.tx), ty: uy - k * (uy - current.ty) };
      });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [setView, onTouched]);

  return (
    <div
      className="cmp-pane"
      ref={ref}
      onPointerDown={(event) => {
        // A click on a thumbnail of the empty state picks an image; it must
        // not start a pan.
        if ((event.target as HTMLElement).closest("button, input, select, a, textarea")) return;
        dragging.current = { x: event.clientX, y: event.clientY };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const from = dragging.current;
        if (!from) return;
        const dx = event.clientX - from.x;
        const dy = event.clientY - from.y;
        dragging.current = { x: event.clientX, y: event.clientY };
        onTouched();
        setView((current) => ({ ...current, tx: current.tx + dx, ty: current.ty + dy }));
      }}
      onPointerUp={(event) => {
        dragging.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
      }}
      onPointerCancel={() => {
        dragging.current = null;
      }}
    >
      {children}
      {tag && <span className="cmp-tag">{tag}</span>}
    </div>
  );
}

/* ---------------------------------------------------------------- stage */

export function CompareStage({ a, b, mode, backdrop, opacity, blinkMs, threshold, blankA, blankB, fallback }: {
  a: CompareSource | null;
  b: CompareSource | null;
  mode: CompareMode;
  backdrop: CompareBackdrop;
  opacity: number;
  blinkMs: number;
  threshold: number;
  blankA: ReactNode;
  blankB: ReactNode;
  fallback: ReactNode;
}) {
  const [view, setView] = useState<View>({ s: 1, tx: 0, ty: 0 });
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [peek, setPeek] = useState(false);
  const [showB, setShowB] = useState(false);
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [problem, setProblem] = useState("");

  const first = useRef<HTMLDivElement | null>(null);
  const noAttach = useCallback(() => {}, []);
  const touched = useRef(false);
  const sizeA = useImageSize(a?.url ?? null);
  const sizeB = useImageSize(b?.url ?? null);

  // What the framing must contain: the larger of the two, which is also the
  // shared box of the difference.
  const content = {
    w: Math.max(sizeA.width, sizeB.width, 1),
    h: Math.max(sizeA.height, sizeB.height, 1),
  };

  const onTouched = useCallback(() => {
    touched.current = true;
  }, []);

  const fit = useCallback(
    (width: number, height: number) => {
      if (!width || !height) return;
      const margin = mode === "side" ? 0.94 : 0.86;
      setView({
        s: Math.min(width / content.w, height / content.h) * margin,
        tx: 0,
        ty: 0,
      });
      touched.current = false;
    },
    [content.w, content.h, mode],
  );

  const attach = useCallback((element: HTMLDivElement | null) => {
    first.current = element;
    if (element) setBox({ w: element.clientWidth, h: element.clientHeight });
  }, []);

  // The panes' box: it changes when the window is resized, and it sets the
  // reference scale.
  useLayoutEffect(() => {
    const element = first.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setBox({ w: element.clientWidth, h: element.clientHeight }));
    observer.observe(element);
    setBox({ w: element.clientWidth, h: element.clientHeight });
    return () => observer.disconnect();
  }, []);

  // Reframe when the image or the mode changes.
  useEffect(() => {
    fit(box.w, box.h);
  }, [fit]); // eslint-disable-line react-hooks/exhaustive-deps

  // A plain resize only reframes if the user has not chosen a framing yet:
  // otherwise resizing the window would lose their zoom.
  useEffect(() => {
    if (!touched.current) fit(box.w, box.h);
  }, [box.w, box.h]); // eslint-disable-line react-hooks/exhaustive-deps

  // Blinking: A and B alternate at the requested rate.
  useEffect(() => {
    if (mode !== "blink") {
      setShowB(false);
      return;
    }
    const timer = window.setInterval(() => setShowB((current) => !current), Math.max(60, blinkMs));
    return () => window.clearInterval(timer);
  }, [mode, blinkMs]);

  // Holding the key shows A alone. `preventDefault` stops the page from
  // scrolling and a focused button from activating: the space bar stays a
  // comparison gesture anywhere on the page.
  useEffect(() => {
    const typing = (target: EventTarget | null) => {
      const element = target as HTMLElement | null;
      if (!element) return false;
      return (
        element.tagName === "INPUT" ||
        element.tagName === "TEXTAREA" ||
        element.tagName === "SELECT" ||
        element.isContentEditable
      );
    };
    const gesture = (event: KeyboardEvent) => event.code === "Space" || event.key === "b" || event.key === "B";
    const down = (event: KeyboardEvent) => {
      if (!gesture(event) || event.repeat || typing(event.target)) return;
      event.preventDefault();
      setPeek(true);
    };
    const up = (event: KeyboardEvent) => {
      if (gesture(event)) setPeek(false);
    };
    // Losing focus with the key held would leave the comparator stuck on A:
    // the release never arrives.
    const blur = () => setPeek(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // The difference is recomputed when the threshold moves: the delay avoids
  // restarting the computation on every pixel of slider drag.
  const urlA = a?.url ?? null;
  const urlB = b?.url ?? null;
  useEffect(() => {
    if (mode !== "diff" || !urlA || !urlB) {
      setDiff(null);
      setNotes([]);
      setProblem("");
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      computeDiff(urlA, urlB, threshold)
        .then((result) => {
          if (!live) return;
          setDiff(result);
          setNotes(result.notes);
          setProblem("");
        })
        .catch((error: unknown) => {
          if (!live) return;
          setDiff(null);
          setNotes([]);
          setProblem(error instanceof Error ? error.message : t("difference impossible"));
        });
    }, 140);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [mode, urlA, urlB, threshold]);

  const stacked = mode !== "side";
  const peeking = peek && stacked && Boolean(a) && Boolean(b);

  let opacityA = 1;
  let opacityB = 0;
  let opacityDiff = 0;
  if (mode === "overlay") {
    opacityB = peeking ? 0 : opacity;
  } else if (mode === "blink") {
    opacityA = peeking || !showB ? 1 : 0;
    opacityB = !peeking && showB ? 1 : 0;
  } else if (mode === "diff") {
    opacityA = peeking ? 1 : 0;
    opacityDiff = peeking ? 0 : 1;
  }

  const shown = mode === "blink" ? (peeking || !showB ? "A" : "B") : null;
  const ratio = diff && diff.covered ? diff.changed / diff.covered : 0;
  // The difference only replaces B once computed, and unless the original is
  // asked for.
  const diffVisible = Boolean(diff) && mode === "diff" && !peeking;

  const width = Math.max(1, Math.round(content.w * view.s));
  const height = Math.max(1, Math.round(content.h * view.s));
  const missing = [a ? "" : t("A empty"), b ? "" : t("B empty")].filter(Boolean).join(" · ");
  // Two empty frames deserve a single message: the fallback replaces both, and
  // there is nothing to zoom while no image is there.
  const empty = !a && !b;

  return (
    <>
      <div className="cmp-stage" data-bg={backdrop}>
        <div className="cmp-pair" data-layout={mode === "side" ? "pair" : "stack"}>
          <Pane attach={attach} setView={setView} onTouched={onTouched}
            tag={mode === "side" && a ? <>A <b>{a.name}</b></> : undefined}>
            {a && sizeA.width > 0 && (
              <Layer url={a.url} width={sizeA.width} height={sizeA.height} view={view}
                opacity={mode === "side" ? 1 : opacityA} />
            )}
            {mode === "side" && !a && !empty && blankA}
            {a && sizeA.failed && <div className="cmp-problem">{t("Unreadable image: {name}", { name: a.name })}</div>}
          </Pane>

          <Pane attach={noAttach} setView={setView} onTouched={onTouched}
            tag={mode === "side" && b ? <>B <b>{b.name}</b></> : undefined}>
            {b && sizeB.width > 0 && !diffVisible && (
              <Layer url={b.url} width={sizeB.width} height={sizeB.height} view={view}
                opacity={mode === "side" ? 1 : opacityB} />
            )}
            {diffVisible && diff && (
              <Layer url={diff.url} width={content.w} height={content.h} view={view} opacity={opacityDiff} />
            )}
            {mode === "side" && !b && !empty && blankB}
            {b && sizeB.failed && <div className="cmp-problem">{t("Unreadable image: {name}", { name: b.name })}</div>}
          </Pane>
        </div>

        {/* When stacked, pane B covers pane A: what is missing does not show in
            a pane, so it is said here. Two empty frames do not need this
            reminder: the fallback already says it. */}
        {mode !== "side" && !empty && missing && <span className="cmp-missing">{missing}</span>}

        {/* Nothing to zoom, nothing to measure: the floating bars only show
            once there is an image behind them. */}
        {!empty && <div className="cmp-tools">
          <button
            className="btn btn-secondary btn-icon"
            title={t("Zoom out")}
            aria-label={t("Zoom out")}
            onClick={() => {
              onTouched();
              setView((current) => ({ ...current, s: clamp(current.s / 1.25, MIN_SCALE, MAX_SCALE) }));
            }}
          >
            −
          </button>
          <button
            className="btn btn-secondary btn-sm"
            title={t("Actual size")}
            onClick={() => {
              onTouched();
              setView((current) => ({ ...current, s: 1 }));
            }}
          >
            1:1
          </button>
          <button
            className="btn btn-secondary btn-icon"
            title={t("Zoom in")}
            aria-label={t("Zoom in")}
            onClick={() => {
              onTouched();
              setView((current) => ({ ...current, s: clamp(current.s * 1.25, MIN_SCALE, MAX_SCALE) }));
            }}
          >
            +
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => fit(box.w, box.h)}>
            {t("Fit")}
          </button>
        </div>}

        {!empty && <div className="cmp-hud">
          {a && <span className="kbd">A <b>{sizeA.width || "?"}×{sizeA.height || "?"}</b></span>}
          {b && <span className="kbd">B <b>{sizeB.width || "?"}×{sizeB.height || "?"}</b></span>}
          <span className="kbd">{t("zoom")} <b>×{view.s.toFixed(2)}</b></span>
          {mode === "overlay" && <span className="kbd">{t("opacity")} <b>{Math.round(opacity * 100)} %</b></span>}
          {mode === "blink" && (
            <>
              <span className="kbd">{t("rate")} <b>{t("{v} Hz", { v: (1000 / blinkMs).toFixed(1) })}</b></span>
              <span className="kbd">{t("showing")} <b>{shown ?? "—"}</b></span>
            </>
          )}
          {mode === "diff" && (
            <>
              <span className="kbd">{t("difference")} <b>{(ratio * 100).toFixed(2)} %</b></span>
              <span className="kbd">{t("threshold")} <b>{threshold}</b></span>
            </>
          )}
        </div>}

        {/* The difference scale: grey did not move, color did. */}
        {mode === "diff" && (
          <div className="cmp-ramp">
            <span className="hint">{t("unchanged")}</span>
            <span className="bar" />
            <span className="hint">{t("changed")}</span>
          </div>
        )}

        {!empty && <div className="cmp-hint mono">
          {mode === "side" && t("wheel: zoom under the cursor · drag: move both panes")}
          {mode === "overlay" && t("wheel: zoom · drag: move · hold space: A alone, without B")}
          {mode === "blink" && t("hold space: freeze on A · rate and framing apply to both images")}
          {mode === "diff" && t("hold space: the difference fades, A alone · grey: unchanged, color: changed")}
          <span className="sep">·</span>
          <span>{t("{width}×{height} px", { width, height })}</span>
        </div>}

        {empty && <div className="cmp-blank">{fallback}</div>}
      </div>

      {mode === "diff" && (
        <div style={{ marginTop: "var(--space-4)", display: "grid", gap: "var(--space-3)" }}>
          {problem ? (
            <Callout>{t("Difference impossible: {problem}.", { problem })}</Callout>
          ) : diff ? (
            <>
              <Callout>
                {t("{changed} pixel(s) differ out of {covered} covered — {percent}% of the covered "
                  + "area, at threshold {threshold}.", {
                  changed: num(diff.changed), covered: num(diff.covered),
                  percent: (ratio * 100).toFixed(2), threshold,
                })}
              </Callout>
              {notes.map((note) => <Callout key={note}>{note}</Callout>)}
            </>
          ) : a && b ? (
            <Callout>{t("Computing the difference…")}</Callout>
          ) : (
            <Callout>{t("The difference needs two images: choose one in each slot.")}</Callout>
          )}
        </div>
      )}
    </>
  );
}

export { DEFAULT_THRESHOLD };
