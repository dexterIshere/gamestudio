/**
 * A world card's workbench: the card, its concepts, its 3D.
 *
 * Nothing is generated without context. An entity starts with its card -- what
 * it is, what it is for, what it looks like --, and that text seeds its
 * concepts. The images are iterated on, one is chosen, and that one goes to 3D.
 * The 3D viewport is therefore a step of the card and exists nowhere else. The
 * rig and animations are handed to an agent from that step.
 *
 * The logic lives in `service/entities.py`; the page only shows it.
 */

import { Suspense, lazy, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ApiError, assetPreviewUrl, type CardBrief, type WorldAxis, type Workbench,
} from "../api";
import HandoffDialog from "../components/Handoff";
import { ChatPlusGlyph, DocumentEditor } from "./Documents";
import { IMAGE_MODELS, MESH_MODELS } from "../lib/catalog";
import { inTauri, pickFile } from "../lib/host";
import { usePoses, usePrompts, useRefreshProject, useWorkbench } from "../lib/queries";
import { useStudio } from "../lib/store";
import {
  Badge, Choice, Dialog, Empty, Facts, Field, PageHeader, Panel, Seg, Select, Setting, Slider,
  State, Toggle, cost, shortDate,
} from "../components/ui";
import { decimal, num, t, tn, tr } from "../lib/i18n";

// three.js is most of the bundle's weight: it loads only at the 3D step.
const Viewport = lazy(() => import("./Viewport"));

export type Step = "card" | "concepts" | "3d";

const SIZES = [
  { label: t("Portrait"), sub: "768 × 1152", width: 768, height: 1152 },
  { label: t("Square"), sub: "1024 × 1024", width: 1024, height: 1024 },
  { label: t("Landscape"), sub: "1152 × 768", width: 1152, height: 768 },
];

/**
 * FLUX Kontext edits an image: without a starting concept, it has nothing to
 * edit. Same guard as a card's generation (`CardGenerate.tsx`).
 */
const NEEDS_REFERENCE = new Set(["runware:106@1"]);

/** The face-count bounds the service accepts (`service/produce.py`). */
const FACES = { min: 500, max: 50000, step: 500 };

/**
 * A Runware AIR reads `source:model@version`; an id of the Tripo API called
 * directly (`P2-20260801`, `tripo-v3.1`) does not. The id picks the route, and
 * therefore the account charged.
 */
const viaRunware = (air: string) => /^[^:\s]+:[^@\s]+@\S+$/.test(air);

export default function WorkbenchPage({ section, name, step, onStep, onGone }: {
  section: string;
  name: string;
  step: Step;
  onStep: (step: Step) => void;
  onGone: () => void;
}) {
  const { project } = useStudio();
  const { data: bench, error, isLoading } = useWorkbench(project, section, name);
  const [discussing, setDiscussing] = useState(false);

  if (!bench) {
    return (
      <div className="stack-4">
        <PageHeader title={name} project={project} />
        {error ? <Empty title={t("Card not found")} hint={(error as ApiError).message} />
          : <Empty title={isLoading ? t("Reading…") : t("Card not found")} />}
      </div>
    );
  }

  const character = bench.character;
  const steps: { key: Step; label: string; state: ReactNode; ready: boolean }[] = [
    { key: "card", label: t("Card"), state: t("written"), ready: true },
    {
      key: "concepts",
      label: t("Concepts"),
      state: bench.concept ? t("1 chosen") : bench.concepts.length ? t("{length} drawn", { length: bench.concepts.length }) : t("none"),
      ready: Boolean(bench.concept),
    },
    {
      key: "3d",
      label: "3D",
      state: character?.has_rig3d ? "mesh" : "—",
      ready: Boolean(character?.has_rig3d),
    },
  ];

  return (
    <div className="stack-5">
      <PageHeader
        title={bench.title}
        project={project}
        trail={[{ label: bench.section.label, href: `#world:${bench.section.id}` }]}
        actions={
          <>
            {bench.pending.length > 0 && (
              <State state="running" label={t("{length} task(s) running", { length: bench.pending.length })} />
            )}
            <button type="button" className="btn btn-secondary btn-icon"
                    aria-label={t("Discuss {title}", { title: bench.title })} title={t("Discuss")}
                    onClick={() => setDiscussing(true)}>
              <ChatPlusGlyph />
            </button>
          </>
        }
      />
      {discussing && (
        <EntityHandoff
          project={project}
          card={{ section: bench.section.id, name: bench.name, title: bench.title,
                   axes: bench.axes, concepts: bench.concepts.length,
                   concept: bench.concept, mesh: Boolean(character?.has_rig3d) }}
          axes={bench.section.axes}
          onClose={() => setDiscussing(false)}
        />
      )}

      <nav className="stepper" aria-label={t("Card steps")}>
        {steps.map((entry, index) => (
          <button
            key={entry.key}
            className={entry.ready ? "is-ready" : undefined}
            aria-current={entry.key === step ? "step" : undefined}
            onClick={() => onStep(entry.key)}
          >
            <span className="step-num">{index + 1}</span>
            <span className="txt">
              <b>{entry.label}</b>
              <span className="mono">{entry.state}</span>
            </span>
          </button>
        ))}
      </nav>

      {step === "card" && (
        <>
          <AxesPane bench={bench} />
          <DocumentEditor
            key={bench.name}
            project={project}
            folder={`world/${bench.section.id}`}
            name={bench.name}
            onDeleted={onGone}
            actions={(_document, dirty) => (
              <button className="btn btn-secondary btn-sm" disabled={dirty} onClick={() => onStep("concepts")}>
                {t("Go to concepts")}
              </button>
            )}
          />
        </>
      )}
      {step === "concepts" && <Concepts bench={bench} onNext={() => onStep("3d")} />}
      {step === "3d" && (
        <Suspense fallback={<div className="skeleton" style={{ height: 480 }} />}>
          <Viewport
            key={bench.entity}
            project={project}
            character={bench.entity}
            card={{ section: bench.section.id, name: bench.name, title: bench.title }}
            meshPane={<MeshPane bench={bench} onConcepts={() => onStep("concepts")} />}
            placeholder={<ConceptBoard bench={bench} onConcepts={() => onStep("concepts")} />}
          />
        </Suspense>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- filing */

/**
 * The card's filing along its section's axes: a declared grid
 * (`service/world.py`), one value chosen per axis. Without a declared axis,
 * nothing to show.
 */
function AxesPane({ bench }: { bench: Workbench }) {
  const { project, notify } = useStudio();
  const client = useQueryClient();
  const axes = bench.section.axes ?? [];
  if (!axes.length) return null;

  const choose = async (axis: string, value: string) => {
    try {
      await api.setEntityAxes(project, bench.section.id, bench.name, { [axis]: value });
      await client.invalidateQueries({
        queryKey: ["workbench", project, bench.section.id, bench.name],
      });
      await client.invalidateQueries({ queryKey: ["entities", project] });
    } catch (failure) {
      notify({ kind: "error", title: t("Filing refused"), body: (failure as Error).message });
    }
  };

  return (
    <Panel title={t("Filing")} bodyClass="stack-3">
      {axes.map((axis) => (
        <Setting key={axis.id} label={axis.label}>
          <Seg
            value={bench.axes?.[axis.id] ?? ""}
            onChange={(value) => void choose(axis.id, value)}
            options={[
              { value: "", label: "—" },
              ...axis.values.map((value) => ({ value: value.id, label: value.label })),
            ]}
          />
        </Setting>
      ))}
    </Panel>
  );
}

/* ------------------------------------------------------------------ concepts */

/** A card's concepts, by batch, most recent first. */
function useBatches(bench: Workbench) {
  return useMemo(() => {
    const batches = new Map<string, Workbench["concepts"]>();
    bench.concepts.forEach((concept) => {
      const key = concept.batch || concept.created_at.slice(0, 10);
      if (!batches.has(key)) batches.set(key, []);
      batches.get(key)!.push(concept);
    });
    return [...batches.entries()];
  }, [bench.concepts]);
}

function Concepts({ bench, onNext }: { bench: Workbench; onNext: () => void }) {
  const { project, notify } = useStudio();
  const refresh = useRefreshProject();
  const { data: poses } = usePoses();
  const { data: studioPrompts } = usePrompts();
  const batches = useBatches(bench);

  const [focus, setFocus] = useState<string | null>(bench.concept ?? bench.concepts[0]?.id ?? null);
  const [prompt, setPrompt] = useState(bench.prompt);
  const [negative, setNegative] = useState("");
  const [promptId, setPromptId] = useState("apose");
  const [count, setCount] = useState(4);
  const [model, setModel] = useState(IMAGE_MODELS[0]!.air);
  const [size, setSize] = useState(0);
  const [transparent, setTransparent] = useState(false);
  const [style, setStyle] = useState(true);
  const [pose, setPose] = useState("");
  const [reference, setReference] = useState<string | null>(null);
  const [strength, setStrength] = useState(0.6);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // A concept that just arrived shows itself while nothing is focused.
  useEffect(() => {
    if (!focus && bench.concepts.length) setFocus(bench.concepts[0]!.id);
  }, [focus, bench.concepts]);

  // Kontext does not apply without a starting concept: the default model replaces it.
  const image = IMAGE_MODELS.find(
    (entry) => entry.air === model && (reference !== null || !NEEDS_REFERENCE.has(entry.air)),
  ) ?? IMAGE_MODELS[0]!;
  const dimension = SIZES[size]!;
  const total = image.usd * count;

  async function compose() {
    try {
      const rendered = await api.renderPrompt(promptId, prompt);
      setPrompt(rendered.positive);
      setNegative(rendered.negative);
      setTransparent(rendered.transparent);
      if (rendered.pose) setPose(rendered.pose);
      const index = SIZES.findIndex(
        (entry) => entry.width === rendered.width && entry.height === rendered.height,
      );
      if (index >= 0) setSize(index);
    } catch (failure) {
      notify({ kind: "error", title: t("Prompt failed"), body: (failure as ApiError).message });
    }
  }

  async function draw() {
    setBusy(true);
    try {
      await api.concepts(project, bench.section.id, bench.name, {
        prompt,
        negative_prompt: negative,
        model: image.air,
        count,
        style,
        reference_asset_id: reference,
        strength,
        pose: pose || null,
        width: dimension.width,
        height: dimension.height,
        transparent,
        confirm: true,
      });
      refresh(project);
    } catch (failure) {
      notify({ kind: "error", title: t("Draw refused"), body: (failure as ApiError).message });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function choose(assetId: string | null) {
    try {
      await api.chooseConcept(project, bench.section.id, bench.name, assetId);
      refresh(project);
    } catch (failure) {
      notify({ kind: "error", title: t("Choice refused"), body: (failure as ApiError).message });
    }
  }

  const drawing = bench.pending.filter((job) => job.step.startsWith("concepts:"));

  return (
    <div className="split-wide">
      <div className="stack-5">
        <Panel
          title={t("Concepts")}
          eyebrow={t("{length} image(s) · {length2} batch(es)", { length: bench.concepts.length, length2: batches.length })}
          actions={
            focus && bench.concept && focus !== bench.concept ? (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => { window.location.hash = `compare:${bench.concept},${focus}`; }}
              >
                {t("Compare with the chosen one")}
              </button>
            ) : undefined
          }
          bodyClass="stack-6"
        >
          {drawing.length > 0 && (
            <div className="assets">
              {drawing.map((job) => (
                <div className="asset" key={job.id} aria-busy="true">
                  <span className="thumb plain"><span className="spin" aria-hidden="true" /></span>
                  <span className="meta">
                    <b>{job.state === "running" ? t("running") : t("queued")}</b>
                    <span>{job.id.slice(0, 8)}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          {!bench.concepts.length && !drawing.length && <Empty title={t("No concept")} />}
          {batches.map(([batch, concepts]) => (
            <div key={batch}>
              <p className="group-title">
                <span>{t("Batch {batch} · {shortDate}", { batch, shortDate: shortDate(concepts[0]!.created_at) })}</span>
                <span className="n">{concepts.length}</span>
              </p>
              <div className="assets">
                {concepts.map((concept) => (
                  <button
                    key={concept.id}
                    className="asset"
                    aria-pressed={concept.id === focus}
                    onClick={() => setFocus(concept.id)}
                    onDoubleClick={() => void choose(concept.id)}
                  >
                    <span className="thumb">
                      <img src={assetPreviewUrl(concept.id, 320)} alt="" loading="lazy" />
                    </span>
                    <span className="meta">
                      <b>{concept.id === bench.concept ? <State state="done" label={t("chosen")} /> : concept.id.slice(0, 8)}</b>
                      <span>{concept.reference ? t("variation") : t("draw")}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </Panel>
      </div>

      <div className="stack-5 sticky">
        <Panel
          title={focus ? (focus === bench.concept ? t("Chosen concept") : t("Concept")) : t("No concept")}
          eyebrow={focus ? focus.slice(0, 12) : undefined}
          bodyClass="stack-4"
          foot={
            focus ? (
              <div className="row row-wrap gap-3">
                {focus === bench.concept ? (
                  <button className="btn btn-ghost btn-sm" onClick={() => void choose(null)}>{t("Deselect")}</button>
                ) : (
                  <button className="btn btn-primary btn-sm" onClick={() => void choose(focus)}>{t("Choose")}</button>
                )}
                <button className="btn btn-secondary btn-sm" onClick={() => setReference(focus)}>{t("Vary")}</button>
                {bench.concept && (
                  <button className="btn btn-secondary btn-sm" onClick={onNext}>{t("Move to 3D")}</button>
                )}
              </div>
            ) : undefined
          }
        >
          {focus ? (
            <div className="board check concept-board">
              <img src={assetPreviewUrl(focus, 640)} alt="" />
            </div>
          ) : (
            <Empty title={t("Draw a first batch")} />
          )}
        </Panel>

        <Panel title={t("Draw concepts")} bodyClass="stack-4">
          <div className="setting-row" style={{ marginTop: 0, borderTop: 0, paddingTop: 0 }}>
            <span className="grow"><b>{t("Workbench prompt")}</b></span>
            <Select
              label={t("Workbench prompt")}
              value={promptId}
              options={(studioPrompts ?? []).map((entry) => ({ value: entry.id, label: tr(entry.label) }))}
              onChange={setPromptId}
            />
            <button className="btn btn-ghost btn-sm" disabled={!prompt.trim()} onClick={() => void compose()}>
              {t("Compose")}
            </button>
          </div>
          <Field label={t("What the images show")}>
            <textarea rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          </Field>
          <div className="row gap-3">
            <button
              className="btn btn-ghost btn-sm"
              disabled={prompt === bench.prompt}
              onClick={() => setPrompt(bench.prompt)}
            >
              {t("Reset to the card")}
            </button>
            <span className="spacer" />
            <span style={{ fontSize: "var(--text-sm)" }}>{t("Project style")}</span>
            <Toggle checked={style} onChange={setStyle} label={t("Project style")} />
          </div>

          {reference && (
            <div className="stack-3">
              <div className="ref-row">
                <span className="board ref-thumb">
                  <img src={assetPreviewUrl(reference, 120)} alt={t("Starting concept")} />
                </span>
                <div className="grow col" style={{ gap: "var(--space-1)" }}>
                  <b>{t("Variation")}</b>
                  <span className="mono muted">{reference.slice(0, 12)}</span>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setReference(null)}>{t("Remove")}</button>
              </div>
              <div className="field">
                <span>
                  {t("Faithfulness to the concept")}{" "}
                  <span className="mono" style={{ color: "var(--fg)" }}>{decimal(strength)}</span>
                </span>
                <Slider value={strength} min={0} max={1} step={0.05} onChange={setStrength} bounds={false} />
                <div className="ends"><span>{t("loose")}</span><span>{t("almost identical")}</span></div>
              </div>
            </div>
          )}

          <div className="field">
            <span>{t("Images")} <span className="mono" style={{ color: "var(--fg)" }}>{count}</span></span>
            <Slider value={count} min={1} max={8} step={1} onChange={setCount} bounds={false} />
          </div>

          <details className="more">
            <summary>{t("Model, frame, pose")}</summary>
            <div className="more-body stack-4">
              <div className="stack-3" role="radiogroup" aria-label={t("Image model")}>
                {IMAGE_MODELS.map((entry) => (
                  <Choice
                    key={entry.air}
                    checked={image.air === entry.air}
                    label={entry.label}
                    price={cost(entry.usd)}
                    disabled={reference === null && NEEDS_REFERENCE.has(entry.air)}
                    onSelect={() => setModel(entry.air)}
                  />
                ))}
              </div>
              <div className="pick pick-3" role="radiogroup" aria-label={t("Frame")}>
                {SIZES.map((entry, index) => (
                  <Choice
                    key={entry.label}
                    checked={size === index}
                    label={entry.label}
                    sub={entry.sub}
                    onSelect={() => setSize(index)}
                  />
                ))}
              </div>
              <div className="field">
                <span>{t("Pose")}</span>
                <Select
                  label={t("Pose")}
                  value={pose}
                  options={[
                    { value: "", label: t("No imposed pose"), muted: true },
                    ...(poses ?? []).map((entry) => ({
                      value: entry.name, label: entry.name, detail: tr(entry.description),
                    })),
                  ]}
                  onChange={setPose}
                />
              </div>
              <div className="row">
                <span className="grow" style={{ fontSize: "var(--text-sm)" }}>{t("Transparent background")}</span>
                <Toggle checked={transparent} onChange={setTransparent} label={t("Transparent background")} />
              </div>
              <Field label={t("To avoid")}>
                <input value={negative} onChange={(event) => setNegative(event.target.value)} />
              </Field>
            </div>
          </details>

          <div className="cost-box">
            <span className="eyebrow">{t("Estimated cost")}</span>
            <strong>{cost(total)}</strong>
            <span className="hint">{image.label} × {count}</span>
          </div>
          <button
            className="btn btn-primary btn-block"
            disabled={busy || !prompt.trim()}
            onClick={() => setConfirming(true)}
          >
            {busy ? t("sending…") : tn(count, "Draw {n} concept · {cost}", "Draw {n} concepts · {cost}", { cost: cost(total) })}
          </button>
        </Panel>
      </div>

      {confirming && (
        <Dialog
          eyebrow={t("Paid generation")}
          title={tn(count, "{n} concept of “{title}” for {total}?", "{n} concepts of “{title}” for {total}?",
            { title: bench.title, total: cost(total) })}
          onClose={() => setConfirming(false)}
          foot={
            <>
              <Badge tone="warn">{t("paid")}</Badge>
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setConfirming(false)}>{t("Back to the settings")}</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => void draw()}>
                {busy ? t("sending…") : t("Pay {total} and start", { total: cost(total) })}
              </button>
            </>
          }
        >
          <Facts
            rows={[
              [`${image.label} × ${count}`, cost(total)],
              [t("Frame"), `${dimension.label} · ${dimension.sub}`],
              [t("Variation"), reference ? reference.slice(0, 12) : "—"],
              [t("Pose"), pose || "—"],
              [t("Project style"), style ? t("yes") : t("no")],
            ]}
          />
          <p className="hint" style={{ margin: 0 }}>
            {t("Charged to your Runware account when it starts. If you cancel, nothing is billed.")}
          </p>
        </Dialog>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------ 3D */

/** The scene without a mesh: the chosen concept, waiting to go to 3D. */
function ConceptBoard({ bench, onConcepts }: { bench: Workbench; onConcepts: () => void }) {
  if (!bench.concept) {
    return (
      <div className="col" style={{ gap: "var(--space-3)", alignItems: "center" }}>
        <b style={{ color: "var(--fg)" }}>{t("No concept chosen")}</b>
        <button className="btn btn-secondary btn-sm" onClick={onConcepts}>{t("Go to concepts")}</button>
      </div>
    );
  }
  return <img className="stage-concept" src={assetPreviewUrl(bench.concept, 720)} alt={t("Chosen concept")} />;
}

/** The 3D step's "Mesh" panel: from the chosen concept to a mesh, paid or forged locally. */
function MeshPane({ bench, onConcepts }: { bench: Workbench; onConcepts: () => void }) {
  const { project, notify } = useStudio();
  const refresh = useRefreshProject();
  const [meshModel, setMeshModel] = useState(MESH_MODELS[0]!.air);
  const [faces, setFaces] = useState(8000);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState("");
  const mesh = MESH_MODELS.find((entry) => entry.air === meshModel) ?? MESH_MODELS[0]!;
  const meshed = Boolean(bench.character?.has_rig3d);
  const baking = bench.pending.some((job) => job.step.startsWith("entity:"));

  async function generate() {
    setBusy(true);
    try {
      await api.realize(project, bench.section.id, bench.name, {
        mesh_model: meshModel,
        face_limit: faces,
        confirm: true,
      });
      refresh(project);
    } catch (failure) {
      notify({ kind: "error", title: t("Mesh refused"), body: (failure as ApiError).message });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function attach(chosen: string) {
    setBusy(true);
    try {
      const result = await api.attachMesh(project, bench.section.id, bench.name, chosen, meshed);
      notify({ kind: "success", title: t("Mesh attached to {entity}", { entity: result.entity }) });
      setPath("");
      refresh(project);
    } catch (failure) {
      notify({ kind: "error", title: t("Import refused"), body: (failure as ApiError).message });
    } finally {
      setBusy(false);
    }
  }

  async function browse() {
    const chosen = await pickFile([{ name: t("3D mesh"), extensions: ["glb", "gltf", "fbx", "obj"] }]);
    if (chosen) await attach(chosen);
  }

  return (
    <>
      <header className="side-head">
        <h2>{t("Mesh")}</h2>
      </header>
      <div className="side-body">
        <div className="blk">
          <p className="eyebrow">{t("Chosen concept")}</p>
          {bench.concept ? (
            <div className="ref-row">
              <span className="board ref-thumb">
                <img src={assetPreviewUrl(bench.concept, 120)} alt={t("Chosen concept")} />
              </span>
              <span className="grow mono muted">{bench.concept.slice(0, 12)}</span>
              <button className="btn btn-ghost btn-sm" onClick={onConcepts}>{t("Change")}</button>
            </div>
          ) : (
            <button className="btn btn-secondary btn-block" onClick={onConcepts}>{t("Choose a concept")}</button>
          )}
        </div>

        <div className="blk" role="radiogroup" aria-label={t("Mesh model")}>
          <p className="eyebrow">{t("Model")}</p>
          {MESH_MODELS.map((entry) => (
            <Choice
              key={entry.air}
              checked={meshModel === entry.air}
              label={entry.label}
              sub={entry.usdMore ? t("{sub} · {usdMore} in detailed quality", { sub: entry.sub, usdMore: cost(entry.usdMore) }) : entry.sub}
              price={cost(entry.usd)}
              onSelect={() => setMeshModel(entry.air)}
            />
          ))}
        </div>

        <div className="blk">
          <div className="field">
            <span>
              {t("Face limit")} <span className="mono" style={{ color: "var(--fg)" }}>{num(faces)}</span>
            </span>
            <Slider value={faces} min={FACES.min} max={FACES.max} step={FACES.step} onChange={setFaces} bounds={false} />
            <div className="ends"><span>{t("low-poly")}</span><span>{t("detailed")}</span></div>
          </div>
        </div>

        <div className="blk">
          <p className="eyebrow">{t("Local path · free")}</p>
          {inTauri() ? (
            <button className="btn btn-ghost btn-block" disabled={busy} onClick={() => void browse()}>
              {t("Attach a locally forged .glb…")}
            </button>
          ) : (
            <div className="row gap-3">
              <input
                className="grow mono"
                value={path}
                placeholder="/path/to/model.glb"
                onChange={(event) => setPath(event.target.value)}
              />
              <button className="btn btn-ghost btn-sm" disabled={busy || !path.startsWith("/")} onClick={() => void attach(path)}>
                {t("Attach")}
              </button>
            </div>
          )}
        </div>
      </div>
      <footer className="side-foot">
        <div className="cost"><span>{t("Estimated cost")}</span><b>{cost(mesh.usd)}</b></div>
        <button
          className="btn btn-primary btn-block"
          disabled={!bench.concept || busy || baking}
          onClick={() => setConfirming(true)}
        >
          {baking ? t("mesh in progress…") : meshed ? t("Redo the mesh") : t("Generate the mesh")}
        </button>
      </footer>

      {confirming && (
        <Dialog
          eyebrow={t("Paid generation")}
          title={t("Mesh of “{title}” for {usd}?", { title: bench.title, usd: cost(mesh.usd) })}
          onClose={() => setConfirming(false)}
          foot={
            <>
              <Badge tone="warn">{t("paid")}</Badge>
              <span className="spacer" />
              <button className="btn btn-ghost" onClick={() => setConfirming(false)}>{t("Back to the settings")}</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => void generate()}>
                {busy ? t("sending…") : t("Pay {usd} and start", { usd: cost(mesh.usd) })}
              </button>
            </>
          }
        >
          <Facts
            rows={[
              [t("Mesh · {label}", { label: mesh.label }), cost(mesh.usd)],
              [t("Face limit"), num(faces)],
              [t("Concept"), bench.concept?.slice(0, 12) ?? "—"],
              ...(meshed ? [[t("Current mesh"), t("replaced")] as [string, string]] : []),
            ]}
          />
          <p className="hint" style={{ margin: 0 }}>
            {viaRunware(mesh.air)
              ? t("Charged to your Runware account when it starts. If you cancel, nothing is billed.")
              : t("Charged in credits to your Tripo account when it starts. If you cancel, nothing is billed.")}
          </p>
        </Dialog>
      )}
    </>
  );
}

/** A card's filing in plain words: "Race: Humans · Faction: —". */
export function filingLabel(axes: WorldAxis[], values: Record<string, string>): string | null {
  if (!axes.length) return null;
  return axes
    .map((axis) => `${axis.label}: ${axis.values.find((value) => value.id === values[axis.id])?.label ?? "—"}`)
    .join(" · ");
}

/**
 * The agent conversation on a world card: the brief states its filing, its
 * group siblings, its concepts and its 3D; the agent sums up, then waits for
 * the request.
 */
export function EntityHandoff({ project, card, axes, onClose }: {
  project: string;
  card: {
    section: string; name: string; title: string; axes: Record<string, string>;
    concepts: number; concept: string | null; mesh: boolean;
  };
  /** Its section's axes. */
  axes: WorldAxis[];
  onClose: () => void;
}) {
  const folder = `world/${card.section}`;
  return (
    <HandoffDialog<CardBrief>
      title={t("Discuss this card")}
      eyebrow={card.title}
      load={() => api.cardBrief(project, folder, card.name)}
      rows={(brief) => [
        [t("Section"), brief.section_label],
        [t("Filing"), filingLabel(axes, card.axes)],
        [t("Concepts"), <span className="num">{card.concepts}</span>],
        [t("Chosen concept"), card.concept ? t("Yes") : null],
        ["3D", card.mesh ? t("Mesh") : null],
      ]}
      submit={(choice) => api.cardHandoff(project, folder, card.name, choice)}
      sent={(session) => t("{title} handed to {title2}", { title: card.title, title2: session.title })}
      onClose={onClose}
    />
  );
}
