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
 * (`lookdev.save`). A device selector shows the whole screen of a phone, a
 * tablet or a desktop, with the shader's use placed in it as the game's
 * stretch settings place it.
 *
 * The graphic style and the game type lead the sections: each written part by
 * part, with its influences and a thread with an agent (`DirectionRoom`).
 */

import {
  Fragment, memo, useCallback, useEffect, useMemo, useReducer, useRef, useState, type PointerEvent, type WheelEvent,
} from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  lookdevFontUrl, lookdevFrameUrl, lookdevThumbUrl, type UniformInfo,
  api, type DirectionAspect, type LookdevAspect, type LookdevAspectBrief, type LookdevBrief, type LookdevColor, type LookdevDevice,
  type LookdevFrameQuery, type LookdevIndex, type LookdevPalette, type LookdevSpecimen, type LookdevTopic,
  type LookdevTypography,
} from "../api";
import DirectionRoom from "../components/DirectionRoom";
import HandoffDialog from "../components/Handoff";
import { useSetLookdevState, useLookdev, useLookdevSpecimen } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Badge, CloseCross, Empty, Facts, Seg, Select, Slider, Toggle, ToolGroup, Toolbar } from "../components/ui";
import { ChatPlusGlyph } from "./Documents";
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
  { value: "cylinder", label: t("Cylinder") },
  { value: "capsule", label: t("Capsule") },
  { value: "torus", label: t("Torus") },
];
// The shape choice that opens the file picker: a model to lay the material on.
const IMPORT_MESH = "import-mesh";
const MESH_SOURCES: Record<string, string> = {
  game: t("Game"),
  library: t("Library"),
  import: t("Dropped"),
};

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

/** The studio's own view of the specimen, or the whole screen of a device. */
type ScreenId = "studio" | LookdevDevice["id"];

const DEVICE_NAMES: Record<LookdevDevice["id"], string> = {
  phone: t("Phone"),
  tablet: t("Tablet"),
  desktop: t("Desktop"),
};

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
 * The game's fonts: every face of every family, each family under a name of
 * its own (`lookdev-<project>-<n>`), each face with its real weight and style.
 * A heading in semibold then uses the game's semibold file, not a weight the
 * browser makes up from the regular one. `page` is the family the page is set
 * in -- the game's default font --, `null` until it is there.
 *
 * Their bytes go through `fetch`, never `url(…)`: the shell's CSP
 * (`font-src 'self' data:`) refuses a font served by the local API, while it
 * lets a request through (`connect-src`). The URL carries the token.
 */
function useGameFonts(project: string, data: LookdevIndex | undefined) {
  const [loaded, setLoaded] = useState<Record<string, string>>({});
  const families = data?.typography.families;
  const signature = JSON.stringify(families?.map((family) => [family.family, family.faces.map((face) => face.file)]) ?? []);
  useEffect(() => {
    if (!families?.length) return;
    let alive = true;
    const added: FontFace[] = [];
    void (async () => {
      const names: Record<string, string> = {};
      await Promise.all(families.map(async (family, index) => {
        const name = `lookdev-${project}-${index}`;
        const faces = await Promise.all(family.faces.map(async (face) => {
          try {
            const response = await fetch(lookdevFontUrl(project, face.file));
            if (!response.ok) throw new Error(`${response.status}`);
            return await new FontFace(name, await response.arrayBuffer(), {
              weight: String(face.weight),
              style: face.italic ? "italic" : "normal",
            }).load();
          } catch (error) {
            // An unreadable face does not keep the zone from opening: its
            // family keeps its other faces, and the console says why.
            console.warn("lookdev: game font not loaded", face.file, error);
            return null;
          }
        }));
        if (!alive) return;
        for (const face of faces) {
          if (face) {
            document.fonts.add(face);
            added.push(face);
          }
        }
        if (faces.some(Boolean)) names[family.family] = name;
      }));
      if (alive) setLoaded(names);
    })();
    return () => {
      alive = false;
      for (const face of added) document.fonts.delete(face);
    };
    // `signature` says when the families change; `families` itself is a new
    // array at each reading of the game.
  }, [project, signature]); // eslint-disable-line react-hooks/exhaustive-deps
  const page = data?.theme.font?.family;
  return { families: loaded, page: page ? loaded[page] ?? null : null };
}

/**
 * The sky behind the page, rendered for this window: as wide and as tall as
 * the first screen, times the screen's pixel density, so it is never
 * stretched. The bench's sky is 768×432 at scale 1.
 */
function skyScale(): number {
  const wanted = Math.max(window.innerWidth / 768, window.innerHeight / 432) * (window.devicePixelRatio || 1);
  return Math.min(4, Math.max(1, Math.ceil(wanted * 4) / 4));
}

/* ------------------------------------------------------------- the sections */

/** The aspects of the art direction, each in its own section. */
type Section = DirectionAspect | LookdevTopic;

const SECTION_KEY = "gs-lookdev-section";

/** The section a shader belongs to, by its type. */
function shaderSection(kind: string): Section {
  return kind === "canvas_item" ? "interface" : kind === "spatial" ? "materials" : kind === "sky" ? "sky" : "other";
}

/** The section shown, remembered for this visitor; one that has nothing falls back on the first. */
function useSection(available: Section[]): [Section | null, (section: Section) => void] {
  const [chosen, setChosen] = useState<Section | null>(() => {
    try {
      return localStorage.getItem(SECTION_KEY) as Section | null;
    } catch {
      return null;
    }
  });
  const choose = (section: Section) => {
    setChosen(section);
    try {
      localStorage.setItem(SECTION_KEY, section);
    } catch {
      /* A comfort: without storage, the page opens on its first section. */
    }
  };
  const shown = chosen && available.includes(chosen) ? chosen : available[0] ?? null;
  return [shown, choose];
}

/** A file's name, without its folders. */
const basename = (file: string) => file.split("/").pop() ?? file;

/** Where the game writes a color, as the Universe's sections name it. */
const ASPECTS: LookdevAspect[] = ["interface", "materials", "sky", "other"];
const ASPECT_LABELS: Record<LookdevAspect, string> = {
  interface: t("Interface"),
  materials: t("Materials"),
  sky: t("Sky"),
  other: t("Elsewhere"),
};
// The colors an aspect shows before it is unfolded: the most used.
const FOLDED_COLORS = 24;

/** The colors the Colors section holds; a material's colors are shown with the materials. */
const PALETTE_ASPECTS: LookdevAspect[] = ["interface", "sky", "other"];
const MATERIAL_ASPECTS: LookdevAspect[] = ["materials"];

/** A constant's name (`TEXT_DIM`) or a theme key (`Button/colors/font_color`). */
const NAMED = /^[A-Z][A-Z0-9_]*$|\//;

/**
 * A color the aspect builds on: written in several files, or carried by a
 * constant, a theme key or a shader setting -- where a palette is defined.
 * The others are written once, in passing.
 */
function isBase(color: LookdevColor, aspect: LookdevAspect): boolean {
  const uses = color.uses.filter((use) => use.aspect === aspect);
  return uses.length > 1 || uses.some((use) =>
    /\.gdshader(inc)?$/.test(use.file) || use.names.some((name) => NAMED.test(name)));
}

/** The colors used for these aspects, the most used for them first. */
function colorsFor(palette: LookdevPalette, aspects: LookdevAspect[]): LookdevColor[] {
  const weight = (color: LookdevColor) => aspects.reduce((total, aspect) => total + (color.aspects[aspect] ?? 0), 0);
  return palette.colors.filter((color) => weight(color) > 0).sort((a, b) => weight(b) - weight(a));
}

/**
 * The game's colors for some aspects: in the Colors section, their share as a
 * band then the colors of each aspect -- interface, sky, elsewhere --; in the
 * Materials section, the materials' own. Beside them, every place the chosen
 * one is written.
 */
function PaletteSection({ palette, aspects }: { palette: LookdevPalette; aspects: LookdevAspect[] }) {
  const colors = useMemo(() => colorsFor(palette, aspects), [palette, aspects]);
  const [chosen, setChosen] = useState(colors[0]?.hex ?? "");
  const [unfolded, setUnfolded] = useState<LookdevAspect[]>([]);
  const picked = colors.find((color) => color.hex === chosen) ?? colors[0];
  const single = aspects.length === 1;
  const groups = useMemo(() => aspects.map((aspect) => ({
    aspect,
    colors: palette.colors.filter((color) => color.aspects[aspect])
      .sort((a, b) => (b.aspects[aspect] ?? 0) - (a.aspects[aspect] ?? 0)),
  })).filter((group) => group.colors.length > 0), [palette, aspects]);
  if (!picked) return single ? null : <Empty title={t("No color written in the game")} />;
  const weight = (color: LookdevColor) => aspects.reduce((total, aspect) => total + (color.aspects[aspect] ?? 0), 0);
  const total = colors.reduce((sum, color) => sum + weight(color), 0);

  const list = (aspect: LookdevAspect, colors: LookdevColor[]) => (
    <ul className="lookdev-swatches">
      {colors.map((color) => {
        const uses = color.uses.filter((use) => use.aspect === aspect);
        const name = uses.find((use) => use.names.length > 0)?.names[0];
        return (
          <li key={color.hex}>
            <button type="button" className="lookdev-swatch" aria-pressed={color.hex === picked.hex}
                    onClick={() => setChosen(color.hex)}
                    title={uses.map((use) => use.file).join("\n")}>
              <span className="lookdev-swatch-chip lookdev-chip"
                    style={{ "--chip": color.hex } as React.CSSProperties} />
              <span className="lookdev-swatch-text">
                <span className="mono">{color.hex}</span>
                <span className="num">×{color.aspects[aspect]}</span>
                {name && <span className="mono lookdev-swatch-key">{name}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
  const swatches = groups.map((group) => {
    const open = unfolded.includes(group.aspect);
    // The colors the aspect builds on first, then the ones written once, in passing.
    const base = group.colors.filter((color) => isBase(color, group.aspect));
    const passing = group.colors.filter((color) => !isBase(color, group.aspect));
    const split = base.length > 0 && passing.length > 0;
    const shown = open ? passing : passing.slice(0, Math.max(0, FOLDED_COLORS - base.length));
    return (
      <section key={group.aspect} className="lookdev-subsection">
        <h3>
          {single ? t("Colors") : ASPECT_LABELS[group.aspect]}
          <span className="num lookdev-subsection-file">
            {tn(group.colors.length, "{n} color", "{n} colors")}
          </span>
        </h3>
        {split && (
          <h4 className="lookdev-palette-kind">
            {t("Base colors")} <span className="num">{base.length}</span>
          </h4>
        )}
        {base.length > 0 && list(group.aspect, base)}
        {split && (
          <h4 className="lookdev-palette-kind">
            {t("Passing colors")} <span className="num">{passing.length}</span>
          </h4>
        )}
        {shown.length > 0 && list(group.aspect, shown)}
        {shown.length < passing.length || open ? (
          <button type="button" className="btn btn-ghost lookdev-unfold"
                  onClick={() => setUnfolded((held) => open
                    ? held.filter((aspect) => aspect !== group.aspect) : [...held, group.aspect])}>
            {open ? t("Show fewer") : t("Show all {n}", { n: passing.length })}
          </button>
        ) : null}
      </section>
    );
  });

  return (
    <>
      {!single && (
        <div className="lookdev-band" role="img" aria-label={t("Game colors")}>
          {colors.map((color) => (
            <span key={color.hex} className="lookdev-chip"
                  style={{ "--chip": color.hex, flexGrow: weight(color) } as React.CSSProperties}
                  title={`${color.hex} · ${Math.round((100 * weight(color)) / Math.max(1, total))} %`} />
          ))}
        </div>
      )}
      <div className={`lookdev-colors ${single ? "is-single" : ""}`}>
        <div>{swatches}</div>
        <ColorUses color={picked} />
      </div>
    </>
  );
}

/** Where a color is written: aspect by aspect, each file with the names carrying it. */
function ColorUses({ color }: { color: LookdevColor }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [color.hex]);
  const copy = () => {
    void navigator.clipboard?.writeText(color.hex).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    }).catch(() => {
      /* Without a clipboard, the hex stays readable above. */
    });
  };
  return (
    <aside className="lookdev-uses" aria-label={t("Where {hex} is written", { hex: color.hex })}>
      <span className="lookdev-uses-chip lookdev-chip" style={{ "--chip": color.hex } as React.CSSProperties} />
      <div className="lookdev-uses-head">
        <b className="mono">{color.hex}</b>
        <span className="num">{tn(color.count, "written {n} time", "written {n} times")}</span>
        <button type="button" className="btn btn-ghost btn-icon" onClick={copy}
                aria-label={t("Copy the color")} title={copied ? t("Copied") : t("Copy the color")}>
          {copied ? <CheckGlyph /> : <CopyGlyph />}
        </button>
      </div>
      {ASPECTS.filter((aspect) => color.aspects[aspect]).map((aspect) => (
        <section key={aspect} className="lookdev-uses-aspect">
          <h4>{ASPECT_LABELS[aspect]} <span className="num">×{color.aspects[aspect]}</span></h4>
          <ul>
            {color.uses.filter((use) => use.aspect === aspect).map((use) => (
              <li key={use.file}>
                <span className="mono lookdev-uses-file">
                  {use.file.includes("/") && <span>{use.file.slice(0, use.file.lastIndexOf("/") + 1)}</span>}
                  {basename(use.file)}
                </span>
                {use.count > 1 && <span className="num">×{use.count}</span>}
                {use.names.length > 0 && <span className="mono lookdev-uses-names">{use.names.join(" · ")}</span>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </aside>
  );
}

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 àâçéèêëîïôûü «» — …";

/**
 * The game's typography: each family with its faces, set in the game's own
 * texts; the sizes it uses; its font resources, the default one first.
 */
function TypeSection({ typography, families }: {
  typography: LookdevTypography;
  /** CSS family name of each game family, once its faces are loaded. */
  families: Record<string, string>;
}) {
  const [text, setText] = useState(() =>
    typography.samples.find((sample) => sample.length >= 12) ?? typography.samples[0] ?? "");
  const sample = text.trim() || typography.samples[0] || GLYPHS;
  const fallback = "var(--font-body)";
  const css = (family: string) => (families[family] ? `"${families[family]}", ${fallback}` : fallback);
  const main = typography.default.family || typography.families[0]?.family || "";
  const weight = typography.families.flatMap((family) => family.faces).find((face) => face.default)?.weight;
  const resources = [...typography.resources].sort(
    (a, b) => Number(b.file === typography.default.resource) - Number(a.file === typography.default.resource));

  return (
    <>
      <div className="lookdev-type-bar">
        <input
          className="lookdev-sample"
          value={text}
          list="lookdev-samples"
          aria-label={t("Specimen text")}
          placeholder={t("Specimen text")}
          onChange={(event) => setText(event.target.value)}
        />
        <datalist id="lookdev-samples">
          {typography.samples.map((entry) => <option key={entry} value={entry} />)}
        </datalist>
      </div>

      {typography.families.map((family) => (
        <article key={family.family} className="lookdev-family">
          <header className="lookdev-family-head">
            <samp className="lookdev-family-aa" style={{ fontFamily: css(family.family) }}>{"Aa"}</samp>
            <div>
              <h3 style={{ fontFamily: css(family.family) }}>{family.family}</h3>
              <p className="num">
                {tn(family.faces.length, "{n} face", "{n} faces")}
                {family.faces[0]?.glyphs ? ` · ${tn(family.faces[0].glyphs, "{n} glyph", "{n} glyphs")}` : ""}
              </p>
            </div>
          </header>
          <ul className="lookdev-faces">
            {family.faces.map((face) => (
              <li key={face.file} className="lookdev-face">
                <div className="lookdev-face-meta">
                  <b>{face.style}</b>
                  <span className="num">{face.weight}</span>
                  {face.default ? <Badge>{t("Default font")}</Badge>
                    : face.uses === 0 ? <Badge tone="quiet">{t("Not cited")}</Badge>
                    : <span className="num">{tn(face.uses, "{n} use", "{n} uses")}</span>}
                  <span className="mono lookdev-face-file" title={face.file}>{basename(face.file)}</span>
                </div>
                <p className="lookdev-face-sample" style={{
                  fontFamily: css(family.family), fontWeight: face.weight,
                  fontStyle: face.italic ? "italic" : "normal",
                }}>
                  {sample}
                </p>
              </li>
            ))}
          </ul>
          <samp className="lookdev-glyphs" style={{ fontFamily: css(family.family), fontWeight: weight }}>{GLYPHS}</samp>
        </article>
      ))}

      {typography.sizes.length > 0 && (
        <section className="lookdev-subsection">
          <h3>{t("Sizes")}</h3>
          <ul className="lookdev-scale">
            {typography.sizes.map((size) => (
              <li key={size.size} title={size.files.join("\n")}>
                <span className="num lookdev-scale-size">{size.size}</span>
                <span className="lookdev-scale-sample" style={{ fontFamily: css(main), fontSize: size.size, fontWeight: weight }}>
                  {sample}
                </span>
                <span className="num">×{size.count}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {resources.map((resource) => (
        <section key={resource.file} className="lookdev-subsection">
          <h3>
            {resource.file === typography.default.resource ? t("Default font") : resource.type}
            <span className="mono lookdev-subsection-file">{resource.file}</span>
          </h3>
          <Facts rows={[
            [t("Type"), <span className="mono">{resource.type}</span>],
            [t("Base"), resource.base ? <span className="mono">{basename(resource.base)}</span> : null],
            [t("Fallbacks"), resource.fallbacks.length || resource.system.length
              ? <span className="mono">{[...resource.system, ...resource.fallbacks].map(basename).join(" → ")}</span> : null],
            [t("OpenType features"), resource.features.length
              ? <span className="mono">{resource.features.map((feature) => `${feature.tag}=${feature.value}`).join(" · ")}</span> : null],
            ...Object.entries(resource.settings).map(([key, value]): [React.ReactNode, React.ReactNode] => [
              <span className="mono">{key}</span>, <span className="mono">{value}</span>]),
          ]} />
        </section>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ the page */

export default function Lookdev() {
  const { project } = useStudio();
  return <Zone key={project} project={project} />;
}

function Zone({ project }: { project: string }) {
  const { data, error, isLoading } = useLookdev(project);
  const [discussing, setDiscussing] = useState<LookdevSpecimen | null>(null);
  const [topic, setTopic] = useState<LookdevTopic | null>(null);
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  // A rule of the art direction: ticked here, carried by the agents' briefs.
  const setRule = async (rule: string, value: boolean) => {
    try {
      const rules = await api.setLookdevRule(project, rule, value);
      queryClient.setQueryData<LookdevIndex>(["lookdev", project],
        (held) => (held ? { ...held, rules } : held));
    } catch (error) {
      notify({ kind: "error", title: t("Rule not saved"), body: tr((error as Error).message) });
    }
  };
  const [open, setOpen] = useState<string | null>(null);
  // A saved specimen changed in the game: its thumbnail is requested again.
  const [saved, setSaved] = useState<Record<string, number>>({});
  const fonts = useGameFonts(project, data);
  const family = fonts.page;
  const accent = data ? vivid(data.theme.colors) : null;
  // Once per page: a sky rendered for this window, without loss.
  const [scale] = useState(skyScale);

  const style = useMemo(() => {
    if (!data) return undefined;
    const theme = data.theme;
    const sky = theme.sky
      ? lookdevFrameUrl(project, theme.sky, { scale, yaw: 20, pitch: 8, format: "png" }) : "";
    return {
      "--u-bg": theme.background,
      "--u-ink": theme.colors[0] ?? undefined,
      "--u-accent": accent ?? undefined,
      // The studio's controls (tabs, sliders) take the game's accent.
      "--accent": accent ?? undefined,
      "--u-sky": sky ? `url("${sky}")` : undefined,
      fontFamily: family ? `"${family}", var(--font-body)` : undefined,
    } as React.CSSProperties;
  }, [data, project, family, accent, scale]);

  const sections = useMemo(() => {
    if (!data) return [];
    const count = (section: Section) => data.specimens.filter((entry) => shaderSection(entry.kind) === section).length;
    // The graphic style and the universe type lead, and always show: they are
    // discussed before anything is in them.
    const all: { value: Section; label: string; count: number; always?: boolean }[] = [
      { value: "style", label: t("Graphic style"), always: true,
        count: data.direction.style.written + data.direction.style.influences },
      { value: "game", label: t("Game type"), always: true,
        count: data.direction.game.written + data.direction.game.influences },
      { value: "colors", label: t("Colors"), count: colorsFor(data.palette, PALETTE_ASPECTS).length },
      { value: "typography", label: t("Typography"),
        count: data.typography.families.reduce((total, item) => total + item.faces.length, 0) },
      { value: "interface", label: t("Interface"), count: count("interface") },
      { value: "materials", label: t("Materials"), count: count("materials"),
        always: colorsFor(data.palette, MATERIAL_ASPECTS).length > 0 },
      { value: "sky", label: t("Sky"), count: count("sky") },
      { value: "other", label: t("Other shaders"), count: count("other") },
    ];
    return all.filter((entry) => entry.always || entry.count > 0);
  }, [data]);
  const [section, setSection] = useSection(sections.map((entry) => entry.value));
  const shown = (data?.specimens ?? []).filter((entry) => shaderSection(entry.kind) === section);
  const current = sections.find((entry) => entry.value === section);

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
      </header>

      {isLoading ? (
        <Empty title={t("Reading the game…")} />
      ) : error ? (
        <div className="lookdev-panel"><Empty title={(error as Error).message} /></div>
      ) : data && sections.length === 0 ? (
        <div className="lookdev-panel"><Empty title={t("No shader in the game")} /></div>
      ) : data && section && current ? (
        <>
          <nav className="lookdev-sections" aria-label={t("Art direction")}>
            <Seg<Section>
              value={section}
              onChange={setSection}
              options={sections.map((entry) => ({
                value: entry.value,
                label: <>{entry.label} <span className="num">{entry.count}</span></>,
              }))}
            />
          </nav>
          <section className={`lookdev-room is-${section}`}>
            <div className="lookdev-room-head">
              <h2>{current.label}</h2>
              {section === "materials" && (
                <label className="lookdev-rule">
                  {t("Must be procedural")}
                  <Toggle checked={data.rules.procedural_materials ?? false} label={t("Must be procedural")}
                          onChange={(value) => void setRule("procedural_materials", value)} />
                </label>
              )}
              {section !== "style" && section !== "game" && (
                <button type="button" className="btn btn-secondary btn-icon" title={t("Discuss")}
                        aria-label={t("Discuss {title}", { title: current.label })} onClick={() => setTopic(section)}>
                  <ChatPlusGlyph />
                </button>
              )}
            </div>
            {section === "style" || section === "game" ? (
              <DirectionRoom key={section} project={project} aspect={section} />
            ) : section === "colors" ? (
              <PaletteSection palette={data.palette} aspects={PALETTE_ASPECTS} />
            ) : section === "typography" ? (
              <TypeSection typography={data.typography} families={fonts.families} />
            ) : (
              <>
                {shown.length > 0 && (
                  <ul className="lookdev-grid">
                    {shown.map((entry) => (
                      <SpecimenCard key={entry.id} project={project} entry={entry} onOpen={() => setOpen(entry.id)}
                                    procedural={section === "materials" && (data.rules.procedural_materials ?? false)}
                                    version={`${entry.updated_at ?? ""}-${saved[entry.id] ?? 0}`}
                                    onDiscuss={() => setDiscussing(entry)} />
                    ))}
                  </ul>
                )}
                {section === "materials" && <PaletteSection palette={data.palette} aspects={MATERIAL_ASPECTS} />}
              </>
            )}
          </section>
        </>
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
      {topic && createPortal(
        <div className="lookdev-handoff">
          <TopicHandoff project={project} topic={topic} label={sections.find((entry) => entry.value === topic)?.label ?? ""}
                        count={sections.find((entry) => entry.value === topic)?.count ?? 0}
                        onClose={() => setTopic(null)} />
        </div>,
        document.body,
      )}
    </div>
  );
}

function SpecimenCard({ project, entry, version, procedural = false, onOpen, onDiscuss }: {
  project: string;
  entry: LookdevSpecimen;
  version: string;
  /** Materials must be procedural: one that reads an image says so. */
  procedural?: boolean;
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
            {procedural && entry.textures && <span className="lookdev-tag-warn">{t("Reads an image")}</span>}
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

/** The agent conversation on a whole section: its colors, its type, a family of shaders. */
function TopicHandoff({ project, topic, label, count, onClose }: {
  project: string;
  topic: LookdevTopic;
  label: string;
  count: number;
  onClose: () => void;
}) {
  return (
    <HandoffDialog<LookdevAspectBrief>
      title={t("Discuss this section")}
      eyebrow={label}
      load={() => api.lookdevAspectBrief(project, topic)}
      rows={() => [
        [t("Section"), label],
        [t("Elements"), <span className="num">{count}</span>],
      ]}
      submit={(choice) => api.lookdevAspectHandoff(project, topic, choice)}
      sent={(session) => t("{title} handed to {title2}", { title: label, title2: session.title })}
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
    if (value === IMPORT_MESH) {
      meshPicker.current?.click();
      return;
    }
    setShape(value);
    keep({ shape: value });
  };
  // A dropped model joins the project's, then carries the material at once.
  const meshPicker = useRef<HTMLInputElement>(null);
  const importMesh = async (file: File) => {
    try {
      const mesh = await api.importLookdevMesh(project, file);
      await client.invalidateQueries({ queryKey: ["lookdevSpecimen", project, id] });
      chooseShape(mesh.shape);
    } catch (failure) {
      notify({ kind: "error", title: t("Model refused"), body: tr((failure as Error).message) });
    }
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
  const device = data?.screen.devices.find((entry) => entry.id === screenId) ?? null;
  const scale = flat && data?.size ? Math.min(3, Math.max(1, 560 / data.size[0])) : 1;

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
    scale: device ? undefined : scale,
    format: "jpg",
    background: background || undefined,
    device: device?.id,
  }), [overrides, chosen, shape, scale, background, device]);

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
                {device && <span className="num lookdev-screen-scale">{device.width}×{device.height}</span>}
                <Seg<ScreenId>
                  value={screenId}
                  options={[{ value: "studio", label: t("Studio") },
                    ...data.screen.devices.map((entry) => ({
                      value: entry.id,
                      label: <DeviceGlyph id={entry.id} wide={entry.width > entry.height} />,
                      title: `${DEVICE_NAMES[entry.id]} · ${entry.width}×${entry.height}`,
                    }))]}
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
                     animated={data.animated} device={device !== null} />
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
                <Select
                  label={t("Shape")}
                  value={shape ?? (data.shape || "sphere")}
                  onChange={chooseShape}
                  options={[
                    ...SHAPES,
                    ...data.meshes.map((mesh, rank) => ({
                      value: mesh.shape, label: mesh.label, divider: rank === 0,
                      detail: MESH_SOURCES[mesh.source] ?? mesh.source,
                    })),
                    { value: IMPORT_MESH, label: t("Drop a model…"), divider: true, muted: true },
                  ]}
                />
                <input ref={meshPicker} type="file" accept=".glb,.gltf" hidden onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void importMesh(file);
                }} />
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
function LiveFrame({ project, id, query, flat, orbit, animated, device }: {
  project: string;
  id: string;
  query: LookdevFrameQuery;
  flat: boolean;
  orbit: boolean;
  animated: boolean;
  /** The image is a device's whole screen: fitted to the stage, in its bezel. */
  device: boolean;
}) {
  const image = useRef<HTMLImageElement>(null);
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
          if (image.current) image.current.src = url;
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
        device ? "is-device" : ""}`}
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

/** Copy: one sheet over another. */
function CopyGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="8.5" y="8.5" width="11" height="11" rx="1.5" />
      <path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" />
    </svg>
  );
}

/** Done: a tick. */
function CheckGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
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

/** A device by its outline: a phone, a tablet, a desktop monitor; turned when the game is held wide. */
function DeviceGlyph({ id, wide }: { id: LookdevDevice["id"]; wide: boolean }) {
  const turn = wide && id !== "desktop" ? "rotate(90 12 12)" : undefined;
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <g transform={turn}>
        {id === "phone" && (
          <>
            <rect x="7" y="3" width="10" height="18" rx="2" />
            <path d="M11 18h2" />
          </>
        )}
        {id === "tablet" && (
          <>
            <rect x="5" y="3" width="14" height="18" rx="2" />
            <path d="M11 18h2" />
          </>
        )}
        {id === "desktop" && (
          <>
            <rect x="3" y="4" width="18" height="12" rx="1.5" />
            <path d="M12 16v4M8 20h8" />
          </>
        )}
      </g>
    </svg>
  );
}
