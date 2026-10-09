/**
 * The graphic style or the game type, in the Universe: its parts, its board of influences, and a thread with an agent.
 *
 * Each part is a card of the art direction (the game type's gameplay style,
 * setting and lore; the graphic style's look), shown in its own tab and
 * written in place. The influences are a mood board: each one with its images
 * large, what is kept from it and what is left. The agent is Claude Code,
 * driven by the studio (`service/direction_chat.py`): it reads the game,
 * writes the cards and fills the board. It never pays: each image proposal
 * shows in the thread with its amount, and only the user's click pays it
 * (`influences.pay`); once generated, its images join their influence.
 */

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, assetFileUrl, assetPreviewUrl, documentImageUrl, type ChatMessage, type ChatStep,
  type DirectionAspect, type DirectionBoard, type DirectionImage, type DirectionPart, type DirectionThread,
  type Influence, type InfluenceProposal,
} from "../api";
import { IMAGE_ACCEPT, IMAGE_LIMITS, useImageDrop, type Dropped } from "./CardReferences";
import Markdown from "./Markdown";
import { Badge, Dialog, Seg, Select, cost } from "./ui";
import { PencilGlyph } from "../pages/Documents";
import { IMAGE_MODELS, imageCost } from "../lib/catalog";
import { lang, t, tn, tr } from "../lib/i18n";
import { useDirectionBoard, useDirectionThread } from "../lib/queries";
import { useStudio } from "../lib/store";

const KINDS: Record<string, string> = {
  work: t("Work"),
  game: t("Game"),
  film: t("Film"),
  book: t("Book"),
  artist: t("Artist"),
  movement: t("Movement"),
  place: t("Place"),
  blend: t("Blend"),
  other: t("Other"),
};

const PARTS: Record<string, string> = {
  look: t("The style"),
  gameplay: t("Gameplay style"),
  setting: t("Setting"),
  lore: t("Lore"),
};

/** What one can ask an empty thread, in one click. */
const STARTERS: Record<DirectionAspect, string[]> = {
  style: [t("What graphic style does the game show today?")],
  game: [t("Sum up the game type as it is today"), t("Write the lore from the game's docs")],
};

/** The models a thread can run on, as Claude Code names them; the first by default. */
const MODELS = [
  { value: "sonnet", label: "Sonnet" },
  { value: "opus", label: "Opus" },
  { value: "haiku", label: "Haiku" },
];
const MODEL_KEY = "gs-direction-model";
const DEFAULT_MODEL = "sonnet";

const INFLUENCES = "influences";
const GALLERY = "gallery";

/** The aspect as a whole, as a proposal or an image names it when no influence does. */
const ASPECTS: Record<DirectionAspect, string> = {
  style: t("Graphic style"),
  game: t("Game type"),
};


const KONTEXT = "runware:106@1";
// Exploring: the cheapest image model, as the server proposes by default.
const SCHNELL = "runware:100@1";
// The images an influence shows at once; the viewer shows them all.
const MOSAIC = 4;

/** What a step of the agent's work says, in one line. */
function stepLabel(step: ChatStep): string {
  const detail = step.detail ?? "";
  switch (step.tool) {
    case "Read":
      return t("Reads {detail}", { detail });
    case "Glob":
    case "Grep":
      return t("Searches {detail}", { detail });
    case "influence_board":
      return t("Reads the board");
    case "influence_set":
      return t("Names the influence {detail}", { detail });
    case "influence_propose":
      return t("Proposes images for {detail}", { detail });
    case "list_documents":
    case "document_templates":
      return t("Lists the art direction's cards");
    case "read_document":
      return t("Reads the card {detail}", { detail });
    case "write_document":
      return t("Writes the card {detail}", { detail });
    case "view_asset":
      return t("Looks at an image");
    case "lookdev":
      return t("Reads the game's colors, type and shaders");
    case "render_scene":
      return t("Renders a scene of the game");
    default:
      return step.tool ?? "";
  }
}

function imageUrl(project: string, board: DirectionBoard, image: DirectionImage, size?: number): string {
  if (image.kind === "generated" && image.asset_id) {
    return size ? assetPreviewUrl(image.asset_id, size) : assetFileUrl(image.asset_id);
  }
  return documentImageUrl(project, board.card.folder, `${board.card.name}.references/${image.file ?? ""}`);
}

/** The tab shown in an aspect, remembered for this visitor. */
function useTab(aspect: DirectionAspect, tabs: string[]): [string, (tab: string) => void] {
  const key = `gs-direction-${aspect}`;
  const [chosen, setChosen] = useState<string | null>(() => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  });
  const choose = (tab: string) => {
    setChosen(tab);
    try {
      localStorage.setItem(key, tab);
    } catch {
      /* A comfort: without storage, the aspect opens on its first tab. */
    }
  };
  return [chosen && tabs.includes(chosen) ? chosen : tabs[0] ?? "", choose];
}

/** What the viewer shows: a list of images, the one in front, and the influence they belong to. */
interface Viewing {
  images: DirectionImage[];
  at: number;
  influence?: string;
}

/** Take an image off an influence; the board comes back without it. */
function useRemoveImage(project: string, aspect: DirectionAspect) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  return async (influence: string, image: DirectionImage): Promise<boolean> => {
    try {
      queryClient.setQueryData(["direction", project, aspect],
        await api.removeInfluenceImage(project, aspect, influence, image.key));
      return true;
    } catch (error) {
      notify({ kind: "error", title: t("Removal refused"), body: tr((error as Error).message) });
      return false;
    }
  };
}

export default function DirectionRoom({ project, aspect }: { project: string; aspect: DirectionAspect }) {
  const queryClient = useQueryClient();
  const thread = useDirectionThread(project, aspect);
  const running = thread.data?.running ?? false;
  const board = useDirectionBoard(project, aspect, running);
  const [viewing, setViewing] = useState<Viewing | null>(null);
  // The influences a generation draws on: null when the dialog is closed.
  const [generating, setGenerating] = useState<string[] | null>(null);
  // What the user is writing to the agent: the page can suggest it, never send it.
  const [draft, setDraft] = useState("");
  const composer = useRef<HTMLTextAreaElement>(null);
  const removeImage = useRemoveImage(project, aspect);
  const tabs = [...(board.data?.parts ?? []).map((part) => part.id), INFLUENCES, GALLERY];
  const [tab, setTab] = useTab(aspect, tabs);

  // A turn that ends may have changed the cards and the board: they are read again.
  const was = useRef(running);
  useEffect(() => {
    if (was.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      void queryClient.invalidateQueries({ queryKey: ["lookdev", project] });
    }
    was.current = running;
  }, [running, project, aspect, queryClient]);

  if (board.error) return <p className="direction-failure">{tr((board.error as Error).message)}</p>;
  if (!board.data) return null;
  const data = board.data;
  const part = data.parts.find((entry) => entry.id === tab);
  const open = (images: DirectionImage[], at: number, influence?: string) =>
    setViewing({ images, at, influence });
  // The image in front leaves the influence; the viewer moves on to the next one.
  const removeShown = async () => {
    if (!viewing?.influence) return;
    const image = viewing.images[viewing.at];
    if (!image || !(await removeImage(viewing.influence, image))) return;
    const images = viewing.images.filter((entry) => entry.key !== image.key);
    setViewing(images.length ? { ...viewing, images, at: Math.min(viewing.at, images.length - 1) } : null);
  };
  // A request suggested by the page goes into the composer, never straight to the
  // agent: the user reworks it, then sends it.
  const prefill = (message: string) => {
    setDraft(message);
    requestAnimationFrame(() => {
      const element = composer.current;
      if (!element) return;
      element.focus();
      element.setSelectionRange(element.value.length, element.value.length);
    });
  };

  return (
    <div className="direction">
      <div className="direction-main">
        <nav className="direction-tabs" aria-label={t("Sections")}>
          <Seg<string>
            value={tab}
            onChange={setTab}
            options={[
              ...data.parts.map((entry) => ({
                value: entry.id,
                label: <>{PARTS[entry.id] ?? entry.title}{entry.written && <span className="direction-written" aria-hidden="true" />}</>,
              })),
              { value: INFLUENCES, label: <>{t("Influences")} <span className="num">{data.influences.length}</span></> },
              { value: GALLERY, label: <>{t("Images")} <span className="num">{data.gallery.length}</span></> },
            ]}
          />
        </nav>
        {part ? (
          <PartCard key={part.id} project={project} aspect={aspect} part={part} onAsk={prefill} />
        ) : tab === GALLERY ? (
          <Gallery project={project} aspect={aspect} board={data} onOpen={open}
                   onGenerate={() => setGenerating([])} />
        ) : (
          <Influences project={project} aspect={aspect} board={data} onOpen={open}
                      onGenerate={(influence) => setGenerating([influence])} />
        )}
      </div>
      <Thread project={project} aspect={aspect} board={data} thread={thread.data}
              text={draft} onText={setDraft} composer={composer} onPrefill={prefill}
              onOpen={(image) => open([image], 0)} />
      {generating && (
        <Generate project={project} aspect={aspect} board={data} chosen={generating}
                  onClose={() => setGenerating(null)} />
      )}
      {viewing && (
        <Viewer project={project} board={data} viewing={viewing} onMove={(at) => setViewing({ ...viewing, at })}
                onRemove={viewing.influence ? () => void removeShown() : undefined}
                onClose={() => setViewing(null)} />
      )}
    </div>
  );
}

/* --------------------------------------------------------------- a part */

/** A part's card: read, or written in place; one never written is written by hand or asked of the agent. */
function PartCard({ project, aspect, part, onAsk }: {
  project: string;
  aspect: DirectionAspect;
  part: DirectionPart;
  /** Puts a request for this part into the composer, to be reworked then sent. */
  onAsk: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [erasing, setErasing] = useState(false);

  const save = async () => {
    if (draft === null) return;
    setSaving(true);
    try {
      await api.saveDocument(project, part.name, draft, part.folder);
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      void queryClient.invalidateQueries({ queryKey: ["lookdev", project] });
    } catch (error) {
      notify({ kind: "error", title: t("Card not saved"), body: tr((error as Error).message) });
    } finally {
      setSaving(false);
    }
  };
  const label = PARTS[part.id] ?? part.title;
  const edit = () => setDraft(part.written ? part.text : `# ${label}\n\n`);
  const erase = async () => {
    try {
      await api.deleteDocument(project, part.name, part.folder);
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      void queryClient.invalidateQueries({ queryKey: ["lookdev", project] });
    } catch (error) {
      notify({ kind: "error", title: t("Card not erased"), body: tr((error as Error).message) });
    }
    setErasing(false);
  };

  return (
    <section className="direction-part">
      {draft !== null ? (
        <form className="direction-editor" onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}>
          <textarea
            autoFocus
            value={draft}
            aria-label={PARTS[part.id] ?? part.title}
            rows={Math.min(32, Math.max(12, draft.split("\n").length + 2))}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setDraft(null);
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                void save();
              }
            }}
          />
          <div className="direction-editor-actions">
            <span className="mono direction-editor-file">{`${part.folder}/${part.name}.md`}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(null)}>{t("Cancel")}</button>
            <button type="submit" className="btn btn-secondary btn-sm" disabled={saving || !draft.trim()}>
              {saving ? t("Saving…") : t("Save")}
            </button>
          </div>
        </form>
      ) : (
        part.written ? (
          <div className="direction-text">
            <div className="direction-text-actions">
              <button type="button" className="btn btn-ghost btn-icon" onClick={edit}
                      aria-label={t("Edit {title}", { title: label })} title={t("Edit")}>
                <PencilGlyph />
              </button>
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => setErasing(true)}
                      aria-label={t("Erase {title}", { title: label })} title={t("Erase the card")}>
                <TrashGlyph />
              </button>
            </div>
            <Markdown text={part.text} titled />
            {erasing && (
              <Dialog title={t("Erase this card?")} eyebrow={label} onClose={() => setErasing(false)}
                      foot={(
                        <>
                          <span className="grow" />
                          <button type="button" className="btn btn-ghost" onClick={() => setErasing(false)}>
                            {t("Cancel")}
                          </button>
                          <button type="button" className="btn btn-danger" onClick={() => void erase()}>
                            {t("Erase")}
                          </button>
                        </>
                      )}>
                <p className="mono">{part.path}</p>
              </Dialog>
            )}
          </div>
        ) : (
          <div className="direction-blank">
            <h3>{label}</h3>
            <div>
              <button type="button" className="btn btn-secondary" onClick={edit}>
                <PencilGlyph />
                {t("Write")}
              </button>
              <button type="button" className="btn btn-secondary"
                      onClick={() => onAsk(t("Write the card “{title}” ({name}) from what the game and its docs show",
                        { title: label, name: part.name }))}>
                <AgentGlyph />
                {t("Ask the agent")}
              </button>
            </div>
          </div>
        )
      )}
    </section>
  );
}

/* ------------------------------------------------------------- the board */

function Influences({ project, aspect, board, onOpen, onGenerate }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  onOpen: (images: DirectionImage[], at: number, influence?: string) => void;
  onGenerate: (influence: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const names = Object.fromEntries(board.influences.map((entry) => [entry.id, entry.name]));
  const pending = (id: string) => board.proposals
    .filter((entry) => entry.influence === id && entry.status === "queued")
    .reduce((total, entry) => total + entry.count, 0);
  return (
    <section className="direction-influences" aria-label={t("Influences")}>
      {adding ? (
        <AddInfluence project={project} aspect={aspect} onDone={() => setAdding(false)} />
      ) : (
        <button type="button" className="btn btn-secondary btn-sm direction-add" onClick={() => setAdding(true)}>
          <PlusGlyph />
          {t("Add an influence")}
        </button>
      )}
      {board.influences.length > 0 && (
        <ul>
          {board.influences.map((entry) => (
            <InfluenceCard key={entry.id} project={project} aspect={aspect} board={board} influence={entry}
                           names={names} pending={pending(entry.id)} onOpen={onOpen}
                           onGenerate={() => onGenerate(entry.id)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function InfluenceCard({ project, aspect, board, influence, names, pending, onOpen, onGenerate }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  influence: Influence;
  names: Record<string, string>;
  pending: number;
  onOpen: (images: DirectionImage[], at: number, influence?: string) => void;
  /** Images generated from this influence's own images. */
  onGenerate: () => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const removeImage = useRemoveImage(project, aspect);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const drop = async ({ files, paths }: Dropped) => {
    setBusy(true);
    try {
      let last: DirectionBoard | null = null;
      for (const file of files) last = await api.addInfluenceImage(project, aspect, influence.id, file);
      for (const path of paths) last = await api.addInfluenceImagePath(project, aspect, influence.id, path);
      if (last) queryClient.setQueryData(["direction", project, aspect], last);
    } catch (error) {
      notify({ kind: "error", title: t("Image refused"), body: tr((error as Error).message) });
    } finally {
      setBusy(false);
    }
  };
  const { dragging, bind } = useImageDrop((dropped) => void drop(dropped));

  const remove = async () => {
    try {
      queryClient.setQueryData(["direction", project, aspect],
        await api.removeInfluence(project, aspect, influence.id));
      void queryClient.invalidateQueries({ queryKey: ["lookdev", project] });
    } catch (error) {
      notify({ kind: "error", title: t("Removal refused"), body: tr((error as Error).message) });
    }
    setRemoving(false);
  };

  const shown = influence.images.slice(0, MOSAIC);
  const slots = Math.min(pending, Math.max(0, MOSAIC - shown.length));
  const more = influence.images.length - shown.length;
  const tiles = shown.length + slots;

  return (
    <li className={`direction-influence ${dragging ? "is-dragging" : ""}`} {...bind}>
      {tiles > 0 ? (
        <div className={`direction-mosaic is-${tiles}`}>
          {shown.map((image, rank) => (
            <div key={image.key} className="direction-tile">
              <button type="button" onClick={() => onOpen(influence.images, rank, influence.id)}
                      title={image.prompt || image.file}>
                <img src={imageUrl(project, board, image, rank === 0 ? 640 : 360)} alt="" loading="lazy" />
                {rank === shown.length - 1 && more > 0 && <span className="direction-more num">+{more}</span>}
              </button>
              <button type="button" className="btn btn-secondary btn-icon direction-tile-remove"
                      aria-label={t("Remove the image")} title={t("Remove the image")}
                      onClick={() => void removeImage(influence.id, image)}>
                <TrashGlyph />
              </button>
            </div>
          ))}
          {Array.from({ length: slots }, (_, rank) => (
            <span key={`pending-${rank}`} className="direction-pending" aria-label={t("Generating…")} />
          ))}
        </div>
      ) : (
        <button type="button" className="direction-drop" disabled={busy} onClick={() => picker.current?.click()}
                aria-label={t("Add images")} title={IMAGE_LIMITS}>
          {dragging ? IMAGE_LIMITS : <ImageGlyph />}
        </button>
      )}
      {editing ? (
        <EditInfluence project={project} aspect={aspect} influence={influence} onDone={() => setEditing(false)} />
      ) : (
        <div className="direction-influence-body">
          <header>
            <div>
              <h4>{influence.name}</h4>
              <span className="direction-kind">
                {KINDS[influence.kind] ?? influence.kind}
                {influence.of.length > 0 && ` · ${influence.of.map((id) => names[id] ?? id).join(" × ")}`}
              </span>
            </div>
            {influence.images.length > 0 && (
              <button type="button" className="btn btn-ghost btn-icon"
                      aria-label={t("Generate from {title}", { title: influence.name })} title={t("Generate images")}
                      onClick={onGenerate}>
                <SparkGlyph />
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-icon"
                    aria-label={t("Edit {title}", { title: influence.name })} title={t("Edit")}
                    onClick={() => setEditing(true)}>
              <PencilGlyph />
            </button>
            <button type="button" className="btn btn-ghost btn-icon" disabled={busy}
                    aria-label={t("Add images")} title={t("Add images")} onClick={() => picker.current?.click()}>
              <ImageGlyph />
            </button>
            <button type="button" className="btn btn-ghost btn-icon"
                    aria-label={t("Remove {title}", { title: influence.name })} title={t("Remove")}
                    onClick={() => setRemoving(true)}>
              <TrashGlyph />
            </button>
          </header>
          {(influence.keep || influence.avoid) && (
            <button type="button" className={`direction-keep ${open ? "is-open" : ""}`} aria-expanded={open}
                    onClick={() => setOpen(!open)}>
              {influence.keep && <span><b>{t("Keep")}</b> {influence.keep}</span>}
              {influence.avoid && <span><b>{t("Leave")}</b> {influence.avoid}</span>}
            </button>
          )}
        </div>
      )}
      <input ref={picker} type="file" accept={IMAGE_ACCEPT} multiple hidden onChange={(event) => {
        const files = Array.from(event.target.files ?? []);
        event.target.value = "";
        if (files.length) void drop({ files, paths: [] });
      }} />
      {removing && (
        <Dialog title={t("Remove this influence?")} eyebrow={influence.name} onClose={() => setRemoving(false)}
                foot={(
                  <>
                    <span className="grow" />
                    <button type="button" className="btn btn-ghost" onClick={() => setRemoving(false)}>{t("Cancel")}</button>
                    <button type="button" className="btn btn-danger" onClick={() => void remove()}>{t("Remove")}</button>
                  </>
                )}>
          <p>{t("Its images stay with the card.")}</p>
        </Dialog>
      )}
    </li>
  );
}

/** Every image generated for the aspect, the newest first, with what asked for it. */
function Gallery({ project, aspect, board, onOpen, onGenerate }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  onOpen: (images: DirectionImage[], at: number) => void;
  onGenerate: () => void;
}) {
  const names = Object.fromEntries(board.influences.map((entry) => [entry.id, entry.name]));
  const pending = board.proposals.filter((entry) => entry.status === "queued")
    .reduce((total, entry) => total + entry.count, 0);
  if (board.gallery.length + pending === 0) {
    return (
      <div className="direction-blank">
        <h3>{t("Images")}</h3>
        <div>
          <button type="button" className="btn btn-secondary" onClick={onGenerate}>
            <SparkGlyph />
            {t("Generate images")}
          </button>
        </div>
      </div>
    );
  }
  return (
    <section className="direction-influences">
      <button type="button" className="btn btn-secondary btn-sm direction-add" onClick={onGenerate}>
        <SparkGlyph />
        {t("Generate images")}
      </button>
      <ul className="direction-gallery">
        {Array.from({ length: pending }, (_, rank) => (
          <li key={`pending-${rank}`}><span className="direction-pending" aria-label={t("Generating…")} /></li>
        ))}
        {board.gallery.map((image, rank) => (
          <li key={image.key}>
            <button type="button" onClick={() => onOpen(board.gallery, rank)} title={image.prompt}>
              <img src={imageUrl(project, board, image, 480)} alt="" loading="lazy" />
            </button>
            <span className="direction-gallery-for">
              {image.influence ? names[image.influence] ?? image.influence : ASPECTS[aspect]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Images asked for: what the user wants to see, and the influences whose
 * images a model looks at to write the prompt. The proposal comes back with
 * what the model saw and its price; the user adjusts it, pays it, or sets it
 * aside.
 */
function Generate({ project, aspect, board, chosen, onClose }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  /** The influences ticked on opening; empty: every one with images. */
  chosen: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const usable = board.influences.filter((entry) => entry.images.length > 0);
  const [request, setRequest] = useState("");
  const [sources, setSources] = useState<string[]>(() =>
    chosen.length ? chosen.filter((id) => usable.some((entry) => entry.id === id)) : usable.map((entry) => entry.id));
  const [model, setModel] = useState(SCHNELL);
  const [count, setCount] = useState("4");
  const [drafting, setDrafting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [proposal, setProposal] = useState<InfluenceProposal | null>(null);

  const draft = async () => {
    setDrafting(true);
    setFailure(null);
    try {
      const made = await api.draftImages(project, aspect, {
        request, influences: sources, count: Number(count), model, lang,
      });
      setProposal(made);
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
    } catch (error) {
      setFailure(tr((error as Error).message));
    } finally {
      setDrafting(false);
    }
  };
  const toggle = (id: string) =>
    setSources((held) => (held.includes(id) ? held.filter((item) => item !== id) : [...held, id]));

  return (
    <Dialog title={t("Generate images")} eyebrow={ASPECTS[aspect]} wide onClose={onClose}
            foot={proposal ? undefined : (
              <>
                <span className="hint grow">{failure && <Badge tone="danger">{failure}</Badge>}</span>
                <button type="button" className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
                <button type="button" className="btn btn-secondary" disabled={drafting || sources.length === 0}
                        onClick={() => void draft()}>
                  {drafting ? t("Looking at the images…") : t("Write the prompt")}
                </button>
              </>
            )}>
      {proposal ? (
        <Proposal project={project} aspect={aspect} board={board} proposal={proposal} influence={
          board.influences.find((entry) => entry.id === proposal.influence)?.name ?? ""}
                  onOpen={() => undefined} onAnswered={onClose} open />
      ) : (
        <div className="direction-generate">
          <textarea value={request} rows={3} maxLength={1000} aria-label={t("What you want to see")}
                    placeholder={t("What you want to see")} onChange={(event) => setRequest(event.target.value)} />
          {usable.length > 0 ? (
            <ul className="direction-sources" aria-label={t("Influences")}>
              {usable.map((entry) => (
                <li key={entry.id}>
                  <button type="button" aria-pressed={sources.includes(entry.id)} onClick={() => toggle(entry.id)}>
                    {entry.images[0] && <img src={imageUrl(project, board, entry.images[0], 240)} alt="" />}
                    <span>{entry.name}</span>
                    <span className="num">{entry.images.length}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="direction-failure">{t("No influence has images yet")}</p>
          )}
          <div className="direction-proposal-settings">
            <Select value={model} label={t("Model")} onChange={setModel}
                    options={IMAGE_MODELS.filter((entry) => entry.air !== KONTEXT)
                      .map((entry) => ({ value: entry.air, label: entry.label }))} />
            <Seg<string> value={count} onChange={setCount}
                         options={["1", "2", "3", "4"].map((value) => ({ value, label: value }))} />
          </div>
        </div>
      )}
    </Dialog>
  );
}

/** An influence changed in place: its name, its kind, what is kept from it and what is left. */
function EditInfluence({ project, aspect, influence, onDone }: {
  project: string;
  aspect: DirectionAspect;
  influence: Influence;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const [name, setName] = useState(influence.name);
  const [kind, setKind] = useState(influence.kind);
  const [keep, setKeep] = useState(influence.keep);
  const [avoid, setAvoid] = useState(influence.avoid);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await api.setInfluence(project, aspect, { influence: influence.id, name: name.trim(), kind, keep, avoid });
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      onDone();
    } catch (error) {
      notify({ kind: "error", title: t("Influence refused"), body: tr((error as Error).message) });
      setSaving(false);
    }
  };
  return (
    <form className="direction-influence-edit" onSubmit={(event) => {
      event.preventDefault();
      void save();
    }} onKeyDown={(event) => event.key === "Escape" && onDone()}>
      <input autoFocus value={name} maxLength={60} aria-label={t("Name")}
             onChange={(event) => setName(event.target.value)} />
      <Select value={kind} label={t("Kind")} onChange={setKind}
              options={Object.entries(KINDS).filter(([id]) => id !== "blend" || influence.kind === "blend")
                .map(([id, label]) => ({ value: id, label }))} />
      <label>
        <span>{t("Keep")}</span>
        <textarea value={keep} rows={3} maxLength={600} onChange={(event) => setKeep(event.target.value)} />
      </label>
      <label>
        <span>{t("Leave")}</span>
        <textarea value={avoid} rows={3} maxLength={600} onChange={(event) => setAvoid(event.target.value)} />
      </label>
      <div className="direction-add-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>{t("Cancel")}</button>
        <button type="submit" className="btn btn-secondary btn-sm" disabled={saving || !name.trim()}>
          {saving ? t("Saving…") : t("Save")}
        </button>
      </div>
    </form>
  );
}

function AddInfluence({ project, aspect, onDone }: {
  project: string;
  aspect: DirectionAspect;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("work");
  const add = async () => {
    if (!name.trim()) return;
    try {
      await api.setInfluence(project, aspect, { name: name.trim(), kind });
      onDone();
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      void queryClient.invalidateQueries({ queryKey: ["lookdev", project] });
    } catch (error) {
      notify({ kind: "error", title: t("Influence refused"), body: tr((error as Error).message) });
    }
  };
  return (
    <form className="direction-add-form" onSubmit={(event) => {
      event.preventDefault();
      void add();
    }}>
      <input autoFocus value={name} maxLength={60} placeholder={t("A work, an artist, a movement…")}
             aria-label={t("Name")} onChange={(event) => setName(event.target.value)}
             onKeyDown={(event) => event.key === "Escape" && onDone()} />
      <Select value={kind} label={t("Kind")} onChange={setKind}
              options={Object.entries(KINDS).filter(([id]) => id !== "blend")
                .map(([id, label]) => ({ value: id, label }))} />
      <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>{t("Cancel")}</button>
      <button type="submit" className="btn btn-secondary btn-sm" disabled={!name.trim()}>{t("Add")}</button>
    </form>
  );
}

/** The images of an influence, one at a time, large; arrows move through them. */
function Viewer({ project, board, viewing, onMove, onRemove, onClose }: {
  project: string;
  board: DirectionBoard;
  viewing: Viewing;
  onMove: (at: number) => void;
  /** Takes the image in front off its influence; absent outside an influence. */
  onRemove?: () => void;
  onClose: () => void;
}) {
  const { images, at } = viewing;
  const image = images[at];
  const many = images.length > 1;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight" && many) onMove((at + 1) % images.length);
      if (event.key === "ArrowLeft" && many) onMove((at - 1 + images.length) % images.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [at, images.length, many, onMove]);
  if (!image) return null;
  return (
    <Dialog title={image.kind === "generated" ? t("Generated image") : t("Reference")}
            eyebrow={many ? `${at + 1} / ${images.length}` : undefined} wide onClose={onClose}
            foot={onRemove && (
              <>
                <span className="grow" />
                <button type="button" className="btn btn-danger btn-sm" onClick={onRemove}>
                  {t("Remove from the influence")}
                </button>
              </>
            )}>
      <div className="direction-view">
        <img src={imageUrl(project, board, image)} alt="" />
        {many && (
          <>
            <button type="button" className="btn btn-secondary btn-icon direction-prev" aria-label={t("Previous")}
                    onClick={() => onMove((at - 1 + images.length) % images.length)}>
              <ArrowGlyph back />
            </button>
            <button type="button" className="btn btn-secondary btn-icon direction-next" aria-label={t("Next")}
                    onClick={() => onMove((at + 1) % images.length)}>
              <ArrowGlyph />
            </button>
          </>
        )}
      </div>
      {image.prompt && <p className="mono direction-view-prompt">{image.prompt}</p>}
    </Dialog>
  );
}

/* ------------------------------------------------------------ the thread */

type Item = { at: string; message?: ChatMessage; proposal?: InfluenceProposal };

function Thread({ project, aspect, board, thread, text, onText, composer, onPrefill, onOpen }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  thread: DirectionThread | undefined;
  /** The composer's text, held by the room so the page can suggest it. */
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onPrefill: (text: string) => void;
  onOpen: (image: DirectionImage) => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const [sending, setSending] = useState(false);
  const [model, setModel] = useState(() => {
    try {
      return localStorage.getItem(MODEL_KEY) ?? DEFAULT_MODEL;
    } catch {
      return DEFAULT_MODEL;
    }
  });
  const chooseModel = (next: string) => {
    setModel(next);
    try {
      localStorage.setItem(MODEL_KEY, next);
    } catch {
      /* A comfort: without storage, the thread starts on the default model. */
    }
  };
  const list = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const running = thread?.running ?? false;
  const names = Object.fromEntries(board.influences.map((entry) => [entry.id, entry.name]));

  // Messages and proposals, in the order they came.
  const items = useMemo<Item[]>(() => ([
    ...(thread?.messages ?? []).map((message) => ({ at: message.at, message })),
    ...board.proposals.map((proposal) => ({ at: proposal.created_at, proposal })),
  ] as Item[]).sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : Number(Boolean(a.proposal)) - Number(Boolean(b.proposal)))),
  [thread, board.proposals]);
  const last = thread?.messages[thread.messages.length - 1];

  // The thread follows the answer as it grows, unless the user scrolled up to reread.
  useEffect(() => {
    const element = list.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [items, last?.text, last?.steps?.length]);

  const store = (next: DirectionThread) => queryClient.setQueryData(["directionThread", project, aspect], next);
  const act = async (run: () => Promise<DirectionThread>, title: string) => {
    try {
      store(await run());
    } catch (error) {
      notify({ kind: "error", title, body: tr((error as Error).message) });
    }
  };
  const send = async (wanted = text) => {
    const message = wanted.trim();
    if (!message || running || sending) return;
    setSending(true);
    stick.current = true;
    try {
      store(await api.directionSend(project, aspect, message, lang, model));
      onText("");
    } catch (error) {
      notify({ kind: "error", title: t("Message not sent"), body: tr((error as Error).message) });
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="direction-thread" aria-label={t("Discussion")}>
      <header>
        <h3>{t("Discussion")}</h3>
        <span className="direction-agent">Claude Code</span>
        <Select value={model} label={t("Model")} onChange={chooseModel} className="direction-model"
                options={MODELS} />
        {!running && (thread?.messages.length ?? 0) > 0 && (
          <button type="button" className="btn btn-ghost btn-icon" aria-label={t("New discussion")}
                  title={t("New discussion")}
                  onClick={() => void act(() => api.directionReset(project, aspect), t("New discussion refused"))}>
            <NewGlyph />
          </button>
        )}
      </header>
      <div className="direction-messages" ref={list} onScroll={(event) => {
        const element = event.currentTarget;
        stick.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
      }}>
        {items.length === 0 ? (
          <div className="direction-starters">
            {STARTERS[aspect].map((starter) => (
              <button key={starter} type="button" className="direction-starter"
                      onClick={() => onPrefill(starter)}>
                {starter}
              </button>
            ))}
          </div>
        ) : items.map((item) => item.message ? (
          <Message key={item.message.id} message={item.message} />
        ) : item.proposal ? (
          <Proposal key={item.proposal.id} project={project} aspect={aspect} board={board}
                    proposal={item.proposal} influence={names[item.proposal.influence] ?? item.proposal.influence}
                    onOpen={onOpen} />
        ) : null)}
      </div>
      <form className="direction-composer" onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}>
        <textarea ref={composer} value={text} rows={text.includes("\n") || text.length > 60 ? 4 : 2}
                  maxLength={4000} aria-label={t("Message")} placeholder={t("Write to the agent")}
                  onChange={(event) => onText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") onText("");
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }} />
        {running ? (
          // While the agent answers, the send button stops it.
          <button type="button" className="btn btn-secondary btn-icon direction-stop"
                  aria-label={t("Stop the agent")} title={t("Stop the agent")}
                  onClick={() => void act(() => api.directionStop(project, aspect), t("Stop refused"))}>
            <StopGlyph />
          </button>
        ) : (
          <button type="submit" className="btn btn-primary btn-icon" disabled={!text.trim() || sending}
                  aria-label={t("Send")} title={t("Send")}>
            <SendGlyph />
          </button>
        )}
      </form>
    </section>
  );
}

function Message({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return <div className="direction-bubble is-user">{message.text}</div>;
  }
  const steps = message.steps ?? [];
  const running = message.state === "running";
  const latest = [...steps].reverse().find((step) => step.tool);
  return (
    <div className="direction-answer">
      {steps.length > 0 && (
        <details className="direction-steps">
          <summary>{tn(steps.length, "{n} step", "{n} steps")}</summary>
          <ol>
            {steps.map((step, rank) => (
              <li key={rank} className={step.note ? "is-note" : ""}>
                {step.note ?? stepLabel(step)}
              </li>
            ))}
          </ol>
        </details>
      )}
      {message.text ? (
        <div className="direction-bubble">
          <Markdown text={message.text} />
        </div>
      ) : running ? (
        <div className="direction-bubble is-waiting">
          <span className="direction-dots" aria-hidden="true"><i /><i /><i /></span>
          <span>{latest ? stepLabel(latest) : t("Thinking…")}</span>
        </div>
      ) : null}
      {message.state === "failed" && <Badge tone="danger">{tr(message.error) || t("The agent stopped")}</Badge>}
      {message.state === "stopped" && <Badge tone="quiet">{t("Stopped")}</Badge>}
    </div>
  );
}

/** Images the agent proposes: the user adjusts them, pays them, or sets them aside. */
function Proposal({ project, aspect, board, proposal, influence, onOpen, onAnswered, open = false }: {
  project: string;
  aspect: DirectionAspect;
  board: DirectionBoard;
  proposal: InfluenceProposal;
  influence: string;
  onOpen: (image: DirectionImage) => void;
  /** Paid or set aside: the dialog that showed it closes. */
  onAnswered?: () => void;
  /** The prompt shown unfolded: the dialog that asked for it. */
  open?: boolean;
}) {
  const queryClient = useQueryClient();
  const { notify } = useStudio();
  const [prompt, setPrompt] = useState(proposal.prompt);
  const [model, setModel] = useState(proposal.model);
  const [count, setCount] = useState(String(proposal.count));
  const [busy, setBusy] = useState(false);
  const total = imageCost(model, Number(count), proposal.width, proposal.height);
  const models = IMAGE_MODELS.filter((entry) => entry.air !== KONTEXT || proposal.reference);

  const answer = async (run: () => Promise<InfluenceProposal>, title: string) => {
    setBusy(true);
    try {
      await run();
      void queryClient.invalidateQueries({ queryKey: ["direction", project, aspect] });
      onAnswered?.();
    } catch (error) {
      notify({ kind: "error", title, body: tr((error as Error).message) });
    } finally {
      setBusy(false);
    }
  };

  if (proposal.status === "dismissed") {
    return (
      <p className="direction-set-aside">
        {t("Proposal set aside: {title}", { title: proposal.influence ? influence : ASPECTS[aspect] })}
      </p>
    );
  }
  return (
    <article className={`direction-proposal is-${proposal.status}`}>
      <header>
        <span className="direction-proposal-what">
          {t("Images for {title}", { title: proposal.influence ? influence : ASPECTS[aspect] })}
        </span>
        {proposal.status === "proposed" && total !== null && <span className="num direction-price">{cost(total)}</span>}
      </header>
      {proposal.why && <p className="direction-why">{proposal.why}</p>}
      {proposal.status === "proposed" ? (
        <>
          <details className="direction-prompt" open={open}>
            <summary>{t("Prompt")}</summary>
            <textarea className="mono" value={prompt} rows={5} maxLength={1500} aria-label={t("Prompt")}
                      onChange={(event) => setPrompt(event.target.value)} />
          </details>
          <div className="direction-proposal-settings">
            <Select value={model} label={t("Model")} onChange={setModel}
                    options={models.map((entry) => ({ value: entry.air, label: entry.label }))} />
            <Seg<string> value={count} onChange={setCount}
                         options={["1", "2", "3", "4"].map((value) => ({ value, label: value }))} />
          </div>
          <div className="direction-proposal-actions">
            <button type="button" className="btn btn-ghost btn-sm" disabled={busy}
                    onClick={() => void answer(() => api.dismissProposal(project, aspect, proposal.id),
                      t("Proposal not set aside"))}>
              {t("Set aside")}
            </button>
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || !prompt.trim() || total === null}
                    onClick={() => void answer(() => api.payProposal(project, aspect, proposal.id, {
                      prompt, model, count: Number(count), confirm: true,
                    }), t("Generation refused"))}>
              {busy ? t("sending…") : t("Pay {total} and start", { total: cost(total ?? 0) })}
            </button>
          </div>
        </>
      ) : (
        <>
          {proposal.status === "failed" && <Badge tone="danger">{tr(proposal.error)}</Badge>}
          <ul className="direction-proposal-images">
            {proposal.status === "queued"
              ? Array.from({ length: proposal.count }, (_, rank) => (
                <li key={rank} className="direction-pending" aria-label={t("Generating…")} />))
              : proposal.images.map((image) => (
                <li key={image.key}>
                  <button type="button" onClick={() => onOpen(image)}>
                    <img src={imageUrl(project, board, image, 360)} alt="" loading="lazy" />
                  </button>
                </li>
              ))}
          </ul>
        </>
      )}
    </article>
  );
}

/* --------------------------------------------------------------- glyphs */

function ImageGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M20 16l-4.5-4.5L7 19" />
    </svg>
  );
}

function TrashGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 7h14" />
      <path d="M10 4h4" />
      <path d="M7 7l1 12h8l1-12" />
    </svg>
  );
}

function PlusGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function StopGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  );
}

/** A new conversation: a bubble with a plus. */
function NewGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 17.5V6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4 3.5z" />
      <path d="M12 8v5M9.5 10.5h5" />
    </svg>
  );
}

function SendGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="M6 11l6-6 6 6" />
    </svg>
  );
}

/** Generate: a spark. */
function SparkGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 4l1.8 4.7L18.5 10.5l-4.7 1.8L12 17l-1.8-4.7L5.5 10.5l4.7-1.8z" />
      <path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
    </svg>
  );
}

/** Ask the agent: a bubble with a spark. */
function AgentGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 17.5V6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4 3.5z" />
      <path d="M12 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" />
    </svg>
  );
}

function ArrowGlyph({ back = false }: { back?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={back ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"} />
    </svg>
  );
}
