/**
 * 3D viewport: the 3D step of a world card, not a page without context.
 *
 * Three strips, like a 3D workbench: the tools (mesh, animate, sprites,
 * export), the chosen tool's panel, the scene. The mesh is born from the
 * card's chosen concept -- the "Mesh" panel comes from the workbench that
 * embeds this one --, then an agent rigs and animates it, it is rendered to
 * sprites, and its files are collected.
 *
 * Navigation modeled on Blender: drag to orbit, right click to pan, wheel to
 * zoom, 1/3/7 for front/side/top views, F to frame. The GLB carries all of the
 * entity's animations; one plays in the scene, the one chosen.
 */

import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bounds } from "@react-three/drei";
import { api, ApiError, assetFileUrl } from "../api";
import { useCharacter } from "../lib/queries";
import { useStudio } from "../lib/store";
import { inTauri, revealPath, writeClipboard } from "../lib/host";
import { godotAnimation } from "../lib/scene";
import { Choice, Facts, Toggle, bytes } from "../components/ui";
import HandoffDialog from "../components/Handoff";
import SpritePane from "../components/SpriteRender";
import {
  Floor, Lights, Model, Navigation, Reframe, SHADINGS, Stage, type Shading, type Stats,
} from "../components/Scene3D";
import { num, t } from "../lib/i18n";

interface Rig3D {
  mesh_asset_id?: string | null;
  bones?: unknown[];
  animations?: string[];
  normalization_report?: string;
}

/** The card the entity comes from: it is what the agent receives. */
export interface CardRef {
  section: string;
  name: string;
  title: string;
}

type Tool = "mesh" | "rig" | "sprites" | "export";

const TOOLS: { key: Tool; label: string; icon: ReactNode }[] = [
  { key: "mesh", label: t("Mesh"), icon: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /> },
  {
    key: "rig",
    label: t("Animate"),
    icon: (
      <>
        <circle cx="12" cy="4.5" r="2" />
        <path d="M12 6.5v7.5M12 14l-4 7M12 14l4 7M5.5 10.5L12 9l6.5 1.5" />
      </>
    ),
  },
  {
    key: "sprites",
    label: t("Sprites"),
    icon: (
      <>
        <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
        <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
        <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
        <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
      </>
    ),
  },
  { key: "export", label: t("Export"), icon: <path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14" /> },
];

/**
 * An entity's 3D space. `meshPane` is the mesh-making panel, provided by the
 * card's workbench; `placeholder` fills the scene while no mesh exists
 * (typically the chosen concept).
 */
export default function Viewport({ project, character, card, meshPane, placeholder }: {
  project: string;
  character: string;
  card: CardRef;
  meshPane: ReactNode;
  placeholder?: ReactNode;
}) {
  const { data: detail } = useCharacter(project, character);
  const rig3d = (detail?.rig3d ?? null) as Rig3D | null;
  const sheets = (detail?.spritesheets ?? {}) as Record<string, string>;
  const exports = (detail?.exports ?? {}) as Record<string, string>;
  const meshed = Boolean(rig3d?.mesh_asset_id);

  const [tool, setTool] = useState<Tool>(meshed ? "rig" : "mesh");
  // `undefined`: the file's first animation; `null`: rest pose.
  const [playing, setPlaying] = useState<string | null | undefined>(undefined);
  const [shading, setShading] = useState<Shading>("texture");
  const [skeleton, setSkeleton] = useState(false);
  const [grid, setGrid] = useState(true);
  const [light, setLight] = useState(false);
  const [spin, setSpin] = useState(false);
  const [frame, setFrame] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const stage = useRef<HTMLElement>(null);

  const mesh = rig3d?.mesh_asset_id ?? null;
  const { data: file } = useQuery({
    queryKey: ["asset", mesh],
    queryFn: () => api.asset(mesh!),
    enabled: Boolean(mesh),
  });
  // What the file carries is authoritative; the database only keeps a record.
  const animations = stats?.animations ?? rig3d?.animations ?? [];
  const shown = playing === undefined ? animations[0] ?? null : playing;

  useEffect(() => {
    setStats(null);
    setPlaying(undefined);
  }, [mesh]);

  // F frames, as in Blender -- except while typing.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (target.closest("input, textarea, select")) return;
      if (event.key === "f" || event.key === "F") setFrame((count) => count + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="ws ws-embed">
      <nav className="tools" aria-label={t("3D tools")}>
        {TOOLS.map((entry) => (
          <button
            key={entry.key}
            className="tool"
            aria-pressed={tool === entry.key}
            // Without a mesh, only the tool that makes one has anything to work on.
            disabled={!meshed && entry.key !== "mesh"}
            onClick={() => setTool(entry.key)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">{entry.icon}</svg>
            {entry.label}
          </button>
        ))}
      </nav>

      <section className="side" aria-label={t("Tool panel")}>
        {(tool === "mesh" || !meshed) && meshPane}
        {meshed && tool === "rig" && (
          <AnimPane
            project={project}
            card={card}
            stats={stats}
            rig3d={rig3d}
            animations={animations}
            playing={shown}
            onPlay={setPlaying}
            skeleton={skeleton}
            onSkeleton={setSkeleton}
          />
        )}
        {meshed && tool === "sprites" && (
          <SpritePane
            mesh={mesh}
            name={character}
            project={project}
            animations={animations.length}
            sheets={sheets}
          />
        )}
        {meshed && tool === "export" && (
          <ExportPane
            project={project}
            mesh={mesh}
            animations={animations}
            exports={exports}
            sheets={sheets}
            name={character}
          />
        )}
      </section>

      <section className="stage3d" ref={stage} data-bg={light ? "light" : "dark"} aria-label={t("3D scene")}>
        {mesh ? (
          <Stage>
            <Lights />
            {grid && <Floor light={light} />}
            <Suspense fallback={null}>
              <Bounds fit clip observe margin={1.3}>
                <Model
                  key={mesh}
                  url={assetFileUrl(mesh)}
                  shading={shading}
                  skeleton={skeleton}
                  onStats={setStats}
                  animation={shown}
                />
                <Reframe signal={frame} />
              </Bounds>
            </Suspense>
            <Navigation spin={spin} />
          </Stage>
        ) : (
          <div className="stage-msg">{placeholder ?? <b style={{ color: "var(--fg)" }}>{t("No mesh")}</b>}</div>
        )}

        {mesh && (
          <>
            <div className="float f-tl">
              <span className="chipname">
                <b>{character}</b>{" "}
                <span>· {project}{shown ? ` · ${godotAnimation(shown).name}` : ""}</span>
              </span>
              <div className="stats">
                <span>{t("triangles")} <b>{stats ? num(stats.triangles) : "—"}</b></span>
                <span>{t("vertices")} <b>{stats ? num(stats.vertices) : "—"}</b></span>
                <span>{t("bones")} <b>{stats ? stats.bones : "—"}</b></span>
                <span>{t("materials")} <b>{stats ? stats.materials : "—"}</b></span>
                <span>{t("file")} <b>{file ? bytes(file.size_bytes) : "—"}</b></span>
              </div>
            </div>

            <div className="float f-bc glass">
              <div className="seg" role="group" aria-label={t("Mesh display")}>
                {SHADINGS.map((entry) => (
                  <button key={entry.key} aria-pressed={shading === entry.key} onClick={() => setShading(entry.key)}>
                    {entry.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="float f-tr glass" role="toolbar" aria-label={t("Scene")}>
              <IconButton label={t("Skeleton")} pressed={skeleton} onClick={() => setSkeleton(!skeleton)}>
                <circle cx="12" cy="4.5" r="2" />
                <path d="M12 6.5v7.5M12 14l-4 7M12 14l4 7M5.5 10.5L12 9l6.5 1.5" />
              </IconButton>
              <IconButton label={t("Floor grid")} pressed={grid} onClick={() => setGrid(!grid)}>
                <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
              </IconButton>
              <IconButton label={t("Light / dark background")} pressed={light} onClick={() => setLight(!light)}>
                <circle cx="12" cy="12" r="8" />
                <path d="M12 4a8 8 0 0 1 0 16z" style={{ fill: "currentColor" }} />
              </IconButton>
              <IconButton label={t("Auto-rotate")} pressed={spin} onClick={() => setSpin(!spin)}>
                <path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4" />
              </IconButton>
              <IconButton label={t("Refit (F)")} onClick={() => setFrame((count) => count + 1)}>
                <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
              </IconButton>
              <IconButton
                label={t("Full screen")}
                onClick={() => {
                  if (document.fullscreenElement) void document.exitFullscreen();
                  else void stage.current?.requestFullscreen();
                }}
              >
                <path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" />
              </IconButton>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function IconButton({ label, pressed, onClick, children }: {
  label: string;
  pressed?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button className="ibtn" aria-label={label} title={label} aria-pressed={pressed} onClick={onClick}>
      <svg viewBox="0 0 24 24" aria-hidden="true">{children}</svg>
    </button>
  );
}

/* -------------------------------------------------------------------- panels */

function AnimPane({ project, card, stats, rig3d, animations, playing, onPlay, skeleton, onSkeleton }: {
  project: string;
  card: CardRef;
  stats: Stats | null;
  rig3d: Rig3D | null;
  animations: string[];
  playing: string | null;
  onPlay: (animation: string | null) => void;
  skeleton: boolean;
  onSkeleton: (value: boolean) => void;
}) {
  const [handing, setHanding] = useState(false);
  // A GLB may arrive already rigged without the database listing its bones:
  // what is measured on the file is authoritative.
  const bones = stats?.bones ?? rig3d?.bones?.length ?? 0;
  const done = bones > 0 || animations.length > 0;

  return (
    <>
      <header className="side-head">
        <h2>{t("Rig and animations")}</h2>
      </header>
      <div className="side-body">
        <Facts
          rows={[
            [t("Bones"), stats ? stats.bones : rig3d?.bones?.length ?? "—"],
            [t("Animations"), animations.length],
            [t("Inventory"), rig3d?.normalization_report || "—"],
          ]}
        />
        <div className="row" style={{ gap: "var(--space-3)" }}>
          <span className="grow" style={{ fontSize: "var(--text-sm)" }}>{t("Show the skeleton in the scene")}</span>
          <Toggle checked={skeleton} onChange={onSkeleton} label={t("Show the skeleton in the scene")} />
        </div>
        {animations.length > 0 && (
          <div className="blk" role="radiogroup" aria-label={t("Playing animation")}>
            <p className="eyebrow">{t("Play")}</p>
            {animations.map((entry) => {
              const named = godotAnimation(entry);
              return (
                <Choice
                  key={entry}
                  checked={playing === entry}
                  label={named.name}
                  sub={named.loop ? t("loop") : t("once")}
                  onSelect={() => onPlay(entry)}
                />
              );
            })}
            <Choice checked={playing === null} label={t("Rest")} onSelect={() => onPlay(null)} />
          </div>
        )}
      </div>
      <footer className="side-foot">
        <button className="btn btn-primary btn-block" onClick={() => setHanding(true)}>
          {done ? t("Pick up again with an agent") : t("Hand to an agent")}
        </button>
      </footer>
      {handing && (
        <HandoffDialog
          title={t("Rig and animate")}
          eyebrow={card.title}
          load={() => api.animationBrief(project, card.section, card.name)}
          rows={(brief) => [
            [t("Entity"), <span className="mono">{brief.entity}</span>],
            [t("Godot project"), <span className="mono">{brief.godot_ready ? brief.godot : "—"}</span>],
          ]}
          submit={(choice) => api.animationHandoff(project, card.section, card.name, choice)}
          sent={(session) => t("{title} handed to {title2}", { title: card.title, title2: session.title })}
          onClose={() => setHanding(false)}
        />
      )}
    </>
  );
}

function ExportPane({ project, mesh, animations, exports, sheets, name }: {
  project: string;
  mesh: string | null;
  animations: string[];
  exports: Record<string, string>;
  sheets: Record<string, string>;
  name: string;
}) {
  const { notify } = useStudio();
  const [bundling, setBundling] = useState(false);
  const [bundle, setBundle] = useState<{ path: string; files: string[]; size_bytes: number } | null>(null);
  const [seeking, setSeeking] = useState<string | null>(null);

  const copy = (text: string) => {
    writeClipboard(text).then(
      () => notify({ kind: "success", title: t("Path copied"), body: text }),
      (error: unknown) => notify({ kind: "error", title: t("Copy failed"), body: String(error) }),
    );
  };

  /** Shows a path in the file manager; a refusal is reported. */
  async function reveal(path: string) {
    const refusal = await revealPath(path);
    if (refusal) notify({ kind: "error", title: t("The folder does not open"), body: refusal });
  }

  /**
   * Shows a produced file where the library files it (`3d/<entity>/…`).
   *
   * No download: in the shell, the page is served by `tauri://localhost` and
   * the file by the local server, another origin -- `download` is ignored
   * there, and the link would navigate the window to the raw file. The file is
   * already on disk, in the project folder: that is where it is taken from.
   * The store path (`api.asset`) is a hash, never the one handed out; the
   * library names it.
   */
  async function show(asset: string) {
    setSeeking(asset);
    try {
      const tree = await api.tree(project, "3d", 4, "", 1000);
      const file = tree.files.find((entry) => entry.asset_id === asset);
      if (file) await reveal(file.path);
      else notify({ kind: "error", title: t("The folder does not open"), body: t("File missing from the project library.") });
    } catch (error) {
      notify({ kind: "error", title: t("The folder does not open"), body: (error as ApiError).message });
    } finally {
      setSeeking(null);
    }
  }

  // The bundle is an archive written by the server, outside the library: it is
  // opened in the file manager rather than downloaded.
  async function pack() {
    setBundling(true);
    try {
      const result = await api.bundle(project, name);
      setBundle(result);
      notify({ kind: "success", title: t("Bundle ready"), body: t("{length} file(s) · {bytes}", { length: result.files.length, bytes: bytes(result.size_bytes) }) });
      if (inTauri()) await reveal(result.path);
    } catch (error) {
      notify({ kind: "error", title: t("Export failed"), body: (error as ApiError).message });
    } finally {
      setBundling(false);
    }
  }
  return (
    <>
      <header className="side-head">
        <h2>{t("Export")}</h2>
      </header>
      <div className="side-body">
        {mesh || Object.keys(exports).length || Object.keys(sheets).length ? (
          <table className="table">
            <tbody>
              {mesh && (
                <tr>
                  <td className="task-name">
                    <b>{t("GLB mesh")}</b>
                    <span>
                      {`${name}.glb`}{animations.length
                        ? ` · ${animations.map((entry) => godotAnimation(entry).name).join(", ")}`
                        : ""}
                    </span>
                  </td>
                  <td className="num">
                    <button
                      className="btn btn-secondary btn-sm"
                      disabled={!inTauri() || seeking !== null}
                      onClick={() => void show(mesh)}
                    >
                      {t("Show on disk")}
                    </button>
                  </td>
                </tr>
              )}
              {Object.entries(sheets).map(([clip, asset]) => (
                <tr key={asset}>
                  <td className="task-name"><b>{t("Sprites · {clip}", { clip })}</b><span>{t("PNG sheet")}</span></td>
                  <td className="num">
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={!inTauri() || seeking !== null}
                      onClick={() => void show(asset)}
                    >
                      {t("Show on disk")}
                    </button>
                  </td>
                </tr>
              ))}
              {Object.entries(exports).map(([kind, path]) => (
                <tr key={kind}>
                  <td className="task-name"><b>{t("{kind} export", { kind })}</b><span className="path">{path}</span></td>
                  <td className="num">
                    <button className="btn btn-ghost btn-sm" onClick={() => copy(path)}>{t("Copy")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="hint">{t("Nothing to export")}</p>
        )}
      </div>
      <footer className="side-foot">
        <button className="btn btn-primary btn-block" disabled={bundling || !name} onClick={() => void pack()}>
          {bundling ? t("preparing…") : t("Export the bundle")}
        </button>
        {bundle ? (
          <div className="path-box">
            <span className="mono">{bundle.path}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => copy(bundle.path)}>{t("Copy")}</button>
          </div>
        ) : null}
      </footer>
    </>
  );
}
