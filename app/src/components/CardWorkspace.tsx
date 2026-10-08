/**
 * The workbench of a game design card: its gestures, its visuals, its text.
 *
 * An Interface, Mechanics, Art direction or VFX card is not a brief written
 * once: it is where work on that subject goes on. At the top, its gestures
 * (discuss it with an agent, edit it, those of its shelf); then its visuals,
 * the image above the text: the game as it is (the latest engine render named
 * after the card), the sketch of what is wanted, the references added to it,
 * and the images generated from one or the other; last the text, rendered for
 * reading. An image dropped on the visuals becomes a reference, except on the
 * sketch, which takes it for itself.
 *
 * The text is edited like any document (`useDocumentDraft`, in
 * `pages/Documents.tsx`); the media come from `service/cards.py` and re-read
 * themselves: a generation arrives, an agent redoes the render, and the
 * workbench follows without a reload.
 */

import { useState, type ReactNode } from "react";
import { assetFileUrl, documentImageUrl, type CardRender, type ProjectDocument } from "../api";
import { inTauri } from "../lib/host";
import { useReveal } from "../lib/paths";
import { useAddCardReferences, useCardMedia, useRerenderCard } from "../lib/queries";
import {
  ChatPlusGlyph, DeleteButton, DraftText, EditGestures, useDocumentDraft,
} from "../pages/Documents";
import CardGenerate from "./CardGenerate";
import CardReferences, { useImageDrop } from "./CardReferences";
import CardSketch from "./CardSketch";
import ScreenEditor, { SCREEN_FOLDERS } from "./ScreenEditor";
import Markdown, { type DocumentLinker } from "./Markdown";
import { Badge, Callout, Empty, Panel, Seg, State, shortDate } from "./ui";
import { t, tn } from "../lib/i18n";

type Visual = "render" | "edit" | "sketch" | "references" | "generate";

const VISUALS: { value: Visual; label: string }[] = [
  { value: "render", label: t("Current render") },
  // An Interface or Props card is a scene: it can be edited (`ScreenEditor`).
  { value: "edit", label: t("Edit") },
  { value: "sketch", label: t("Sketch") },
  { value: "references", label: t("References") },
  { value: "generate", label: t("Generate") },
];

/** The remembered tab, per viewer: a card reopens on what was being looked at. */
const VISUAL_KEY = "gs-card-visual";

function useVisual(): [Visual, (visual: Visual) => void] {
  const [visual, setVisual] = useState<Visual>(() => {
    try {
      const stored = localStorage.getItem(VISUAL_KEY);
      return VISUALS.find((entry) => entry.value === stored)?.value ?? "render";
    } catch {
      return "render";
    }
  });
  const choose = (next: Visual) => {
    setVisual(next);
    try {
      localStorage.setItem(VISUAL_KEY, next);
    } catch {
      /* A convenience: without storage, the workbench just goes back to the render. */
    }
  };
  return [visual, choose];
}

/** The file name of a cited image, without path, query or anchor. */
function fileName(src: string): string {
  const name = (src.split(/[?#]/)[0] ?? "").split("/").pop() ?? "";
  try {
    return decodeURIComponent(name).toLowerCase();
  } catch {
    return name.toLowerCase();
  }
}

export default function CardWorkspace({
  project, folder, name, editing = false, onDeleted, onDiscuss, linkDocument, actions,
}: {
  project: string;
  /** The section: `design/interface`, `design/mechanics`… */
  folder: string;
  name: string;
  /** A card just created opens in edit mode. */
  editing?: boolean;
  onDeleted: () => void;
  /** Opens the handoff: an agent discussion about this card. */
  onDiscuss: (document: ProjectDocument) => void;
  /** A link to a sibling card: the section opens it. */
  linkDocument?: DocumentLinker;
  /** The shelf's gestures ("Create in Godot" for a VFX). */
  actions?: (document: ProjectDocument, dirty: boolean) => ReactNode;
}) {
  const draft = useDocumentDraft(project, folder, name, { editing });
  const media = useCardMedia(project, folder, name);
  const [chosen, setVisual] = useVisual();
  const screen = SCREEN_FOLDERS.includes(folder);
  const visual: Visual = chosen === "edit" && !screen ? "render" : chosen;
  const addReferences = useAddCardReferences();
  const reveal = useReveal();
  // An image dropped on the render or the generation joins the references;
  // the references tab has its own drop area, the sketch keeps its drops.
  const panelDrop = useImageDrop((dropped) => {
    setVisual("references");
    addReferences.mutate({ project, folder, name, ...dropped });
  });
  const dropsHere = visual === "render" || visual === "generate";
  const { selected } = draft;

  if (!selected) {
    return (
      <Panel title={t("Card")}>
        <Empty title={t("Reading…")} />
      </Panel>
    );
  }

  // The card's render carries its name: its tab shows it, the text does not
  // repeat it.
  const renderFile = `${name}.png`.toLowerCase();
  // A failed re-read keeps what was read: the sketch being drawn does not
  // unmount because the server restarts.
  const ready = media.data ?? null;
  const failure = media.error ? (media.error as Error).message : null;
  const pending = ready?.pending ?? 0;

  return (
    <div className="card">
      <header className="card-head">
        <div className="card-titles">
          <h2>{selected.title}</h2>
          <p className="card-facts">
            <span className="mono">{selected.file}</span>
            <span className="num">{t("{words} words", { words: selected.words })}</span>
            {selected.modified_at && <span className="num">{shortDate(selected.modified_at)}</span>}
            {draft.dirty && <Badge tone="quiet">{t("not saved")}</Badge>}
          </p>
        </div>
        <div className="card-gestures">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            title={t("Discuss this card")}
            onClick={() => onDiscuss(selected)}
          >
            <ChatPlusGlyph />
            {t("Discuss")}
          </button>
          {actions?.(selected, draft.dirty)}
          <EditGestures draft={draft} />
          <span className="vsep" aria-hidden="true" />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={!inTauri()}
            onClick={() => void reveal(selected.path)}
          >
            {t("Show on disk")}
          </button>
          <DeleteButton project={project} folder={folder} name={selected.name}
                        title={selected.title} onDeleted={onDeleted} />
        </div>
        {draft.error && <Badge tone="danger">{draft.error}</Badge>}
      </header>

      <section
        className={`panel card-visuals ${dropsHere && panelDrop.dragging ? "is-dragging" : ""}`}
        {...(dropsHere ? panelDrop.bind : {})}
      >
        <div className="panel-head">
          <Seg<Visual>
            value={visual}
            onChange={setVisual}
            options={VISUALS.filter((entry) => screen || entry.value !== "edit").map((entry) => entry.value === "references" && ready?.references.length
              ? { ...entry, label: t("References · {length}", { length: ready.references.length }) }
              : entry)}
          />
          {pending > 0 && visual !== "generate" && (
            <State state="running" label={tn(pending, "{n} image in progress", "{n} images in progress")} />
          )}
          {ready && failure && <Badge tone="danger">{failure}</Badge>}
          {addReferences.isError && visual !== "references" && (
            <Badge tone="danger">{(addReferences.error as Error).message}</Badge>
          )}
        </div>
        <div className={`card-visual ${ready ? `is-${visual}` : ""}`}>
          {!ready ? (
            failure
              ? <Callout>{t("The card visuals cannot be read: {failure}", { failure })}</Callout>
              : <Empty title={t("Reading…")} />
          ) : visual === "render" ? (
            <CurrentRender project={project} folder={folder} name={name} render={ready.render} />
          ) : visual === "edit" ? (
            <ScreenEditor project={project} folder={folder} name={name} render={ready.render} />
          ) : visual === "sketch" ? (
            <CardSketch project={project} folder={folder} name={name} render={ready.render} />
          ) : visual === "references" ? (
            <CardReferences project={project} folder={folder} name={name}
                             references={ready.references} />
          ) : (
            <CardGenerate project={project} folder={folder} name={name} media={ready} />
          )}
        </div>
      </section>

      <Panel className="card-text" bodyClass="card-text-body">
        <DraftText
          draft={draft}
          reading={(text) => (
            <Markdown
              text={text}
              titled
              lead
              resolveImage={(src) => documentImageUrl(project, folder, src)}
              hideImage={(src) => fileName(src) === renderFile}
              linkDocument={linkDocument}
            />
          )}
        />
      </Panel>
    </div>
  );
}

/**
 * The current render: the game screen as its engine draws it, whole and at its
 * aspect ratio. "Render again" redoes the same scene offscreen: it is free and
 * takes a few seconds.
 */
function CurrentRender({ project, folder, name, render }: {
  project: string;
  folder: string;
  name: string;
  render: CardRender | null;
}) {
  const rerender = useRerenderCard();
  const [broken, setBroken] = useState<string | null>(null);

  if (!render) return <Empty title={t("No render")} />;

  // The same path may hold a newer render: its date busts the cache.
  const file = assetFileUrl(render.asset_id);
  const src = `${file}${file.includes("?") ? "&" : "?"}v=${encodeURIComponent(render.rendered_at)}`;
  const size = render.width && render.height ? `${render.width} × ${render.height}` : null;

  return (
    <div className="card-render">
      {broken === src ? (
        <Empty title={t("Unreadable render")} />
      ) : (
        <div className="card-render-stage board">
          <img src={src} alt={render.scene} onError={() => setBroken(src)} />
        </div>
      )}
      <div className="card-render-meta">
        <span className="mono" title={render.file}>{render.scene}</span>
        <span className="num">{shortDate(render.rendered_at)}</span>
        {size && <span className="num">{size}</span>}
        <span className="spacer" />
        {rerender.isError && <Badge tone="danger">{(rerender.error as Error).message}</Badge>}
        {render.rerender && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={rerender.isPending}
            onClick={() => rerender.mutate({ project, folder, name })}
          >
            {rerender.isPending ? t("Rendering…") : t("Render again")}
          </button>
        )}
      </div>
    </div>
  );
}
