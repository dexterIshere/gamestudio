/**
 * Generating for a card: text to image, or image to image from its sketch or its render.
 *
 * A game design card also moves forward through images: describe the screen,
 * the mechanic or the effect, and a model draws proposals from it, from the
 * text alone, or starting from the user's sketch or the game's current render,
 * which set the composition. It is paid: the button states the amount, and
 * only the confirmation that repeats it sends the request with
 * `confirm: true`. Images arrive on their own: the workbench re-reads the
 * card's media every four seconds, and the waiting cells give way to them.
 */

import { useEffect, useId, useState } from "react";
import {
  ApiError, assetFileUrl, assetPreviewUrl, documentImageUrl,
  type CardFailure, type CardGeneration, type CardMedia, type CardReference,
  type CardReferenceKind, type CardRender,
} from "../api";
import { IMAGE_MODELS } from "../lib/catalog";
import { writeClipboard } from "../lib/host";
import { useGenerateCard } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Badge, Choice, Dialog, Empty, Facts, Field, Seg, Setting, Slider, Toggle, cost, shortDate } from "./ui";
import { decimal, locale, t, tn, tr } from "../lib/i18n";

type Mode = "text" | "image";
type Source = Exclude<CardReference, "">;
type Count = "1" | "2" | "4";
type FormatKey = "portrait" | "square" | "landscape";

const MODES: { value: Mode; label: string }[] = [
  { value: "text", label: t("Text → image") },
  { value: "image", label: t("Image → image") },
];

const COUNTS: { value: Count; label: string }[] = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "4", label: "4" },
];

const FORMATS: Record<FormatKey, { label: string; width: number; height: number }> = {
  portrait: { label: t("Portrait"), width: 768, height: 1344 },
  square: { label: t("Square"), width: 1024, height: 1024 },
  landscape: { label: t("Landscape"), width: 1344, height: 768 },
};

const FORMAT_OPTIONS = (Object.keys(FORMATS) as FormatKey[]).map((key) => ({
  value: key,
  label: FORMATS[key].label,
}));

const REFERENCE_LABELS: Record<CardReferenceKind, string> = {
  "": "—",
  sketch: t("Sketch"),
  render: t("Current render"),
  reference: t("Reference"),
};

/**
 * FLUX Kontext touches up an image: without a reference it has nothing to
 * touch up. The catalog does not carry this constraint; it is kept here, by AIR.
 */
const NEEDS_REFERENCE = new Set(["runware:106@1"]);
const DEFAULT_MODEL = IMAGE_MODELS[0]!;

/** Image-to-image strength: low, the image follows its reference; high, it breaks free. */
const STRENGTH = { min: 0.2, max: 1, step: 0.05, initial: 0.6 };

/** A card's settings, as the user left them. */
interface Draft {
  /** `null`: image to image as soon as there is a starting image, text otherwise. */
  mode: Mode | null;
  /** `null`: the first available reference, the sketch first. */
  source: Source | null;
  strength: number;
  /** `null`: the prompt follows the card's seed until it is edited. */
  prompt: string | null;
  model: string;
  count: Count;
  /** `null`: the render's aspect ratio. */
  format: FormatKey | null;
  style: boolean;
}

/**
 * Settings survive a tab change: go touch up the sketch, come back, the typed
 * prompt is still there. In memory only: another run of the application starts
 * again from the card.
 */
const drafts = new Map<string, Draft>();

/** An offered starting image: the sketch, the game's render, or an added reference. */
interface Reference {
  key: Source;
  label: string;
  url: string;
}

function referencesOf(project: string, folder: string, media: CardMedia): Reference[] {
  const found: Reference[] = [];
  if (media.sketch?.src) {
    // The sketch keeps its path when redrawn: its modification time forces
    // the thumbnail to reload.
    const url = `${documentImageUrl(project, folder, media.sketch.src)}&v=${media.sketch.mtime}`;
    found.push({ key: "sketch", label: REFERENCE_LABELS.sketch, url });
  }
  if (media.render) {
    found.push({ key: "render", label: REFERENCE_LABELS.render, url: assetPreviewUrl(media.render.asset_id, 320) });
  }
  for (const entry of media.references) {
    found.push({
      key: `ref:${entry.file}`,
      label: entry.file,
      url: documentImageUrl(project, folder, `${media.name}.references/${entry.file}`),
    });
  }
  return found;
}

/** The render's aspect ratio: a phone screen calls for a portrait. */
function formatOf(render: CardRender | null): FormatKey {
  if (!render?.width || !render.height) return "portrait";
  const ratio = render.width / render.height;
  return ratio < 0.85 ? "portrait" : ratio > 1.18 ? "landscape" : "square";
}

const modelLabel = (air: string) => IMAGE_MODELS.find((entry) => entry.air === air)?.label ?? (air || null);

/** A prompt cut down to a title line, between two words. */
function abbreviate(text: string, max = 72): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** Whether `a` is later than `b`: by date, or by text if either date is unreadable. */
function later(a: string, b: string): boolean {
  const left = Date.parse(a);
  const right = Date.parse(b);
  return Number.isNaN(left) || Number.isNaN(right) ? a > b : left > right;
}

/** The latest failure, as long as no image arrived since: a success clears it. */
function lastFailure(media: CardMedia): CardFailure | null {
  // `?.`: an older server does not send the field.
  const failure = media.failures?.[0];
  if (!failure) return null;
  const latest = media.generations[0];
  return !latest || later(failure.at, latest.created_at) ? failure : null;
}

/** A failure's time; plus the date if it is not from today. */
function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
    : shortDate(iso);
}

type Props = { project: string; folder: string; name: string; media: CardMedia };

export default function CardGenerate(props: Props) {
  // Another card starts from its own settings: the key remounts the panel.
  return <Generator key={JSON.stringify([props.project, props.folder, props.name])} {...props} />;
}

function Generator({ project, folder, name, media }: Props) {
  const card = JSON.stringify([project, folder, name]);
  const { notify } = useStudio();
  const generate = useGenerateCard();
  const promptId = useId();
  const references = referencesOf(project, folder, media);

  const [draft, setDraft] = useState<Draft>(() => drafts.get(card) ?? {
    mode: null,
    source: null,
    strength: STRENGTH.initial,
    prompt: null,
    model: DEFAULT_MODEL.air,
    count: "2",
    format: null,
    style: true,
  });
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);

  useEffect(() => {
    drafts.set(card, draft);
  }, [card, draft]);

  const patch = (change: Partial<Draft>) => setDraft((held) => ({ ...held, ...change }));

  // What actually applies: a reference that disappeared (sketch cleared)
  // falls back to text, and Kontext is not available without one.
  const reference = references.find((entry) => entry.key === draft.source) ?? references[0] ?? null;
  const imageMode = (draft.mode ?? "image") === "image" && reference !== null;
  const model = IMAGE_MODELS.find(
    (entry) => entry.air === draft.model && (imageMode || !NEEDS_REFERENCE.has(entry.air)),
  ) ?? DEFAULT_MODEL;
  const formatKey = draft.format ?? formatOf(media.render);
  const format = FORMATS[formatKey];
  const count = Number(draft.count);
  const total = model.usd * count;
  const prompt = draft.prompt ?? media.prompt_seed;
  const viewed = viewing ? media.generations.findIndex((shot) => shot.asset_id === viewing) : -1;
  const failed = lastFailure(media);

  function launch() {
    generate.mutate(
      {
        project,
        folder,
        name,
        body: {
          prompt: prompt.trim(),
          model: model.air,
          reference: imageMode && reference ? reference.key : "",
          strength: draft.strength,
          width: format.width,
          height: format.height,
          count,
          style: draft.style,
          confirm: true,
        },
      },
      {
        onSuccess: () => {
          setConfirming(false);
          notify({ kind: "success", title: t("Generation queued"), body: tn(count, "{n} image — {title}", "{n} images — {title}", { title: media.title }) });
        },
        onError: (error) => {
          setConfirming(false);
          setFailure(error instanceof ApiError && error.isPayment ? t("Spending refused") : t("Refused · {message}", { message: error.message }));
        },
      },
    );
  }

  return (
    <>
      <div className="card-gen">
        <div className="card-gen-form">
          <ModeSeg
            value={imageMode ? "image" : "text"}
            imageReady={references.length > 0}
            onChange={(mode) => patch({ mode })}
          />

          {imageMode && reference && (
            <>
              <Field group label={t("Reference")}>
                <div className="card-gen-refs" role="radiogroup" aria-label={t("Reference")}>
                  {references.map((entry) => (
                    <button
                      key={entry.key}
                      type="button"
                      role="radio"
                      className="card-gen-ref"
                      aria-checked={entry.key === reference.key}
                      onClick={() => patch({ source: entry.key })}
                    >
                      <span className="board check">
                        <img src={entry.url} alt="" />
                      </span>
                      <span className="cap">{entry.label}</span>
                    </button>
                  ))}
                </div>
              </Field>
              <Field group label={<>{t("Strength")} <span className="mono filled">{decimal(draft.strength)}</span></>}>
                <Slider
                  value={draft.strength}
                  min={STRENGTH.min}
                  max={STRENGTH.max}
                  step={STRENGTH.step}
                  onChange={(strength) => patch({ strength })}
                  bounds={false}
                />
                <div className="ends"><span>{t("faithful")}</span><span>{t("loose")}</span></div>
              </Field>
            </>
          )}

          <div className="field">
            <div className="card-gen-label">
              <label htmlFor={promptId}>{t("Prompt")}</label>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={draft.prompt === null || draft.prompt === media.prompt_seed}
                onClick={() => patch({ prompt: null })}
              >
                {t("Reset to the card")}
              </button>
            </div>
            <textarea
              id={promptId}
              rows={4}
              value={prompt}
              onChange={(event) => patch({ prompt: event.target.value })}
            />
          </div>

          <Field group label={t("Model")}>
            <div className="card-gen-models" role="radiogroup" aria-label={t("Model")}>
              {IMAGE_MODELS.map((entry) => (
                <Choice
                  key={entry.air}
                  checked={entry.air === model.air}
                  label={entry.label}
                  price={cost(entry.usd)}
                  disabled={!imageMode && NEEDS_REFERENCE.has(entry.air)}
                  onSelect={() => patch({ model: entry.air })}
                />
              ))}
            </div>
          </Field>

          <div className="card-gen-pair">
            <Field group label={t("Images")}>
              <Seg value={draft.count} options={COUNTS} onChange={(next) => patch({ count: next })} />
            </Field>
            <Field group label={<>{t("Format")} <span className="mono muted">{format.width} × {format.height}</span></>}>
              <Seg value={formatKey} options={FORMAT_OPTIONS} onChange={(next) => patch({ format: next })} />
            </Field>
          </div>

          <Setting label={t("Project style")}>
            <Toggle checked={draft.style} onChange={(style) => patch({ style })} label={t("Project style")} />
          </Setting>

          {failure && <Badge tone="danger">{failure}</Badge>}
          <button
            type="button"
            className="btn btn-primary btn-block"
            disabled={!prompt.trim() || generate.isPending}
            onClick={() => {
              setFailure(null);
              setConfirming(true);
            }}
          >
            {generate.isPending ? t("sending…") : tn(count, "Generate {n} image · {total}", "Generate {n} images · {total}", { total: cost(total) })}
          </button>
        </div>

        <div className="card-gen-results">
          <p className="eyebrow">
            {t("Images")} <span className="num">{media.generations.length}</span>
          </p>
          {failed && (
            <span className="card-gen-failure" title={tr(failed.error)}>
              <Badge tone="danger">
                {abbreviate(tr(failed.error), 96)}
                <span className="mono">{when(failed.at)}</span>
              </Badge>
            </span>
          )}
          {media.pending > 0 || media.generations.length > 0 ? (
            <div className="assets">
              {Array.from({ length: media.pending }, (_, index) => (
                <div className="asset" key={`pending-${index}`} aria-busy="true">
                  <span className="thumb plain"><span className="spin" aria-hidden="true" /></span>
                  <span className="meta"><b>{t("pending")}</b></span>
                </div>
              ))}
              {media.generations.map((shot) => (
                <button
                  key={shot.asset_id}
                  type="button"
                  className="asset"
                  title={shot.prompt}
                  onClick={() => setViewing(shot.asset_id)}
                >
                  <span className="thumb">
                    <img src={assetPreviewUrl(shot.asset_id, 320)} alt="" loading="lazy" />
                  </span>
                  <span className="meta">
                    <b>{shot.prompt || "—"}</b>
                    <span>{REFERENCE_LABELS[shot.reference] ?? "—"}</span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <Empty title={t("No image")} />
          )}
        </div>
      </div>

      {confirming && (
        <Dialog
          eyebrow={t("Paid generation")}
          title={tn(count, "{n} image of “{title}” for {total}?", "{n} images of “{title}” for {total}?",
            { title: media.title, total: cost(total) })}
          onClose={() => setConfirming(false)}
          foot={
            <>
              <Badge tone="warn">{t("paid")}</Badge>
              <span className="spacer" />
              <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
                {t("Back to the settings")}
              </button>
              <button type="button" className="btn btn-primary" disabled={generate.isPending} onClick={launch}>
                {generate.isPending ? t("sending…") : t("Pay {total} and start", { total: cost(total) })}
              </button>
            </>
          }
        >
          <Facts
            rows={[
              [`${model.label} × ${count}`, cost(total)],
              [t("Reference"), imageMode && reference ? reference.label : "—"],
              ...(imageMode ? [[t("Strength"), decimal(draft.strength)] as [string, string]] : []),
              [t("Format"), `${format.label} · ${format.width} × ${format.height}`],
              [t("Project style"), draft.style ? t("yes") : t("no")],
            ]}
          />
          <p className="hint">
            {t("Charged to your Runware account when it starts. If you cancel, nothing is billed.")}
          </p>
        </Dialog>
      )}

      {viewed >= 0 && (
        <Viewer
          shots={media.generations}
          at={viewed}
          card={media.title}
          onGo={(index) => setViewing(media.generations[index]?.asset_id ?? null)}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

/**
 * The mode, like a `Seg`, except that an option may be missing: without a
 * sketch or a render, there is no starting image.
 */
function ModeSeg({ value, imageReady, onChange }: {
  value: Mode;
  imageReady: boolean;
  onChange: (mode: Mode) => void;
}) {
  return (
    <div className="seg" role="group" aria-label={t("Mode")}>
      {MODES.map((option) => {
        const missing = option.value === "image" && !imageReady;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={option.value === value}
            disabled={missing}
            title={missing ? t("No sketch and no render") : undefined}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A generated image, large: the file itself, on the light table. The arrows
 * move to the neighbour. The viewed image is tracked by its id, so a
 * generation arriving at the top does not replace it.
 */
function Viewer({ shots, at, card, onGo, onClose }: {
  shots: CardGeneration[];
  at: number;
  card: string;
  onGo: (index: number) => void;
  onClose: () => void;
}) {
  const { notify } = useStudio();
  const shot = shots[at];
  const many = shots.length > 1;
  const previous = (at - 1 + shots.length) % shots.length;
  const next = (at + 1) % shots.length;

  useEffect(() => {
    if (!many) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest("input, textarea, [contenteditable]")) return;
      if (event.key === "ArrowLeft") onGo(previous);
      else if (event.key === "ArrowRight") onGo(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [many, previous, next, onGo]);

  async function copy(path: string) {
    try {
      await writeClipboard(path);
      notify({ kind: "success", title: t("Path copied"), body: path });
    } catch (failure) {
      notify({ kind: "error", title: t("Copy failed"), body: (failure as Error).message });
    }
  }

  if (!shot) return null;
  const path = shot.path;

  return (
    <Dialog
      wide
      eyebrow={card}
      title={abbreviate(shot.prompt) || "—"}
      onClose={onClose}
      foot={
        <>
          {many && (
            <>
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => onGo(previous)} aria-label={t("Previous image")}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
              </button>
              <span className="mono">{at + 1} / {shots.length}</span>
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => onGo(next)} aria-label={t("Next image")}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>
              </button>
            </>
          )}
          <span className="spacer" />
          {path && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copy(path)}>
              {t("Copy the path")}
            </button>
          )}
        </>
      }
    >
      <div className="board check card-gen-view">
        <img src={assetFileUrl(shot.asset_id)} alt={abbreviate(shot.prompt)} />
      </div>
      {shot.prompt && <p className="card-gen-prompt">{shot.prompt}</p>}
      <Facts
        rows={[
          [t("Model"), modelLabel(shot.model)],
          [t("Reference"), REFERENCE_LABELS[shot.reference] ?? "—"],
          [t("Size"), shot.width && shot.height ? `${shot.width} × ${shot.height}` : null],
          [t("Created"), shortDate(shot.created_at)],
        ]}
      />
    </Dialog>
  );
}
