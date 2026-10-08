/**
 * Compare: two images or two meshes, under the same eye.
 *
 * Images: four modes, one framing -- the same zoom on both, an overlay to
 * slide, a blink to tune, or the per-pixel difference. Meshes: side by side
 * under one camera (turn one, the other follows), or overlaid -- B in
 * wireframe over A --, with their measurements face to face.
 *
 * Nothing here costs anything: all local, no call to Runware.
 */

import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, assetFileUrl, assetPreviewUrl, thumbUrl, type Asset, type LibraryFile } from "../api";
import { useAssets, useEntities, useTree } from "../lib/queries";
import { useStudio } from "../lib/store";
import {
  Badge, Callout, Empty, Flags, PageHeader, Panel, Seg, Slider, ToolGroup, Toolbar, bytes, shortDate,
} from "../components/ui";
import { SHADINGS, cameraLink, type Shading, type Stats } from "../lib/scene";
import { ComparePicker, sourceFromAsset } from "../components/ComparePicker";
import {
  CompareStage,
  DEFAULT_THRESHOLD,
  type CompareBackdrop,
  type CompareMode,
  type CompareSource,
} from "../components/CompareStage";
import { num, t } from "../lib/i18n";

const MODES: { value: CompareMode; label: string }[] = [
  { value: "side", label: t("side by side") },
  { value: "overlay", label: t("overlay") },
  { value: "blink", label: t("blink") },
  { value: "diff", label: t("difference") },
];

const BACKDROPS: { value: CompareBackdrop; label: string }[] = [
  { value: "dark", label: t("dark") },
  { value: "checker", label: t("checkerboard") },
  { value: "light", label: t("light") },
];

/** The latest images, one click away: filling an empty slot needs no search. */
function Quick({ assets, onPick, label, hidden }: {
  assets: Asset[];
  onPick: (asset: Asset) => void;
  label: string;
  hidden?: string | null;
}) {
  // What already fills the other slot is not offered again.
  const shown = hidden ? assets.filter((asset) => asset.id !== hidden) : assets;
  if (!shown.length) return null;
  return (
    <>
      <span className="hint">{label}</span>
      <div className="cmp-quick">
        {shown.slice(0, 6).map((asset) => (
          <button className="ref" key={asset.id} title={asset.id} onClick={() => onPick(asset)}>
            <span className="thumb">
              <img src={assetPreviewUrl(asset.id, 160)} alt="" loading="lazy" />
            </span>
            <span className="cap">{shortDate(asset.created_at)}</span>
          </button>
        ))}
      </div>
    </>
  );
}

// three.js loads only when comparing meshes.
const MeshPreview = lazy(() => import("../components/MeshPreview"));

type Media = "image" | "mesh";

/** A store asset, as a comparator source. */
const sourceOf = (id: string): CompareSource => ({
  assetId: id,
  name: id.slice(0, 12),
  origin: t("store · {slice}", { slice: id.slice(0, 12) }),
  url: assetFileUrl(id),
});

/**
 * `#compare:<a>[,<b>]`: what another page sends to the comparator -- two of a
 * card's concepts, a library mesh. The first asset's kind says whether these
 * are images or meshes.
 */
function useSentPair(): { media: Media; a: CompareSource | null; b: CompareSource | null } | null {
  const [sent, setSent] = useState<{ media: Media; a: CompareSource | null; b: CompareSource | null } | null>(null);
  useEffect(() => {
    const [first, second] = (window.location.hash.split(":")[1] ?? "").split(",").filter(Boolean);
    if (!first) return;
    void api.asset(first).then((asset) => {
      setSent({
        media: asset.kind === "mesh" ? "mesh" : "image",
        a: sourceOf(first),
        b: second ? sourceOf(second) : null,
      });
    }).catch(() => undefined);
  }, []);
  return sent;
}

export default function Compare() {
  const sent = useSentPair();
  const [media, setMedia] = useState<Media>("image");
  useEffect(() => {
    if (sent) setMedia(sent.media);
  }, [sent]);

  const toggle = (
    <Seg<Media>
      value={media}
      onChange={setMedia}
      options={[
        { value: "image", label: t("Images") },
        { value: "mesh", label: t("3D meshes") },
      ]}
    />
  );
  return media === "image"
    ? <Images key={`i:${sent?.a?.assetId ?? ""}`} toggle={toggle}
              initial={sent?.media === "image" ? sent : null} />
    : <Meshes key={`m:${sent?.a?.assetId ?? ""}`} toggle={toggle}
              initial={sent?.media === "mesh" ? sent : null} />;
}

function Images({ toggle, initial }: {
  toggle: React.ReactNode;
  initial: { a: CompareSource | null; b: CompareSource | null } | null;
}) {
  const { project } = useStudio();
  const [a, setA] = useState<CompareSource | null>(initial?.a ?? null);
  const [b, setB] = useState<CompareSource | null>(initial?.b ?? null);
  const [mode, setMode] = useState<CompareMode>("side");
  const [backdrop, setBackdrop] = useState<CompareBackdrop>("dark");
  const [opacity, setOpacity] = useState(0.5);
  const [hz, setHz] = useState(3);
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLD);
  const { data: recent } = useAssets("image", 12, project);

  const same = Boolean(a && b && a.assetId === b.assetId);
  const quick = (asset: Asset) => sourceFromAsset(asset);

  return (
    <>
      <PageHeader
        title={t("Compare")}
        actions={
          <>
            {toggle}
            <button
              className="btn btn-secondary btn-sm"
              disabled={!a && !b}
              title={t("Put B in A and vice versa")}
              onClick={() => {
                setA(b);
                setB(a);
              }}
            >
              {t("⇄ Swap")}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={!a && !b}
              onClick={() => {
                setA(null);
                setB(null);
              }}
            >
              {t("Clear")}
            </button>
          </>
        }
      />

      <Panel style={{ marginBottom: "var(--space-5)" }}>
        <Toolbar>
          <ToolGroup label={t("Mode")}>
            <Seg value={mode} options={MODES} onChange={setMode} />
          </ToolGroup>
          <ToolGroup label={t("Background")}>
            <Seg value={backdrop} options={BACKDROPS} onChange={setBackdrop} />
          </ToolGroup>
          {mode === "overlay" && (
            <ToolGroup label={t("B opacity")}>
              <Slider value={opacity} min={0} max={1} step={0.01} bounds={false} onChange={setOpacity} />
              <span className="num tool-value">{Math.round(opacity * 100)} %</span>
            </ToolGroup>
          )}
          {mode === "blink" && (
            <ToolGroup label={t("Rate")}>
              <Slider value={hz} min={0.5} max={10} step={0.5} bounds={false} onChange={setHz} />
              <span className="num tool-value">{t("{v} Hz", { v: hz.toFixed(1) })}</span>
            </ToolGroup>
          )}
          {mode === "diff" && (
            <ToolGroup label={t("Threshold")}>
              <Slider value={threshold} min={0} max={96} step={1} bounds={false} onChange={setThreshold} />
              <span className="num tool-value">{threshold}</span>
            </ToolGroup>
          )}
          <ToolGroup label={t("Key")} end>
            <span className="kbd">{t("space")} <b>{t("held")}</b></span>
          </ToolGroup>
        </Toolbar>
      </Panel>

      {same && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <Callout>{t("Both slots hold the same image: the difference will be zero.")}</Callout>
        </div>
      )}

      <CompareStage
        a={a}
        b={b}
        mode={mode}
        backdrop={backdrop}
        opacity={opacity}
        blinkMs={1000 / hz}
        threshold={threshold}
        blankA={
          <Empty
            title={t("No image in A")}
            action={<Quick assets={recent ?? []} hidden={b?.assetId ?? null} onPick={(asset) => setA(quick(asset))} label={t("Recent images")} />}
          />
        }
        blankB={
          <Empty
            title={t("No image in B")}
            action={<Quick assets={recent ?? []} hidden={a?.assetId ?? null} onPick={(asset) => setB(quick(asset))} label={t("Recent images")} />}
          />
        }
        fallback={
          <Empty
            title={t("Compare two images")}
            action={<Quick assets={recent ?? []} hidden={b?.assetId ?? null} onPick={(asset) => setA(quick(asset))} label={t("Recent images")} />}
          />
        }
      />

      <div className="split-even" style={{ marginTop: "var(--space-5)" }}>
        <ComparePicker
          slot="A"
          source={a}
          project={project}
          hidden={b?.assetId ?? null}
          onPick={setA}
          onClear={() => setA(null)}
          note={same ? <Badge tone="warn">{t("same image as B")}</Badge> : undefined}
        />
        <ComparePicker
          slot="B"
          source={b}
          project={project}
          hidden={a?.assetId ?? null}
          onPick={setB}
          onClear={() => setB(null)}
          note={same ? <Badge tone="warn">{t("same image as A")}</Badge> : undefined}
        />
      </div>
    </>
  );
}

/* -------------------------------------------------------------------- meshes */

type MeshMode = "side" | "overlay";

const isMesh = (file: LibraryFile) => /\.(glb|gltf)$/i.test(file.name);

/** The project's meshes: the library's, named after their card when they have one. */
function useMeshes(project: string) {
  const { data: tree, isLoading } = useTree(project, "3d", 4, "", 1000);
  const { data: entities } = useEntities(project);
  const meshes = useMemo(() => {
    const files = tree?.files ?? [];
    return files.filter(isMesh).map((file) => {
      const entity = file.folder.split("/")[1] ?? "";
      const card = entities?.find((entry) => entry.entity === entity);
      const poster = files.find((other) => other.folder === `3d/${entity}` && other.name === "concept.png");
      return {
        file,
        poster,
        source: {
          assetId: file.asset_id,
          name: card?.title ?? entity,
          origin: t("library · {folder}", { folder: file.folder }),
          url: assetFileUrl(file.asset_id),
        } satisfies CompareSource,
      };
    });
  }, [tree, entities]);
  return { meshes, isLoading };
}

function MeshPicker({ slot, source, meshes, onPick, onClear }: {
  slot: "A" | "B";
  source: CompareSource | null;
  meshes: ReturnType<typeof useMeshes>["meshes"];
  onPick: (source: CompareSource) => void;
  onClear: () => void;
}) {
  return (
    <Panel
      eyebrow={t("Slot {slot}", { slot })}
      title={source?.name ?? t("No mesh")}
      actions={source ? <button className="btn btn-ghost btn-sm" onClick={onClear}>{t("Remove")}</button> : undefined}
    >
      {meshes.length ? (
        <div className="cmp-grid">
          {meshes.map((entry) => (
            <button
              className="ref"
              key={entry.file.asset_id + entry.file.folder}
              aria-pressed={source?.assetId === entry.file.asset_id}
              title={entry.source.origin}
              onClick={() => onPick(entry.source)}
            >
              <span className="thumb">
                {entry.poster ? <img src={thumbUrl(entry.poster, 200)} alt="" loading="lazy" />
                  : <span className="mono muted">{"glb"}</span>}
              </span>
              <span className="cap">{entry.source.name}</span>
            </button>
          ))}
        </div>
      ) : (
        <Empty title={t("No mesh in the project")} />
      )}
    </Panel>
  );
}

/** One measurement of A against B, and the difference when both are known. */
function Measure({ label, a, b, format = num }: {
  label: string;
  a: number | undefined;
  b: number | undefined;
  format?: (value: number) => string;
}) {
  const delta = a !== undefined && b !== undefined && a !== 0 ? (b - a) / a : null;
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{a === undefined ? "—" : format(a)}</td>
      <td className="num">{b === undefined ? "—" : format(b)}</td>
      <td className="num">
        {delta === null ? "—" : `${delta > 0 ? "+" : ""}${Math.round(delta * 100)} %`}
      </td>
    </tr>
  );
}

function Meshes({ toggle, initial }: {
  toggle: React.ReactNode;
  initial: { a: CompareSource | null; b: CompareSource | null } | null;
}) {
  const { project } = useStudio();
  const { meshes } = useMeshes(project);
  const [a, setA] = useState<CompareSource | null>(initial?.a ?? null);
  const [b, setB] = useState<CompareSource | null>(initial?.b ?? null);
  const [mode, setMode] = useState<MeshMode>("side");
  const [shading, setShading] = useState<Shading>("texture");
  const [grid, setGrid] = useState(true);
  const [light, setLight] = useState(false);
  const [statsA, setStatsA] = useState<Stats | null>(null);
  const [statsB, setStatsB] = useState<Stats | null>(null);
  // One camera for two scenes: the one being turned leads.
  const link = useMemo(() => cameraLink(), []);
  const { data: fileA } = useAssetSize(a?.assetId);
  const { data: fileB } = useAssetSize(b?.assetId);

  useEffect(() => setStatsA(null), [a?.assetId]);
  useEffect(() => setStatsB(null), [b?.assetId]);
  // Switching mode remounts the scenes: the linked camera starts over.
  useEffect(() => { link.leader = null; }, [mode, link]);

  const same = Boolean(a && b && a.assetId === b.assetId);

  return (
    <>
      <PageHeader
        title={t("Compare")}
        actions={
          <>
            {toggle}
            <button
              className="btn btn-secondary btn-sm"
              disabled={!a && !b}
              onClick={() => {
                setA(b);
                setB(a);
              }}
            >
              {t("⇄ Swap")}
            </button>
            <button className="btn btn-ghost btn-sm" disabled={!a && !b} onClick={() => { setA(null); setB(null); }}>
              {t("Clear")}
            </button>
          </>
        }
      />

      <Panel style={{ marginBottom: "var(--space-5)" }}>
        <Toolbar>
          <ToolGroup label={t("Mode")}>
            <Seg<MeshMode>
              value={mode}
              onChange={setMode}
              options={[
                { value: "side", label: t("side by side") },
                { value: "overlay", label: t("overlay") },
              ]}
            />
          </ToolGroup>
          <ToolGroup label={mode === "overlay" ? t("Render of A") : t("Render")}>
            <Seg<Shading>
              value={shading}
              onChange={setShading}
              options={SHADINGS.map((entry) => ({ value: entry.key, label: entry.label }))}
            />
          </ToolGroup>
          <ToolGroup label={t("View")} end>
            <Flags options={[
              { label: t("Grid"), checked: grid, onChange: setGrid },
              { label: t("Light background"), checked: light, onChange: setLight },
            ]} />
          </ToolGroup>
        </Toolbar>
      </Panel>

      {same && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <Callout>{t("Both slots hold the same mesh: the difference will be zero.")}</Callout>
        </div>
      )}

      <div className="cmp-stage cmp-3d" data-bg={light ? "light" : "dark"}>
        <div className="cmp-pair" data-layout={mode === "side" ? "pair" : "stack"}>
          {mode === "side" ? (
            (["A", "B"] as const).map((slot) => {
              const source = slot === "A" ? a : b;
              return (
                <div className="cmp-pane stage3d" key={slot} data-bg={light ? "light" : "dark"}>
                  {source ? (
                    <Suspense fallback={null}>
                      <MeshPreview
                        url={source.url}
                        shading={shading}
                        grid={grid}
                        light={light}
                        link={link}
                        id={slot}
                        onStats={slot === "A" ? setStatsA : setStatsB}
                      />
                    </Suspense>
                  ) : (
                    <div className="stage-msg">{t("No mesh in {slot}", { slot })}</div>
                  )}
                  {source && <span className="cmp-tag"><b>{slot}</b> {source.name}</span>}
                </div>
              );
            })
          ) : (
            <div className="cmp-pane stage3d" data-bg={light ? "light" : "dark"}>
              {a ? (
                <Suspense fallback={null}>
                  <MeshPreview
                    url={a.url}
                    ghost={b && !same ? b.url : null}
                    shading={shading}
                    grid={grid}
                    light={light}
                    onStats={setStatsA}
                    onGhostStats={setStatsB}
                  />
                </Suspense>
              ) : (
                <div className="stage-msg">{t("No mesh in A")}</div>
              )}
              {a && (
                <span className="cmp-tag">
                  <b>A</b> {a.name}{b ? <> · <b>B</b> {b.name} {t("in wireframe")}</> : null}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="split-even" style={{ marginTop: "var(--space-5)" }}>
        <Panel title={t("Measurements")} bodyClass="tight">
          <table className="table">
            <thead>
              <tr><th></th><th className="num">A</th><th className="num">B</th><th className="num">{t("difference")}</th></tr>
            </thead>
            <tbody>
              <Measure label={t("Triangles")} a={statsA?.triangles} b={statsB?.triangles} />
              <Measure label={t("Vertices")} a={statsA?.vertices} b={statsB?.vertices} />
              <Measure label={t("Bones")} a={statsA?.bones} b={statsB?.bones} />
              <Measure label={t("Materials")} a={statsA?.materials} b={statsB?.materials} />
              <Measure label={t("File")} a={fileA?.size_bytes ?? undefined} b={fileB?.size_bytes ?? undefined} format={bytes} />
            </tbody>
          </table>
        </Panel>
        <div className="stack-5">
          <MeshPicker slot="A" source={a} meshes={meshes} onPick={setA} onClear={() => setA(null)} />
          <MeshPicker slot="B" source={b} meshes={meshes} onPick={setB} onClear={() => setB(null)} />
        </div>
      </div>
    </>
  );
}

/** An asset's file size: the one measurement the scene does not give. */
function useAssetSize(id: string | undefined) {
  return useQuery({
    queryKey: ["asset", id],
    queryFn: () => api.asset(id!),
    enabled: Boolean(id),
  });
}
