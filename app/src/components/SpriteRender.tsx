/**
 * Rendering a 3D mesh into sprite sheets: the "Sprites" panel of the 3D space.
 *
 * The bridge from 3D to 2D: pick a style, a number of directions and a view
 * angle, and Blender renders the mesh into as many sheets. Everything is local
 * and free, hence no spending confirmation here, unlike the mesh generation.
 */

import { useState } from "react";
import { api, ApiError, assetPreviewUrl } from "../api";
import { useSpriteStyles } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Choice, Slider, Toggle } from "./ui";
import { t, tn, tr } from "../lib/i18n";

/** Usual elevations: each one matches a game genre. */
const ELEVATIONS = [
  { value: 0, label: t("Side"), title: t("0° — platformer") },
  { value: 30, label: t("Iso"), title: t("30° — RPG, tactics") },
  { value: 45, label: t("High angle"), title: t("45° — high isometric") },
  { value: 90, label: t("Top"), title: t("90° — twin-stick") },
];

/** 1 renders a single front view; 8 is the usual choice for a 2D game. */
const DIRECTIONS = [1, 4, 8, 16];

export default function SpritePane({ mesh, name, project, animations, sheets }: {
  mesh: string | null;
  name: string;
  project: string;
  /** The mesh's animations: each one is rendered, one sheet per direction. */
  animations: number;
  /** The sheets already rendered for this character: animation_direction → asset. */
  sheets: Record<string, string>;
}) {
  const { notify } = useStudio();
  const { data: styles } = useSpriteStyles();

  const [style, setStyle] = useState("normal");
  const [directions, setDirections] = useState(8);
  const [size, setSize] = useState(128);
  const [elevation, setElevation] = useState(30);
  const [palette, setPalette] = useState(32);
  const [maxFrames, setMaxFrames] = useState(8);
  const [limitFrames, setLimitFrames] = useState(true);
  const [fps, setFps] = useState(12);
  const [busy, setBusy] = useState(false);

  // Each direction is rendered on each kept frame of each animation: that
  // product is the compute time, and it is what to show before launching.
  const animated = animations > 0;
  const frames = animated && limitFrames ? maxFrames : animated ? 24 : 1;
  const renders = directions * frames * Math.max(animations, 1);
  const rendered = Object.entries(sheets);

  async function submit() {
    if (!mesh) return;
    setBusy(true);
    try {
      await api.renderSprites({
        mesh,
        name: `${name}-${style}`,
        recipe: project || null,
        directions,
        size,
        elevation,
        style,
        palette,
        max_frames: animated && limitFrames ? maxFrames : 0,
        fps,
      });
      notify({
        kind: "success",
        title: t("Render queued"),
        body: t("{renders} images to render — tracked in the Control room, result in the Library.", { renders }),
      });
    } catch (error) {
      notify({ kind: "error", title: t("Render refused"), body: (error as ApiError).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="side-head">
        <h2>{t("Render to sprites")}</h2>
      </header>
      <div className="side-body">
        <div className="blk" role="radiogroup" aria-label={t("Render style")}>
          <p className="eyebrow">{t("Style")}</p>
          {(styles ?? []).map((entry) => (
            <Choice
              key={entry.name}
              checked={style === entry.name}
              label={tr(entry.label)}
              onSelect={() => setStyle(entry.name)}
            />
          ))}
        </div>

        <div className="blk">
          <p className="eyebrow">{t("Directions")}</p>
          <div className="seg full" role="group" aria-label={t("Directions")}>
            {DIRECTIONS.map((entry) => (
              <button key={entry} aria-pressed={directions === entry} onClick={() => setDirections(entry)}>
                {entry}
              </button>
            ))}
          </div>
        </div>

        <div className="blk">
          <p className="eyebrow">{t("View angle")}</p>
          <div className="seg full" role="group" aria-label={t("View angle")}>
            {ELEVATIONS.map((entry) => (
              <button
                key={entry.value}
                title={entry.title}
                aria-pressed={elevation === entry.value}
                onClick={() => setElevation(entry.value)}
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <span>
            {t("Frame size")} <span className="mono" style={{ color: "var(--fg)" }}>{t("{size} px", { size })}</span>
          </span>
          <Slider value={size} min={32} max={512} step={32} onChange={setSize} bounds={false} />
        </div>

        {style === "pixel" && (
          <div className="field">
            <span>
              {t("Palette colors")} <span className="mono" style={{ color: "var(--fg)" }}>{palette}</span>
            </span>
            <Slider value={palette} min={4} max={64} step={1} onChange={setPalette} bounds={false} />
          </div>
        )}

        {animated && (
          <div className="blk">
            <p className="eyebrow">{t("Animation")}</p>
            <div className="row">
              <span className="grow" style={{ fontSize: "var(--text-sm)" }}>{t("Limit the number of frames")}</span>
              <Toggle checked={limitFrames} onChange={setLimitFrames} label={t("Limit the number of frames")} />
            </div>
            {limitFrames && (
              <div className="field">
                <span>
                  {t("Frames kept")} <span className="mono" style={{ color: "var(--fg)" }}>{maxFrames}</span>
                </span>
                <Slider value={maxFrames} min={2} max={24} step={1} onChange={setMaxFrames} bounds={false} />
              </div>
            )}
            <div className="field">
              <span>
                {t("Frames per second")} <span className="mono" style={{ color: "var(--fg)" }}>{fps}</span>
              </span>
              <Slider value={fps} min={4} max={30} step={1} onChange={setFps} bounds={false} />
            </div>
          </div>
        )}

        {rendered.length > 0 && (
          <div className="blk">
            <p className="eyebrow">{t("Already rendered · {name}", { name })}</p>
            <div className="recent">
              {rendered.map(([clip, asset]) => (
                <span key={clip} title={clip}>
                  <img className={style === "pixel" ? "pixelated" : undefined} src={assetPreviewUrl(asset, 160)} alt={clip} />
                </span>
              ))}
            </div>
            <span className="hint">{rendered.map(([clip]) => clip).join(" · ")}</span>
          </div>
        )}
      </div>
      <footer className="side-foot">
        <div className="cost"><span>{t("Cost")}</span><b>{t("free")}</b></div>
        <button className="btn btn-primary btn-block" disabled={busy || !mesh} onClick={submit}>
          {busy ? t("sending…") : tn(directions, "Render {n} direction", "Render {n} directions")}
        </button>
        {mesh && (
          <p className="hint num">
            {[
              tn(renders, "{n} image", "{n} images"),
              ...(animated ? [tn(animations, "{n} animation", "{n} animations")] : []),
            ].join(" · ")}
          </p>
        )}
      </footer>
    </>
  );
}
