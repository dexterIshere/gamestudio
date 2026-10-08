/**
 * Documents: the texts written for a project.
 *
 * A character bible, world notes, an art direction -- what no tool guesses and
 * no report replaces. They live in `<folder>/.gamestudio/documents/`, so they
 * are versioned with the recipe: the opposite of the workspace report, which
 * is regenerated and not kept.
 *
 * The page reads the Markdown and renders it. "Edit" switches the text to
 * input, "Save" switches it back to reading: there is no second "raw" pane.
 *
 * `Shelf` is the same page for one shelf: Notes, Ideas, Devlog, the game design
 * pages and each world section are just one more documents folder
 * (`.gamestudio/documents/<shelf>/`), with their default template. In a game
 * design section (`design/…`: Interface, Mechanics, Art direction, VFX) each
 * card becomes a workbench (`CardWorkspace`): the game's render, a sketch,
 * generated images, and an agent conversation on the card's subject.
 */

import {
  Suspense, lazy, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject,
} from "react";
import {
  api, documentImageUrl, type CreateBrief, type DocumentTemplate, type CardBrief,
  type ProjectDocument,
} from "../api";
import { IMAGE_LIMITS, PickImages, useImageDrop, type Dropped } from "../components/CardReferences";
import HandoffDialog from "../components/Handoff";
import Markdown, { type DocumentLinker } from "../components/Markdown";
import {
  Badge, Callout, CloseCross, Dialog, Empty, Field, PageHeader, Panel, Section, Seg, bytes, shortDate,
} from "../components/ui";
import { inTauri } from "../lib/host";
import { useReveal } from "../lib/paths";
import {
  useCreateDocument, useDeleteDocument, useDocuments, useDocumentTemplates, useCardMedia,
  useSaveDocument,
} from "../lib/queries";
import { useStudio } from "../lib/store";
import { t, tr } from "../lib/i18n";

// A card's workbench carries the sketch editor, which is heavy: it loads only
// when a game design section is opened.
const CardWorkspace = lazy(() => import("../components/CardWorkspace"));

// Below this section width, the list goes above the workbench: side by side,
// the workbench would keep less than room for a sketch (Excalidraw folds its
// tools below 730 px) and a 72-character line.
const CARD_SPLIT = 1020;

/**
 * Is the section too narrow for the list and the workbench side by side?
 * Measured on the section itself, not the window: the rail folds by hand as
 * well as at 1180 px. No container query: its containment would make the
 * section the containing block of the `position: fixed` dialogs inside it --
 * a paid generation's, for instance.
 */
function useNarrow(below: number): [RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setNarrow(element.clientWidth < below);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [below]);
  return [ref, narrow];
}

export default function Documents() {
  return <Shelf title={t("Documents")} folder="" />;
}

/**
 * The document a relative link points to in this shelf -- `top-bar.md`,
 * `./top-bar.md#states`, `../interface/top-bar.md` --, or `null` if it leads
 * elsewhere: another shelf, the disk, the web.
 */
function shelfLink(folder: string, href: string): string | null {
  const path = href.split(/[?#]/)[0] ?? "";
  if (!/\.md$/i.test(path) || /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("/")) return null;
  const here = folder.split("/").filter(Boolean);
  const there = [...here];
  const segments = path.split("/");
  const file = segments.pop() ?? "";
  for (const segment of segments) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (!there.length) return null;
      there.pop();
    } else {
      there.push(segment);
    }
  }
  if (there.join("/") !== here.join("/")) return null;
  try {
    return decodeURIComponent(file.slice(0, -3)) || null;
  } catch {
    return file.slice(0, -3) || null;
  }
}

export function Shelf({ title, folder, template = "blank", actions, docActions }: {
  title: string;
  /** The shelf: empty for the root of the project's documents. */
  folder: string;
  /** The template offered first on creation. */
  template?: string;
  /** Shelf-specific actions, next to "New". */
  actions?: ReactNode;
  /** Actions on the open document, next to "Edit". */
  docActions?: (document: ProjectDocument, dirty: boolean) => ReactNode;
}) {
  const { project } = useStudio();
  const { data: documents, isLoading, error } = useDocuments(project, folder);
  const { data: templates } = useDocumentTemplates();
  const [current, setCurrent] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const [creating, setCreating] = useState(false);
  const [discussing, setDiscussing] = useState<ProjectDocument | null>(null);
  // The list is re-read after a creation or a deletion, not before: the created
  // document is not in it yet, the deleted one still is. Without these two
  // refs, the page would reopen the wrong one.
  const created = useRef<string | null>(null);
  const gone = useRef<string | null>(null);
  const side = useRef<HTMLDivElement>(null);
  const [shelf, narrow] = useNarrow(CARD_SPLIT);
  // A game design section: each card in it is a workbench.
  const design = folder.startsWith("design/");

  // The current document: the most recent one when nothing is open.
  useEffect(() => {
    if (!documents) return;
    if (current && documents.some((entry) => entry.name === current)) {
      if (created.current === current) created.current = null;
      return;
    }
    if (current && created.current === current) return;
    setCurrent(documents.find((entry) => entry.name !== gone.current)?.name ?? null);
  }, [documents, current]);

  const open = (name: string) => {
    setCurrent(name);
    setFresh(false);
  };

  // A link from a document to a sibling opens it here, at the top of its panel,
  // instead of navigating the window.
  const linkDocument: DocumentLinker = (href) => {
    const name = shelfLink(folder, href);
    if (!name || !documents?.some((entry) => entry.name === name)) return null;
    return () => {
      open(name);
      side.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    };
  };

  const onDeleted = () => {
    gone.current = current;
    setCurrent(null);
  };

  const list = (
    <Panel
      title={title}
      eyebrow={documents
        ? design
          ? t("{n} card(s)", { n: documents.length })
          : t("{n} document(s)", { n: documents.length })
        : undefined}
      className={design ? "shelf-list card-list" : "shelf-list"}
      bodyClass={design ? "card-list-body" : "doc-list-body"}
    >
      {isLoading ? (
        <Empty title={t("Reading…")} />
      ) : (documents?.length ?? 0) === 0 ? (
        <Empty title={design ? t("No card") : t("No document")} />
      ) : design ? (
        // Two separate buttons on the line -- open, discuss --, never a button
        // inside a button.
        documents?.map((document) => (
          <div
            key={document.name}
            className={`card-row ${document.name === current ? "is-current" : ""}`}
          >
            <button
              type="button"
              className="card-pick"
              aria-current={document.name === current ? "true" : undefined}
              onClick={() => open(document.name)}
            >
              <b>{document.title}</b>
              <span className="card-row-facts">
                <span className="mono">{document.file}</span>
                <span className="num">{t("{words} words", { words: document.words })}</span>
              </span>
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-icon"
              aria-label={t("Discuss this card")}
              title={t("Discuss this card")}
              onClick={() => setDiscussing(document)}
            >
              <ChatPlusGlyph />
            </button>
          </div>
        ))
      ) : (
        documents?.map((document) => (
          <button
            key={document.name}
            className={`doc-row ${document.name === current ? "is-current" : ""}`}
            onClick={() => open(document.name)}
          >
            <span className="grow">
              <b>{document.title}</b>
              <span className="mono">{document.file}</span>
            </span>
            <span className="meta num">{t("{words} words", { words: document.words })}</span>
          </button>
        ))
      )}
    </Panel>
  );

  const opened = !current ? (
    <Panel title={design ? t("No card open") : t("No document open")}>
      <Empty title={design ? t("Choose a card") : t("Choose a document")} />
    </Panel>
  ) : design ? (
    <Suspense fallback={<div className="skeleton" style={{ height: 480 }} />}>
      <CardWorkspace
        key={`${folder}/${current}`}
        project={project}
        folder={folder}
        name={current}
        editing={fresh}
        onDeleted={onDeleted}
        onDiscuss={setDiscussing}
        linkDocument={linkDocument}
        actions={docActions}
      />
    </Suspense>
  ) : (
    <DocumentEditor
      key={`${folder}/${current}`}
      project={project}
      folder={folder}
      name={current}
      editing={fresh}
      onDeleted={onDeleted}
      linkDocument={linkDocument}
      actions={docActions}
    />
  );

  const columns = (
    <>
      {list}
      <div ref={side} className="shelf-side">{opened}</div>
    </>
  );

  return (
    <div className="stack-4">
      <PageHeader
        title={title}
        project={project}
        actions={
          <>
            {actions}
            <button className="btn btn-primary" disabled={!project} onClick={() => setCreating(true)}>
              {design ? t("New card") : t("New document")}
            </button>
          </>
        }
      />

      {error && <Callout>{t("The documents cannot be read: {error}", { error: String(error) })}</Callout>}

      <div ref={shelf} className={`card-shelf ${narrow ? "is-narrow" : ""}`}>{columns}</div>

      {creating && (
        <NewDocument
          project={project}
          folder={folder}
          label={title}
          initial={template}
          templates={templates ?? []}
          onClose={() => setCreating(false)}
          onCreated={(name) => {
            created.current = name;
            setCurrent(name);
            setFresh(true);
            setCreating(false);
          }}
        />
      )}

      {discussing && (
        <CardHandoff
          project={project}
          folder={folder}
          document={discussing}
          onClose={() => setDiscussing(null)}
        />
      )}
    </div>
  );
}

/**
 * Discussing a card with an agent: the brief gives it its subject -- the text,
 * the game's render, the sketch, the generated images --, then the
 * conversation, new or already open, it lands in.
 */
function CardHandoff({ project, folder, document, onClose }: {
  project: string;
  folder: string;
  document: ProjectDocument;
  onClose: () => void;
}) {
  const { data: media } = useCardMedia(project, folder, document.name);
  return (
    <HandoffDialog<CardBrief>
      title={t("Discuss this card")}
      eyebrow={document.title}
      load={() => api.cardBrief(project, folder, document.name)}
      rows={(brief) => [
        [t("Section"), brief.section_label],
        [t("Render"), media?.render ? <span className="mono">{media.render.scene}</span> : null],
        [t("Sketch"), media?.sketch?.png ? t("Yes") : null],
        [t("Generated images"), media ? <span className="num">{media.generations.length}</span> : null],
      ]}
      submit={(choice) => api.cardHandoff(project, folder, document.name, choice)}
      sent={(session) => t("{title} handed to {title2}", { title: document.title, title2: session.title })}
      onClose={onClose}
    />
  );
}

/* ------------------------------------------------------------------ the text */

/** A document being read and edited: its text, its input, its saving. */
export interface DocumentDraft {
  /** The document as the shelf's list describes it; `null` until the list is read. */
  selected: ProjectDocument | null;
  /** The text has been read at least once. */
  ready: boolean;
  /** Why the text could not be read. */
  failure: string | null;
  text: string;
  setText: (text: string) => void;
  dirty: boolean;
  editing: boolean;
  edit: () => void;
  /** Leaves input: the text goes back to what is saved. */
  cancel: () => void;
  /** Saves, then goes back to reading; without changes, only goes back. */
  save: () => void;
  saving: boolean;
  /** Why the last save was refused. */
  error: string | null;
}

/**
 * The text of an open document, shared by a shelf's panel and a card's
 * workbench: read, followed, edited, saved.
 */
export function useDocumentDraft(project: string, folder: string, name: string,
  { editing: initialEditing = false, onSaved }: { editing?: boolean; onSaved?: () => void } = {},
): DocumentDraft {
  const { data: documents } = useDocuments(project, folder);
  const selected = (documents ?? []).find((entry) => entry.name === name) ?? null;
  const [text, setText] = useState("");
  // The text as read: the only honest comparison to say "modified". Comparing
  // sizes or titles would give false negatives.
  const [initial, setInitial] = useState("");
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [editing, setEditing] = useState(initialEditing);
  const save = useSaveDocument();
  const dirty = selected !== null && text !== initial;
  // An agent may rewrite the open document: its date changes in the list,
  // which re-reads itself, and the text follows -- except during input, which
  // is never overwritten.
  const stamp = selected?.mtime ?? 0;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const loaded = useRef("");

  useEffect(() => {
    const key = `${project}/${folder}/${name}`;
    const typing = () => loaded.current === key && dirtyRef.current;
    if (typing()) return;
    let alive = true;
    api.document(project, name, folder).then(
      (document) => {
        // Input may have started during the read: it wins.
        if (!alive || typing()) return;
        loaded.current = key;
        setText(document.text);
        setInitial(document.text);
        setReady(true);
        setFailure(null);
      },
      (reason: Error) => {
        if (alive) setFailure(reason.message);
      },
    );
    return () => {
      alive = false;
    };
  }, [project, name, folder, stamp]);

  return {
    selected,
    ready,
    failure,
    text,
    setText,
    dirty,
    editing,
    edit: () => setEditing(true),
    cancel: () => {
      setText(initial);
      setEditing(false);
      save.reset();
    },
    save: () => {
      if (!selected || save.isPending) return;
      if (!dirty) {
        setEditing(false);
        return;
      }
      const sent = text;
      save.mutate({ project, folder, name: selected.name, text: sent }, {
        onSuccess: () => {
          setInitial(sent);
          setEditing(false);
          onSaved?.();
        },
      });
    },
    saving: save.isPending,
    error: save.isError ? (save.error as Error).message : null,
  };
}

/** The text: rendered when reading, a text area when editing. */
export function DraftText({ draft, reading }: {
  draft: DocumentDraft;
  /** The text's rendering, set by each page: title already shown, lead, links. */
  reading: (text: string) => ReactNode;
}) {
  if (!draft.ready) {
    return draft.failure
      ? <Callout>{t("The text cannot be read: {failure}", { failure: draft.failure })}</Callout>
      : <Empty title={t("Reading…")} />;
  }
  if (!draft.editing) return <>{reading(draft.text)}</>;
  return (
    <textarea
      className="mono note-editor"
      value={draft.text}
      aria-label={t("Text")}
      autoFocus
      spellCheck={false}
      readOnly={draft.saving}
      onChange={(event) => draft.setText(event.target.value)}
      onKeyDown={(event) => {
        // Ctrl+S saves, as in any editor.
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          draft.save();
        }
      }}
    />
  );
}

/**
 * The editing actions: the pencil to edit, then "Cancel" and "Save". Cancelling
 * modified input is confirmed: written text would be lost.
 */
export function EditGestures({ draft }: { draft: DocumentDraft }) {
  const [abandon, setAbandon] = useState(false);
  // Typing again disarms the discard.
  useEffect(() => setAbandon(false), [draft.text, draft.editing]);

  if (!draft.editing) {
    return (
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        aria-label={t("Edit")}
        title={t("Edit")}
        disabled={!draft.ready}
        onClick={draft.edit}
      >
        <PencilGlyph />
      </button>
    );
  }
  return (
    <>
      <button
        type="button"
        className={`btn btn-sm ${abandon ? "btn-danger" : "btn-ghost"}`}
        disabled={draft.saving}
        title={abandon ? t("The changes will be lost") : undefined}
        onClick={() => (draft.dirty && !abandon ? setAbandon(true) : draft.cancel())}
      >
        {abandon ? t("Discard") : t("Cancel")}
      </button>
      <button type="button" className="btn btn-primary btn-sm" disabled={draft.saving} onClick={draft.save}>
        {draft.saving ? t("Saving…") : t("Save")}
      </button>
    </>
  );
}

/**
 * An open document: rendered, edited, saved, deleted. It is a shelf's panel,
 * and the "Card" step of a world card's workbench.
 */
export function DocumentEditor({
  project, folder, name, editing = false, onDeleted, onSaved, linkDocument, actions, title,
}: {
  project: string;
  folder: string;
  name: string;
  /** A document just created opens in edit mode. */
  editing?: boolean;
  onDeleted: () => void;
  onSaved?: () => void;
  /** A link to a sibling document: the page knows how to open it. */
  linkDocument?: DocumentLinker;
  /** Actions on the document, next to "Edit". */
  actions?: (document: ProjectDocument, dirty: boolean) => ReactNode;
  /** The panel's title; by default, the document's. */
  title?: ReactNode;
}) {
  const draft = useDocumentDraft(project, folder, name, { editing, onSaved });
  const { selected } = draft;
  const reveal = useReveal();

  if (!selected) {
    return (
      <Panel title={title ?? t("Document")}>
        <Empty title={t("Reading…")} />
      </Panel>
    );
  }

  return (
    <Panel
      title={title ?? selected.title}
      eyebrow={selected.file}
      actions={
        <div className="doc-gestures">
          {actions?.(selected, draft.dirty)}
          <EditGestures draft={draft} />
        </div>
      }
      foot={
        <>
          <span className="hint grow">
            {draft.error ? <Badge tone="danger">{draft.error}</Badge>
              : draft.dirty ? t("Unsaved changes")
                : t("{words} words · {bytes}", { words: selected.words, bytes: bytes(selected.size_bytes) })
                  + (selected.modified_at ? t(" · modified {shortDate}", { shortDate: shortDate(selected.modified_at) }) : "")}
          </span>
          <button
            className="btn btn-ghost btn-sm"
            disabled={!inTauri()}
            onClick={() => void reveal(selected.path)}
          >
            {t("Show on disk")}
          </button>
          <DeleteButton project={project} folder={folder} name={selected.name}
                        title={selected.title} onDeleted={onDeleted} />
        </>
      }
    >
      <DraftText
        draft={draft}
        reading={(text) => (
          <Markdown
            text={text}
            titled={title === undefined}
            resolveImage={(src) => documentImageUrl(project, folder, src)}
            linkDocument={linkDocument}
          />
        )}
      />
    </Panel>
  );
}

export function DeleteButton({ project, folder, name, title, onDeleted }: {
  project: string;
  folder: string;
  name: string;
  title: string;
  onDeleted: () => void;
}) {
  const remove = useDeleteDocument();
  const [asking, setAsking] = useState(false);

  if (!asking) {
    return (
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAsking(true)}>
        {t("Delete")}
      </button>
    );
  }
  return (
    <button
      type="button"
      className="btn btn-danger btn-sm"
      disabled={remove.isPending}
      onClick={() => remove.mutate({ project, folder, name }, { onSuccess: onDeleted })}
      title={t("Delete “{title}” for good", { title })}
    >
      {remove.isPending ? t("Deleting…") : t("Confirm")}
    </button>
  );
}

/* --------------------------------------------------------------------- icons */

/** The chat bubble and its "+": open an agent conversation on a card. */
export function ChatPlusGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5.5 4.5h13a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11l-4.5 3.5v-3.5h-1a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z" />
      <path d="M12 7.5v6M9 10.5h6" />
    </svg>
  );
}

/** The pencil: switch a text to editing. */
export function PencilGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 19.5l.9-4.1L15.6 5.2a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.6 18.6z" />
      <path d="M14 6.8l3.2 3.2" />
    </svg>
  );
}

/** The shortest request an agent can take: the service's limit. */
const MIN_REQUEST = 12;

/** An image dropped with a card to create: bytes, or a disk path (Tauri). */
interface Pending {
  key: string;
  label: string;
  file?: File;
  path?: string;
  /** A preview while the dialog is open: only bytes have one. */
  url?: string;
}

let pendingSeq = 0;

function pendingOf({ files, paths }: Dropped): Pending[] {
  return [
    ...files.map((file) => ({
      key: `f${(pendingSeq += 1)}`, label: file.name || t("pasted image"), file,
      url: URL.createObjectURL(file),
    })),
    ...paths.map((path) => ({
      key: `p${(pendingSeq += 1)}`, label: path.split(/[\\/]/).pop() ?? path, path,
    })),
  ];
}

/**
 * Creating in a section: say what is needed and an agent writes it with the
 * section's template, next to what the section already holds; or open an
 * empty document with the same template. Only the documents root lets the
 * user pick the template.
 *
 * A game design card is also shown: images dragged into the dialog are its
 * references. The agent looks at them and files them with the card they
 * illustrate; an empty card receives them on creation.
 */
export function NewDocument({ project, folder, label, initial, templates, filing, onClose, onCreated }: {
  project: string;
  folder: string;
  /** The section as the rail names it. */
  label: string;
  initial: string;
  templates: DocumentTemplate[];
  /** The world section group the card is filed in: axis → value, and its name. */
  filing?: { values: Record<string, string>; label: string };
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [mode, setMode] = useState<"agent" | "blank">("agent");
  const [request, setRequest] = useState("");
  const [handing, setHanding] = useState(false);
  const [title, setTitle] = useState("");
  const [template, setTemplate] = useState(initial);
  const create = useCreateDocument();
  const card = folder.startsWith("design/") || folder.startsWith("world/");
  // Only game design cards have a workbench that shows references.
  const visual = folder.startsWith("design/");
  const [images, setImages] = useState<Pending[]>([]);
  const [resolved, setResolved] = useState<{ paths: string[]; names: string[] }>(
    { paths: [], names: [] });
  const [sending, setSending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const drop = useImageDrop((dropped) => setImages((held) => [...held, ...pendingOf(dropped)]));
  const forget = (key: string) => setImages((held) => {
    const gone = held.find((entry) => entry.key === key);
    if (gone?.url) URL.revokeObjectURL(gone.url);
    return held.filter((entry) => entry.key !== key);
  });
  // Previews do not outlive the dialog.
  const shown = useRef(images);
  shown.current = images;
  useEffect(() => () => shown.current.forEach((entry) => entry.url && URL.revokeObjectURL(entry.url)), []);
  const heading = `${card ? t("New card") : t("New document")}${folder ? ` — ${label}` : ""}${
    filing ? ` · ${filing.label}` : ""}`;
  const extras = { ...resolved, axes: filing?.values };
  // The project folder is already in the breadcrumb: the path starts from it.
  const eyebrow = `.gamestudio/documents/${folder ? `${folder}/` : ""}`;
  const wanted = request.trim();
  const ready = sending === null && (mode === "agent"
    ? wanted.length >= MIN_REQUEST
    : Boolean(title.trim()) && !create.isPending);

  const submit = async () => {
    if (!ready) return;
    setFailure(null);
    if (mode === "agent") {
      // The agent reads paths: bytes go through the inbox, a file dragged from
      // the disk keeps its own.
      try {
        setSending(t("Uploading…"));
        const paths = [];
        for (const entry of images) {
          paths.push(entry.path ?? (await api.uploadToInbox(entry.file!, entry.label)).relative);
        }
        setResolved({ paths, names: images.map((entry) => entry.label) });
        setHanding(true);
      } catch (error) {
        setFailure((error as Error).message);
      } finally {
        setSending(null);
      }
      return;
    }
    create.mutate({ project, folder, title: title.trim(), template }, {
      onSuccess: async (document) => {
        try {
          // Created in a group: it is filed there before opening.
          if (filing) {
            await api.setEntityAxes(project, folder.split("/")[1] ?? "", document.name, filing.values);
          }
          if (images.length) setSending(t("References…"));
          for (const entry of images) {
            if (entry.path) await api.addCardReferencePath(project, folder, document.name, entry.path);
            else await api.addCardReference(project, folder, document.name, entry.file!, entry.label);
          }
          onCreated(document.name);
        } catch (error) {
          // The card exists: open it; the refused image can be dropped again from its workbench.
          setFailure((error as Error).message);
          onCreated(document.name);
        } finally {
          setSending(null);
        }
      },
    });
  };

  if (handing) {
    return (
      <HandoffDialog<CreateBrief>
        title={heading}
        eyebrow={eyebrow}
        load={() => api.createBrief(project, folder, wanted, extras)}
        rows={(brief) => [
          [t("Request"), brief.request],
          ...(brief.axes.length
            ? [[t("Group"), brief.axes.map((entry) => `${entry.axis_label}: ${entry.value_label}`)
                .join(" · ")] as [string, string]]
            : []),
          ...(brief.images.length ? [[t("Images"), `${brief.images.length}`] as [string, string]] : []),
          [t("Template"), templates.find((entry) => entry.id === brief.template)?.label
            ?? brief.template],
        ]}
        submit={(choice) => api.createHandoff(project, folder, wanted, extras, choice)}
        sent={(session) => t("{label}: request handed to {title}", { label, title: session.title })}
        onClose={onClose}
      />
    );
  }

  return (
    <Dialog
      wide={mode === "agent"}
      title={heading}
      eyebrow={eyebrow}
      onClose={onClose}
      foot={
        <>
          <span className="hint grow">
            {create.isError && <Badge tone="danger">{String(create.error)}</Badge>}
            {failure && <Badge tone="danger">{failure}</Badge>}
          </span>
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!ready} onClick={() => void submit()}>
            {sending ?? (mode === "agent" ? t("Choose the agent") : create.isPending ? t("Creating…") : t("Create"))}
          </button>
        </>
      }
    >
      <div className={`stack-4 ${visual && drop.dragging ? "is-dragging" : ""}`}
           {...(visual ? drop.bind : {})}>
        <Seg
          value={mode}
          options={[{ value: "agent", label: t("By an agent") }, { value: "blank", label: t("Empty") }]}
          onChange={setMode}
        />
        {mode === "agent" ? (
          <Field label={t("Request")} hint={t("At least {MIN_REQUEST} characters", { MIN_REQUEST })}>
            <textarea
              value={request}
              autoFocus
              rows={8}
              onChange={(event) => setRequest(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void submit();
              }}
            />
          </Field>
        ) : (
          <>
            <Field label={t("Title")}>
              <input
                value={title}
                autoFocus
                onChange={(event) => setTitle(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submit();
                }}
              />
            </Field>
            {!folder && (
              <Section title={t("Template")}>
                {templates.map((entry) => (
                  <button
                    key={entry.id}
                    className={`doc-row ${entry.id === template ? "is-current" : ""}`}
                    onClick={() => setTemplate(entry.id)}
                  >
                    <span className="grow"><b>{tr(entry.label)}</b></span>
                  </button>
                ))}
              </Section>
            )}
          </>
        )}
        {visual && (
          <div className="new-refs">
            <div className="new-refs-head">
              <span className="eyebrow">{t("References")}</span>
              <PickImages
                disabled={sending !== null}
                onPick={(files) => setImages((held) => [...held, ...pendingOf({ files, paths: [] })])}
              />
              <span className="hint">{IMAGE_LIMITS}</span>
            </div>
            {images.length > 0 && (
              <ul className="new-refs-list">
                {images.map((entry) => (
                  <li key={entry.key} className="new-ref" title={entry.path ?? entry.label}>
                    <span className="board check">
                      {entry.url ? <img src={entry.url} alt="" /> : <span className="mono">{entry.label}</span>}
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-icon"
                      aria-label={t("Remove {label}", { label: entry.label })}
                      onClick={() => forget(entry.key)}
                    >
                      <CloseCross />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
