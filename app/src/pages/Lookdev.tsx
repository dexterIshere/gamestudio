/**
 * The universe: the game's art direction, shown element by element, dressed as the game.
 *
 * It is not a description to read but a place to enter. The zone takes the
 * game's background, colors, font and sky; each shader in it is a specimen,
 * rendered by Godot itself on an off-screen bench (`service/lookdev.py`) and
 * tunable live -- on its stand-in (a rectangle at its size, a lit shape, a
 * sky), or on the game's real object when a staging builds it. No verdict:
 * what is in the game is kept, what is no longer wanted leaves it; a shader to
 * rework is discussed, its bubble opens an agent on it
 * (`handoff.lookdev_brief`). The universe touches the game only through
 * "Save": the changed settings, written where the shown use takes them
 * (`lookdev.save`). A screen selector shows the specimen as a device draws
 * it, pixel for pixel.
 *
 * The art direction's written cards are one click away: "Cards".
 */

import {
  Fragment, memo, useCallback, useEffect, useMemo, useReducer, useRef, useState, type PointerEvent, type WheelEvent,
} from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  lookdevFontUrl, lookdevFrameUrl, lookdevThumbUrl, type UniformInfo,
  api, type LookdevBrief, type LookdevFrameQuery, type LookdevScreen, type LookdevSpecimen,
  type LookdevTheme,
} from "../api";
import HandoffDialog from "../components/Handoff";
import { CardsGlyph } from "../components/IconForge";
import { useSetLookdevState, useLookdev, useLookdevSpecimen } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Badge, CloseCross, Empty, Seg, Select, Slider, Toggle, ToolGroup, Toolbar } from "../components/ui";
import { ChatPlusGlyph, Shelf } from "./Documents";
import { t, tn, tr } from "../lib/i18n";

const KINDS: Record<string, string> = {
  canvas_item: t("Interface"),
  spatial: t("Material"),
  sky: t("Sky"),
  particles: t("Particles"),
  fog: t("Fog"),
  include: t("Library"),
};

const SHAPES = [
  { value: "sphere", label: t("Sphere") },
  { value: "plane", label: t("Plane") },
  { value: "cube", label: t("Cube") },
];

/** Godot: a range hint (`hint_range`). */
const HINT_RANGE = 1;
/**
 * During a gesture (drag, wheel, slider) and a little after, each frame
 * requests the next at once; at rest, an animated shader runs at thirty frames
 * per second, and a static one is no longer requested.
 */
const ACTIVE_MS = 600;
const IDLE_INTERVAL = 33;
const VIEW = { yaw: 25, pitch: 12, zoom: 1 };

/** The simulated screens, by their short side: the game stretches to them from its base size. */
const SCREENS = [
  { value: "720p", short: 720 },
  { value: "1080p", short: 1080 },
  { value: "1440p", short: 1440 },
] as const;
type ScreenId = "studio" | (typeof SCREENS)[number]["value"];

/**
 * The scale at which a screen draws the specimen (`render`), and the scale at
 * which it shows it (`show`), from the game's stretch mode: `canvas_items`
 * redraws the interface at the screen's size, `viewport` draws at the base
 * size then enlarges the image, `disabled` leaves the interface at its size.
 */
function onScreen(screen: LookdevScreen, short: number, flat: boolean): { render: number; show: number } {
  const factor = short / Math.max(1, Math.min(screen.width, screen.height));
  if (screen.stretch === "viewport") return { render: 1, show: factor };
  if (screen.stretch === "disabled" && flat) return { render: 1, show: 1 };
  return { render: Math.min(4, Math.max(0.25, factor)), show: factor };
}

/** Two setting values equal up to rounding; a color without alpha is opaque. */
function same(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-6;
  if (Array.isArray(a) && Array.isArray(b)) {
    const length = Math.max(a.length, b.length);
    for (let index = 0; index < length; index += 1) {
      const fallback = index === 3 ? 1 : NaN;
      if (!same(a[index] ?? fallback, b[index] ?? fallback)) return false;
    }
    return true;
  }
  return a === b;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/* ------------------------------------------------------------ the game's look */

/** The game's most vivid color: its accent. */
function vivid(colors: string[]): string | null {
  let best: string | null = null;
  let score = -1;
  for (const hex of colors) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    const max = Math.max(r!, g!, b!);
    const min = Math.min(r!, g!, b!);
    const saturation = max === 0 ? 0 : (max - min) / max;
    const value = saturation * max;
    if (value > score) {
      score = value;
      best = hex;
    }
  }
  return best;
}

/**
 * The game's font, loaded under its own name; `null` until it is there.
 *
 * Its bytes go through `fetch`, never `url(…)`: the shell's CSP
 * (`font-src 'self' data:`) refuses a font served by the local API, while it
 * lets a request through (`connect-src`). The URL carries the token.
 */
function useGameFont(project: string, theme: LookdevTheme | undefined): string | null {
  const [family, setFamily] = useState<string | null>(null);
  const file = theme?.fonts.find((font) => /regular|book|medium/i.test(font.family))?.file
    ?? theme?.fonts[0]?.file;
  useEffect(() => {
    if (!file) return;
    const name = `lookdev-${project}`;
    let alive = true;
    let added: FontFace | null = null;
    void (async () => {
      try {
        const response = await fetch(lookdevFontUrl(project, file));
        if (!response.ok) throw new Error(`${response.status}`);
        const face = await new FontFace(name, await response.arrayBuffer()).load();
        if (!alive) return;
        document.fonts.add(face);
        added = face;
        setFamily(name);
      } catch (error) {
        // A missing or unreadable font does not keep the zone from opening: it
        // keeps the studio's, and the console says why.
        console.warn("lookdev: game font not loaded", file, error);
      }
    })();
    return () => {
      alive = false;
      if (added) document.fonts.delete(added);
    };
  }, [project, file]);
  return family;
}

/* ------------------------------------------------------------------ the page */

export default function Lookdev() {
  const { project } = useStudio();
  const [cards, setCards] = useState(false);

  if (cards) {
    return (
      <Shelf
        title={t("Art direction")}
        folder="design/direction"
        template="direction"
        actions={(
          <button type="button" className="btn btn-secondary" onClick={() => setCards(false)}>
            {t("Universe")}
          </button>
        )}
      />
    );
  }
  return <Zone key={project} project={project} onCards={() => setCards(true)} />;
}

function Zone({ project, onCards }: { project: string; onCards: () => void }) {
  const { data, error, isLoading } = useLookdev(project);
  const [discussing, setDiscussing] = useState<LookdevSpecimen | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // A saved specimen changed in the game: its thumbnail is requested again.
  const [saved, setSaved] = useState<Record<string, number>>({});
  const family = useGameFont(project, data?.theme);
  const accent = data ? vivid(data.theme.colors) : null;

  const style = useMemo(() => {
    if (!data) return undefined;
    const theme = data.theme;
    const sky = theme.sky ? lookdevFrameUrl(project, theme.sky, { scale: 2, yaw: 20, pitch: 8 }) : "";
    return {
      "--u-bg": theme.background,
      "--u-ink": theme.colors[0] ?? undefined,
      "--u-accent": accent ?? undefined,
      // The studio's controls (tabs, sliders) take the game's accent.
      "--accent": accent ?? undefined,
      "--u-sky": sky ? `url("${sky}")` : undefined,
      fontFamily: family ? `"${family}", var(--font-body)` : undefined,
    } as React.CSSProperties;
  }, [data, project, family, accent]);

  const shown = data?.specimens ?? [];

  return (
    <div className="lookdev" style={style}>
      <div className="lookdev-sky" aria-hidden="true" />
      <header className="lookdev-head" data-tauri-drag-region>
        <div>
          <p className="lookdev-where mono">{t("{project} › Art direction", { project })}</p>
          <h1>{t("Universe")}</h1>
          {data && (
            <ul className="lookdev-palette" aria-label={t("Game colors")}>
              {data.theme.colors.map((color) => (
                <li key={color} title={color} style={{ background: color }} />
              ))}
            </ul>
          )}
        </div>
        <div className="lookdev-actions">
          <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Cards")} title={t("Cards")}
                  onClick={onCards}>
            <CardsGlyph />
          </button>
        </div>
      </header>

      {isLoading ? (
        <Empty title={t("Reading the game…")} />
      ) : error ? (
        <div className="lookdev-panel"><Empty title={(error as Error).message} /></div>
      ) : data && data.specimens.length === 0 ? (
        <div className="lookdev-panel"><Empty title={t("No shader in the game")} /></div>
      ) : data ? (
        <section className="lookdev-room">
          <div className="lookdev-room-head">
            <h2>{t("Shaders")}</h2>
            <span className="num">{shown.length}</span>
          </div>
          <ul className="lookdev-grid">
            {shown.map((entry) => (
              <SpecimenCard key={entry.id} project={project} entry={entry} onOpen={() => setOpen(entry.id)}
                            version={`${entry.updated_at ?? ""}-${saved[entry.id] ?? 0}`}
                            onDiscuss={() => setDiscussing(entry)} />
            ))}
          </ul>
        </section>
      ) : null}

      {/* The room goes above the whole studio: it leaves the page, taking the
          game's look with it. */}
      {open && createPortal(
        <div className="lookdev-layer" style={style}>
          <Inspector key={open} project={project} id={open} background={data?.theme.background ?? ""}
                 paused={discussing !== null} onClose={() => setOpen(null)}
                 onDiscuss={setDiscussing}
                 onSaved={(id) => setSaved((held) => ({ ...held, [id]: (held[id] ?? 0) + 1 }))} />
        </div>,
        document.body,
      )}
      {/* After the room: the conversation opens on top of it. */}
      {discussing && createPortal(
        <div className="lookdev-handoff">
          <LookdevHandoff project={project} entry={discussing} onClose={() => setDiscussing(null)} />
        </div>,
        document.body,
      )}
    </div>
  );
}

function SpecimenCard({ project, entry, version, onOpen, onDiscuss }: {
  project: string;
  entry: LookdevSpecimen;
  version: string;
  onOpen: () => void;
  onDiscuss: () => void;
}) {
  const [broken, setBroken] = useState(false);
  return (
    <li className="lookdev-card">
      <button type="button" onClick={onOpen} disabled={!entry.renderable} title={entry.file}>
        <span className={`lookdev-thumb ${entry.kind === "canvas_item" ? "is-flat" : ""}`}>
          {entry.renderable && !broken ? (
            <img
              src={lookdevThumbUrl(project, entry.id, version)}
              alt=""
              loading="lazy"
              onError={() => setBroken(true)}
            />
          ) : (
            <span className="mono">{entry.renderable ? "—" : entry.kind}</span>
          )}
        </span>
        <span className="lookdev-card-cap">
          <b>{entry.title}</b>
          <span className="lookdev-tags">
            <span>{KINDS[entry.kind] ?? entry.kind}</span>
            {entry.staged && <span>{t("Game object")}</span>}
          </span>
        </span>
      </button>
      <button type="button" className="btn btn-secondary btn-icon lookdev-chat"
              aria-label={t("Discuss {title}", { title: entry.title })} title={t("Discuss")} onClick={onDiscuss}>
        <ChatPlusGlyph />
      </button>
    </li>
  );
}

/** The agent conversation on a shader: the brief is written, then the chosen agent receives it. */
function LookdevHandoff({ project, entry, onClose }: {
  project: string;
  entry: LookdevSpecimen;
  onClose: () => void;
}) {
  return (
    <HandoffDialog<LookdevBrief>
      title={t("Discuss this shader")}
      eyebrow={entry.title}
      load={() => api.lookdevBrief(project, entry.id)}
      rows={() => [
        [t("Shader"), <span className="mono">{entry.res_path}</span>],
        [t("Type"), KINDS[entry.kind] ?? entry.kind],
        [t("Uses"), <span className="num">{entry.users.length}</span>],
      ]}
      submit={(choice) => api.lookdevHandoff(project, entry.id, choice)}
      sent={(session) => t("{title} handed to {title2}", { title: entry.title, title2: session.title })}
      onClose={onClose}
    />
  );
}

/* ----------------------------------------------------------- a specimen's room */

type Values = Record<string, unknown>;

/**
 * The settings touched, and their history. A continuous gesture (a slider
 * dragged, a color searched, a number typed) is a single step: `merge` extends
 * the step open on the same setting, `seal` closes it, `cancel` undoes it
 * without leaving anything in the history (right click or Esc during the
 * drag). "Back to the game" is a step like any other: it can be undone.
 * Saving changes what the game holds (`saved`): every state of the history
 * is rewritten against that new baseline, so undoing after a save brings back
 * the old values, to be saved again.
 */
interface Edits {
  present: Values;
  past: Values[];
  future: Values[];
  /** The setting whose step is still open. */
  open: string | null;
}

type Edit =
  | { kind: "set"; name: string; value: unknown; merge: boolean }
  | { kind: "seal" }
  | { kind: "cancel"; name: string }
  | { kind: "reset" }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "saved"; before: Values; after: Values };

/** Beyond this, the oldest steps are forgotten. */
const HISTORY = 200;

const NO_EDITS: Edits = { present: {}, past: [], future: [], open: null };

function edits(held: Edits, edit: Edit): Edits {
  const step = (present: Values): Edits => ({
    present, past: [...held.past, held.present].slice(-HISTORY), future: [], open: null,
  });
  switch (edit.kind) {
    case "set":
      if (edit.merge && held.open === edit.name) {
        return { ...held, present: { ...held.present, [edit.name]: edit.value } };
      }
      return { ...step({ ...held.present, [edit.name]: edit.value }), open: edit.merge ? edit.name : null };
    case "seal":
      return held.open === null ? held : { ...held, open: null };
    case "cancel":
      if (held.open !== edit.name || !held.past.length) return { ...held, open: null };
      return { ...held, present: held.past[held.past.length - 1]!, past: held.past.slice(0, -1), open: null };
    case "reset":
      return Object.keys(held.present).length ? step({}) : held;
    case "undo":
      if (!held.past.length) return held;
      return {
        present: held.past[held.past.length - 1]!,
        past: held.past.slice(0, -1),
        future: [held.present, ...held.future],
        open: null,
      };
    case "redo":
      if (!held.future.length) return held;
      return {
        present: held.future[0]!,
        past: [...held.past, held.present],
        future: held.future.slice(1),
        open: null,
      };
    case "saved": {
      // What a state showed is still what it shows; only the baseline moved.
      const rebase = (state: Values): Values => {
        const next = { ...state };
        for (const [name, value] of Object.entries(edit.after)) {
          const shown = name in state ? state[name] : edit.before[name];
          if (same(shown, value)) delete next[name];
          else next[name] = shown;
        }
        return next;
      };
      return {
        present: rebase(held.present),
        past: held.past.map(rebase),
        future: held.future.map(rebase),
        open: null,
      };
    }
  }
}

/** A text field: Ctrl+Z there undoes typing, not the settings. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target instanceof HTMLTextAreaElement) return true;
  return target instanceof HTMLInputElement && ["text", "search"].includes(target.type);
}

function Inspector({ project, id, background, paused, onClose, onDiscuss, onSaved }: {
  project: string;
  id: string;
  /** The game's background: the live frame is rendered over it, and so is the room. */
  background: string;
  /** A conversation is open on top: Esc belongs to it. */
  paused: boolean;
  onClose: () => void;
  onDiscuss: (entry: LookdevSpecimen) => void;
  /** The game changed: the specimen's thumbnail is redone. */
  onSaved: (id: string) => void;
}) {
  const { data, error } = useLookdevSpecimen(project, id);
  const { notify } = useStudio();
  const client = useQueryClient();
  const present = useSetLookdevState();
  // The use shown and the stand-in's shape are the specimen's presentation:
  // they are saved with it (`.gamestudio/documents/lookdev/<id>.json`). Local
  // state shows them at once, without waiting for the answer.
  const [preset, setPreset] = useState<string | null>(null);
  const [shape, setShape] = useState<string | null>(null);
  const keep = (body: { preset: string } | { shape: string }) =>
    present.mutate({ project, specimen: id, body }, {
      onError: (failure) => notify({
        kind: "error", title: t("Presentation not saved"), body: (failure as Error).message,
      }),
    });
  const choosePreset = (value: string) => {
    setPreset(value);
    keep({ preset: value });
  };
  const chooseShape = (value: string) => {
    setShape(value);
    keep({ shape: value });
  };
  const [history, edit] = useReducer(edits, NO_EDITS);
  const overrides = history.present;
  const [search, setSearch] = useState("");
  const [screenId, setScreenId] = useState<ScreenId>("studio");
  const [saving, setSaving] = useState(false);
  const save = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (paused) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        save.current();
        return;
      }
      if (typing(event.target)) return;
      if (key === "z" || key === "y") {
        event.preventDefault();
        edit({ kind: key === "y" || event.shiftKey ? "redo" : "undo" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paused, onClose]);

  const chosen = preset ?? data?.preset ?? "";
  const presetValues: Values = useMemo(() => {
    if (!data) return {};
    if (chosen === "default") return {};
    const found = data.presets.find((entry) => entry.id === chosen) ?? data.presets[0];
    return found?.params ?? {};
  }, [data, chosen]);
  const orbit = data ? data.staged || data.kind === "spatial" || data.kind === "sky" : false;
  const flat = data?.kind === "canvas_item" && !data.staged;
  const simulated = SCREENS.find((entry) => entry.value === screenId);
  const device = data && simulated ? onScreen(data.screen, simulated.short, flat) : null;
  const scale = device ? device.render
    : flat && data?.size ? Math.min(3, Math.max(1, 560 / data.size[0])) : 1;
  // Pixel for pixel: one device pixel on one pixel of the station's screen.
  const display = device ? device.show / (window.devicePixelRatio || 1) : null;

  // What the game holds for each setting, and what departs from it: that is
  // what "Save" writes.
  const baseline = useCallback((name: string): unknown => {
    if (name in presetValues) return presetValues[name];
    return data?.uniform_list.find((uniform) => uniform.name === name)?.default;
  }, [presetValues, data]);
  const pending = useMemo(() => Object.fromEntries(
    Object.entries(overrides).filter(([name, value]) => !same(value, baseline(name))),
  ), [overrides, baseline]);
  const unsaved = Object.keys(pending).length;
  const shownUse = chosen === "default" ? null
    : data?.presets.find((entry) => entry.id === chosen) ?? data?.presets[0] ?? null;
  const destination = shownUse ? shownUse.file : data?.file ?? "";

  save.current = () => {
    if (!data || !unsaved || saving) return;
    setSaving(true);
    const before = Object.fromEntries(Object.keys(pending).map((name) => [name, baseline(name)]));
    void (async () => {
      try {
        const done = await api.saveLookdev(project, id, { params: pending, preset: chosen });
        // The game is read again first, the history rewritten next: no frame
        // goes back through the old values.
        await client.refetchQueries({ queryKey: ["lookdevSpecimen", project, id], exact: true });
        edit({ kind: "saved", before, after: pending });
        onSaved(id);
        notify({
          kind: "success",
          title: t("Saved to the game"),
          body: tn(done.written.length, "{n} setting — {file}", "{n} settings — {file}", { file: done.file }),
        });
      } catch (failure) {
        notify({ kind: "error", title: t("Settings not saved"), body: tr((failure as Error).message) });
      } finally {
        setSaving(false);
      }
    })();
  };

  // The camera is not here: it lives in the live frame, so that a gesture does
  // not redraw a hundred settings on every mouse pixel.
  const query: LookdevFrameQuery = useMemo(() => ({
    params: overrides,
    preset: chosen,
    shape: shape ?? undefined,
    scale,
    format: "jpg",
    background: background || undefined,
  }), [overrides, chosen, shape, scale, background]);

  const uniforms = (data?.uniform_list ?? []).filter((entry) =>
    !search || entry.name.toLowerCase().includes(search.toLowerCase())
    || entry.group.toLowerCase().includes(search.toLowerCase()));
  const edited = Object.keys(overrides).length;

  return (
    <div className="lookdev-inspect" role="dialog" aria-modal="true" aria-label={data?.title ?? id}>
      <div className="lookdev-inspect-stage">
        <header className="lookdev-inspect-head">
          <div>
            <h2>{data?.title ?? id}</h2>
            <p className="mono">{data?.res_path}</p>
          </div>
          <div className="lookdev-inspect-tools">
            {data && data.bench?.ok && (
              <>
                {device && <span className="num lookdev-screen-scale">×{device.show.toFixed(2).replace(/\.?0+$/, "")}</span>}
                <Seg<ScreenId>
                  value={screenId}
                  options={[{ value: "studio", label: t("Studio") },
                    ...SCREENS.map((entry) => ({ value: entry.value, label: entry.value }))]}
                  onChange={setScreenId}
                />
              </>
            )}
            <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Close")} onClick={onClose}>
              <CloseCross />
            </button>
          </div>
        </header>
        {error ? (
          <Empty title={(error as Error).message} />
        ) : data && data.bench && !data.bench.ok ? (
          <Empty title={tr(data.bench.error)} />
        ) : data ? (
          <LiveFrame project={project} id={id} query={query} flat={flat} orbit={orbit}
                     animated={data.animated} display={display} />
        ) : (
          <Empty title={t("Godot is preparing the bench…")} />
        )}
        {data && (
          <Toolbar className="lookdev-inspect-bar">
            {data.presets.length > 0 && (
              <ToolGroup label={t("Use")}>
                <Select
                  label={t("Use")}
                  className="lookdev-select"
                  value={chosen || data.presets[0]?.id || "default"}
                  onChange={choosePreset}
                  options={[
                    ...data.presets.map((entry) => ({ value: entry.id, label: tr(entry.label) })),
                    { value: "default", label: t("Shader values"), divider: true },
                  ]}
                />
              </ToolGroup>
            )}
            {data.kind === "spatial" && !data.staged && (
              <ToolGroup label={t("Shape")}>
                <Seg value={shape ?? (data.shape || "sphere")} options={SHAPES} onChange={chooseShape} />
              </ToolGroup>
            )}
            <ToolGroup end>
              {data.staged ? <Badge>{t("Game object")}</Badge> : <Badge tone="quiet">{t("Stand-in")}</Badge>}
              <Badge tone="quiet">{KINDS[data.kind] ?? data.kind}</Badge>
            </ToolGroup>
          </Toolbar>
        )}
      </div>

      <aside className="lookdev-inspect-side">
        {data && (
          <>
            <button type="button" className="btn btn-primary lookdev-discuss"
                    onClick={() => onDiscuss(data)}>
              <ChatPlusGlyph />
              {t("Discuss")}
            </button>

            <section className="lookdev-controls">
              <div className="lookdev-controls-head">
                <h3>{t("Settings")} <span className="num">{data.uniform_list.length}</span></h3>
                <div className="lookdev-controls-tools">
                  <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Back to the game")}
                          title={t("Back to the game · {edited}", { edited })} disabled={!edited}
                          onClick={() => edit({ kind: "reset" })}>
                    <ResetGlyph />
                  </button>
                  <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Undo (Ctrl+Z)")}
                          title={t("Undo (Ctrl+Z)")} disabled={!history.past.length}
                          onClick={() => edit({ kind: "undo" })}>
                    <UndoGlyph />
                  </button>
                  <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Redo (Ctrl+Shift+Z)")}
                          title={t("Redo (Ctrl+Shift+Z)")} disabled={!history.future.length}
                          onClick={() => edit({ kind: "redo" })}>
                    <RedoGlyph />
                  </button>
                  <button type="button"
                          className={`btn btn-icon ${unsaved ? "btn-primary" : "btn-ghost"}`}
                          aria-label={t("Save to the game")}
                          title={unsaved
                            ? tn(unsaved, "Save {n} setting to {file} (Ctrl+S)",
                              "Save {n} settings to {file} (Ctrl+S)", { file: destination })
                            : t("Save to the game (Ctrl+S)")}
                          disabled={!unsaved || saving}
                          onClick={() => save.current()}>
                    <SaveGlyph />
                  </button>
                </div>
              </div>
              {data.uniform_list.length > 8 && (
                <input
                  className="lookdev-search"
                  placeholder={t("Filter")}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              )}
              <div className="lookdev-uniforms">
                {uniforms.map((uniform, index) => (
                  <Fragment key={uniform.name}>
                    {uniform.group && uniform.group !== uniforms[index - 1]?.group && (
                      <h4 className="lookdev-group">{uniform.group}</h4>
                    )}
                    <UniformControl
                      uniform={uniform}
                      value={uniform.name in overrides ? overrides[uniform.name]
                        : uniform.name in presetValues ? presetValues[uniform.name] : uniform.default}
                      changed={uniform.name in overrides}
                      edit={edit}
                    />
                  </Fragment>
                ))}
              </div>
            </section>

            <details className="more lookdev-sources">
              <summary>{t("Sources")}</summary>
              <ul>
                <li className="mono">{data.file}</li>
                {data.users.map((user) => <li key={user} className="mono">{user}</li>)}
              </ul>
            </details>
          </>
        )}
      </aside>
    </div>
  );
}

/**
 * The live frame: each frame requests the next as soon as it is shown. The
 * shader animates with the engine's time, each setting shows on the next
 * frame; dragging turns the camera, the wheel zooms. The camera lives here, in
 * refs: a gesture only wakes the loop, it does not redraw the room. A frame is
 * replaced only once decoded: nothing flickers.
 */
function LiveFrame({ project, id, query, flat, orbit, animated, display }: {
  project: string;
  id: string;
  query: LookdevFrameQuery;
  flat: boolean;
  orbit: boolean;
  animated: boolean;
  /**
   * A simulated screen: the specimen's base size times this factor, in CSS
   * pixels. Without it, the image keeps its natural size.
   */
  display: number | null;
}) {
  const image = useRef<HTMLImageElement>(null);
  // The render scale of the shown image: its base size follows from it.
  const drawn = useRef(1);
  const sizing = useRef(display);
  sizing.current = display;
  const fit = useCallback((natural?: number) => {
    const element = image.current;
    const width = natural ?? element?.naturalWidth ?? 0;
    if (!element || !width) return;
    element.style.width = sizing.current === null ? "" : `${(width / drawn.current) * sizing.current}px`;
  }, []);
  useEffect(() => fit(), [display, fit]);
  const meter = useRef<HTMLSpanElement>(null);
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const latest = useRef(query);
  latest.current = query;
  const view = useRef({ ...VIEW });
  const touched = useRef(0);
  const wake = useRef<(() => void) | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  const nudge = useCallback(() => {
    touched.current = performance.now();
    wake.current?.();
  }, []);
  // A setting, a use, a shape: the loop restarts.
  useEffect(nudge, [query, nudge]);

  useEffect(() => {
    let alive = true;
    let shown: string | null = null;
    let last = "";
    let failed: string | null = null;
    let count = 0;
    let since = performance.now();
    const rest = () => new Promise<void>((resolve) => {
      wake.current = resolve;
    });
    void (async () => {
      while (alive) {
        if (document.hidden) {
          await sleep(400);
          continue;
        }
        const state: LookdevFrameQuery = {
          ...latest.current,
          ...(orbit ? view.current : { yaw: 0, pitch: 0, zoom: 1 }),
        };
        const key = JSON.stringify(state);
        if (key === last && !animated && !failed) {
          // A static shader, nothing changed: its frame is already shown. Wait for a gesture.
          await rest();
          continue;
        }
        const started = performance.now();
        try {
          const response = await fetch(lookdevFrameUrl(project, id, state));
          if (!response.ok) {
            let detail = `${response.status}`;
            try {
              detail = (await response.json()).detail ?? detail;
            } catch {
              /* keep the status */
            }
            throw new Error(detail);
          }
          const url = URL.createObjectURL(await response.blob());
          const decoded = new Image();
          decoded.src = url;
          await decoded.decode();
          if (!alive) {
            URL.revokeObjectURL(url);
            break;
          }
          if (image.current) {
            drawn.current = state.scale ?? 1;
            image.current.src = url;
            fit(decoded.naturalWidth);
          }
          const previous = shown;
          shown = url;
          if (previous) setTimeout(() => URL.revokeObjectURL(previous), 200);
          last = key;
          if (failed !== null) {
            failed = null;
            setFailure(null);
          }
          setReady(true);
          count += 1;
          const now = performance.now();
          if (now - since >= 500 && meter.current) {
            const ms = Number(response.headers.get("X-Render-Ms")) || 0;
            meter.current.textContent = t("{round} fps · {ms} ms", { round: Math.round((count * 1000) / (now - since)), ms });
            count = 0;
            since = now;
          }
        } catch (error) {
          if (!alive) break;
          failed = (error as Error).message;
          setFailure(failed);
          await sleep(800);
        }
        const pace = performance.now() - touched.current < ACTIVE_MS ? 0 : IDLE_INTERVAL;
        await sleep(Math.max(0, pace - (performance.now() - started)));
      }
      if (shown) URL.revokeObjectURL(shown);
    })();
    return () => {
      alive = false;
      wake.current?.();
    };
  }, [project, id, orbit, animated]);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!orbit) return;
    drag.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const held = view.current;
    held.yaw += (event.clientX - drag.current.x) * 0.4;
    held.pitch = Math.max(-85, Math.min(85, held.pitch + (event.clientY - drag.current.y) * 0.3));
    drag.current = { x: event.clientX, y: event.clientY };
    nudge();
  };
  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (!orbit) return;
    const held = view.current;
    held.zoom = Math.max(0.3, Math.min(5, held.zoom * (event.deltaY > 0 ? 1 / 1.1 : 1.1)));
    nudge();
  };
  // A bench restarting is not the specimen's error: it comes back by itself.
  // This reads the server's message before translating it.
  const restarting = failure !== null && failure.includes("restarting");

  return (
    <div
      className={`lookdev-live ${flat ? "is-flat" : ""} ${orbit ? "can-orbit" : ""} ${
        display !== null ? "is-device" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => { drag.current = null; }}
      onWheel={onWheel}
    >
      <img ref={image} alt="" draggable={false} hidden={!ready} />
      {!ready && <Empty title={failure && !restarting ? tr(failure) : t("Godot is preparing the bench…")} />}
      <span className="lookdev-live-meta">
        {ready && failure && (restarting
          ? <Badge tone="quiet">{t("Godot is restarting the bench…")}</Badge>
          : <Badge tone="danger">{tr(failure)}</Badge>)}
        <span ref={meter} className="mono" />
      </span>
      {orbit && (
        <button
          type="button"
          className="btn btn-ghost btn-sm lookdev-live-reset"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => {
            view.current = { ...VIEW };
            nudge();
          }}
        >
          {t("Refit")}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- one setting */

const toHex = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 255)
  .toString(16).padStart(2, "0");

function colorHex(value: unknown): string {
  const list = Array.isArray(value) ? value as number[] : [1, 1, 1];
  return `#${toHex(list[0] ?? 0)}${toHex(list[1] ?? 0)}${toHex(list[2] ?? 0)}`;
}

function fromHex(hex: string, previous: unknown): number[] {
  const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const list = Array.isArray(previous) ? previous as number[] : [];
  return list.length > 3 ? [...rgb, list[3]!] : rgb;
}

/** A setting's range: the one the shader declares, else around its value. */
function range(uniform: UniformInfo, value: number): { min: number; max: number; step: number } {
  if (uniform.hint === HINT_RANGE) {
    const [min, max, step] = uniform.hint_string.split(",").map(Number);
    if (Number.isFinite(min) && Number.isFinite(max)) {
      return { min: min!, max: max!, step: Number.isFinite(step) ? step! : (max! - min!) / 200 };
    }
  }
  const base = Math.abs(Number(uniform.default ?? value)) || 1;
  const span = base * 3;
  const min = Number(uniform.default) < 0 || value < 0 ? -span : 0;
  return { min, max: span, step: uniform.type === "int" ? 1 : span / 300 };
}

/**
 * One setting. Memoized: when a slider moves, only it redraws, not a material's
 * hundred settings (`edit` is the room's `dispatch`, stable).
 */
const UniformControl = memo(function UniformControl({ uniform, value, changed, edit }: {
  uniform: UniformInfo;
  value: unknown;
  changed: boolean;
  edit: (edit: Edit) => void;
}) {
  const name = uniform.name;
  /** A continuous gesture: its successive values make one history step. */
  const onChange = (next: unknown) => edit({ kind: "set", name, value: next, merge: true });
  const seal = () => edit({ kind: "seal" });
  const label = (
    <span className={`lookdev-uniform-name mono ${changed ? "is-changed" : ""}`} title={uniform.type}>
      {uniform.name}
    </span>
  );
  if (uniform.type === "bool") {
    return (
      <div className="lookdev-uniform">
        {label}
        <Toggle checked={Boolean(value)} label={name}
                onChange={(next) => edit({ kind: "set", name, value: next, merge: false })} />
      </div>
    );
  }
  if (uniform.type === "float" || uniform.type === "int") {
    const number = Number(value ?? 0);
    const bounds = range(uniform, number);
    return (
      <div className="lookdev-uniform is-wide">
        {label}
        <span className="num">{uniform.type === "int" ? number : number.toFixed(3)}</span>
        <Slider value={number} min={Math.min(bounds.min, number)} max={Math.max(bounds.max, number)}
                step={bounds.step} bounds={false} onChange={onChange} onCommit={seal}
                onCancel={() => edit({ kind: "cancel", name })} />
      </div>
    );
  }
  const color = uniform.type === "Color";
  if (color || ((uniform.type === "Vector3" || uniform.type === "Vector4") && uniform.hint_string === "source_color")) {
    return (
      <div className="lookdev-uniform">
        {label}
        <span className="lookdev-color">
          <ColorInput value={colorHex(value)} onChange={(hex) => onChange(fromHex(hex, value))} onCommit={seal} />
          <span className="mono">{colorHex(value)}</span>
        </span>
      </div>
    );
  }
  if (Array.isArray(value)) {
    const list = value as number[];
    return (
      <div className="lookdev-uniform is-wide">
        {label}
        <span className="lookdev-vector">
          {list.map((component, index) => (
            <input
              key={index}
              type="number"
              step="any"
              className="num"
              value={Number(component.toFixed(4))}
              onChange={(event) => {
                const next = [...list];
                next[index] = Number(event.target.value);
                onChange(next);
              }}
              onBlur={seal}
            />
          ))}
        </span>
      </div>
    );
  }
  return (
    <div className="lookdev-uniform">
      {label}
      <span className="mono lookdev-fixed">{value === null || value === undefined ? "—" : String(value)}</span>
    </div>
  );
});

/**
 * A color searched in the picker: each hue passed over arrives through
 * `onChange`, the final choice through the native `change` event (which React
 * does not tell apart from `input`) -- that event closes the history step.
 */
function ColorInput({ value, onChange, onCommit }: {
  value: string;
  onChange: (hex: string) => void;
  onCommit: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const commit = useRef(onCommit);
  commit.current = onCommit;
  useEffect(() => {
    const element = input.current;
    if (!element) return undefined;
    const done = () => commit.current();
    element.addEventListener("change", done);
    element.addEventListener("blur", done);
    return () => {
      element.removeEventListener("change", done);
      element.removeEventListener("blur", done);
    };
  }, []);
  return <input ref={input} type="color" value={value} onChange={(event) => onChange(event.target.value)} />;
}

/* ----------------------------------------------------------------- icons */

/** Go back one step. */
function UndoGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 14L4 9l5-5" />
      <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
    </svg>
  );
}

/** Redo the undone step. */
function RedoGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M15 14l5-5-5-5" />
      <path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />
    </svg>
  );
}

/** Back to what the game holds: every changed setting falls back. */
function ResetGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M4.5 4.5v4h4" />
    </svg>
  );
}

/** Write into the game: the arrow goes down into the tray. */
function SaveGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 4v10.5" />
      <path d="M7.5 10l4.5 4.5 4.5-4.5" />
      <path d="M4.5 15v3.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V15" />
    </svg>
  );
}
