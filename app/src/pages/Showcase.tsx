/**
 * The showcase: the game's icons or props, shown as they are, to be critiqued.
 *
 * Not a list of cards: the elements themselves, by family, side by side, at
 * the size and on the background where they are seen. The tools are a
 * critic's -- the game's background, dark, light or checkerboard; the display
 * size; the silhouette (does an icon read without color?); guides (center,
 * safe zone); crisp pixels; and for a button, its four states side by side,
 * flagging those the theme does not tell apart. An open element is viewed
 * large, with its family siblings; the keyboard moves to the next (← →).
 *
 * No verdict: what is in the game is kept, what is no longer wanted leaves it
 * -- "Delete" moves it (one element, or its whole family) to the project's
 * trash, from which it can be restored. An element to rework is discussed:
 * its bubble opens an agent on it (`handoff.showcase_brief`). Icons are
 * created and redone through the forge (`components/IconForge.tsx`): one,
 * several at once on a split sheet, or one icon redrawn.
 *
 * The section's written cards are one click away: "Cards".
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import {
  api, showcaseImageUrl, type Showcase, type ShowcaseBrief, type ShowcaseItem, type ShowcaseKind,
  type ShowcaseState,
} from "../api";
import HandoffDialog from "../components/Handoff";
import {
  DeleteDialog, CardsGlyph, ForgeDialog, ForgePanel, PlusGlyph, PlusManyGlyph, RedrawAllGlyph,
  RedrawGlyph, TrashDialog, TrashGlyph, useTrashCount,
} from "../components/IconForge";
import { useRenderShowcase, useShowcase } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Badge, CloseCross, Empty, Facts, Flags, PageHeader, Seg, ToolGroup, Toolbar } from "../components/ui";
import { ChatPlusGlyph, Shelf } from "./Documents";
import { t, tn, tr } from "../lib/i18n";

const SHELVES: Record<ShowcaseKind, { title: string; folder: string; template: string }> = {
  icons: { title: t("Icons"), folder: "design/icons", template: "icon" },
  props: { title: "Props", folder: "design/props", template: "prop" },
};

type Backdrop = "game" | "dark" | "light" | "checker";
type Family = Showcase["families"][number];
/** A dialog of the page: the forge, a deletion, the trash. */
type Gesture =
  | { kind: "forge"; mode: "one" | "set" | "redo"; item?: ShowcaseItem; folder?: string }
  | { kind: "delete"; item?: ShowcaseItem; family?: Family }
  | { kind: "trash" };

const BACKDROPS: { value: Backdrop; label: string }[] = [
  { value: "game", label: t("Game") },
  { value: "dark", label: t("Dark") },
  { value: "light", label: t("Light") },
  { value: "checker", label: t("Checkerboard") },
];

const STATE_LABELS: Record<ShowcaseState, string> = {
  normal: t("Normal"),
  hover: t("Hover"),
  pressed: t("Pressed"),
  disabled: t("Disabled"),
};

/** An icon's display sizes, in screen pixels. */
const ICON_SIZES = ["24", "32", "48", "64", "96"] as const;
type IconSize = (typeof ICON_SIZES)[number];
/** The sizes at which an icon is checked to still hold up, smallest first. */
const LADDER = [16, 24, 32, 48, 64];
/** A prop is drawn at twice the game's resolution: 1× brings it back to its game pixels. */
const PROP_SCALE = 2;
const ZOOMS = [{ value: "1", label: "1×" }, { value: "2", label: "2×" }];

/** The critique settings, remembered per viewer. */
interface Look {
  backdrop: Backdrop;
  size: IconSize;
  zoom: "1" | "2";
  silhouette: boolean;
  keylines: boolean;
  crisp: boolean;
  states: boolean;
}

const LOOK_KEY = "gs-showcase";
const LOOK: Look = {
  backdrop: "game", size: "48", zoom: "1", silhouette: false, keylines: false, crisp: false,
  states: true,
};

function useLook(): [Look, (change: Partial<Look>) => void] {
  const [look, setLook] = useState<Look>(() => {
    try {
      return { ...LOOK, ...JSON.parse(localStorage.getItem(LOOK_KEY) ?? "{}") };
    } catch {
      return LOOK;
    }
  });
  const change = (part: Partial<Look>) => setLook((current) => {
    const next = { ...current, ...part };
    try {
      localStorage.setItem(LOOK_KEY, JSON.stringify(next));
    } catch {
      /* A convenience: without storage, the showcase falls back to its defaults. */
    }
    return next;
  });
  return [look, change];
}

/** A showcase stage's background: its class, and the game's background when chosen. */
function backdropOf(look: Look, data: Showcase | undefined): { className: string; style?: CSSProperties } {
  if (look.backdrop === "game") {
    return { className: "showcase-bg is-game", style: { background: data?.theme.background } };
  }
  if (look.backdrop === "checker") return { className: "showcase-bg board check" };
  return { className: `showcase-bg is-${look.backdrop}` };
}

/** A background where black does not show: the silhouette is drawn light on it. */
const darkBackdrop = (look: Look) => look.backdrop === "game" || look.backdrop === "dark";

/* ------------------------------------------------------------------ the page */

export default function ShowcasePage({ kind }: { kind: ShowcaseKind }) {
  const { project } = useStudio();
  const [cards, setCards] = useState(false);
  const shelf = SHELVES[kind];

  if (cards) {
    return (
      <Shelf
        title={shelf.title}
        folder={shelf.folder}
        template={shelf.template}
        actions={(
          <button type="button" className="btn btn-secondary" onClick={() => setCards(false)}>
            {t("Showcase")}
          </button>
        )}
      />
    );
  }
  return <Gallery key={`${project}-${kind}`} project={project} kind={kind}
                  onCards={() => setCards(true)} />;
}

function Gallery({ project, kind, onCards }: {
  project: string;
  kind: ShowcaseKind;
  onCards: () => void;
}) {
  const { data, error, isLoading } = useShowcase(project, kind);
  const draw = useRenderShowcase(project);
  const [look, setLook] = useLook();
  const [open, setOpen] = useState<string | null>(null);
  const [discussing, setDiscussing] = useState<ShowcaseItem | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const trashCount = useTrashCount(project);
  const asked = useRef(false);

  // A prop without an image, or whose scene changed, is drawn on arrival: free,
  // local, a few seconds for the whole batch.
  useEffect(() => {
    if (kind !== "props" || !data?.stale || asked.current) return;
    asked.current = true;
    draw.mutate(false);
  }, [kind, data?.stale, draw]);

  const families = data?.families ?? [];
  const order = useMemo(() => families.flatMap((family) => family.items.map((item) => item.id)),
    [families]);
  const title = SHELVES[kind].title;

  return (
    <div className="showcase">
      <PageHeader
        title={title}
        project={project}
        actions={(
          <>
            {data && (
              <span className="num showcase-total">
                {data.total} {kind === "icons" ? t("icon") : "prop"}{data.total > 1 ? "s" : ""}
              </span>
            )}
            {/* Icon buttons: their name is their tooltip. */}
            {kind === "icons" && (
              <>
                <button type="button" className="btn btn-secondary btn-icon"
                        aria-label={t("New icon")} title={t("New icon")}
                        onClick={() => setGesture({ kind: "forge", mode: "one" })}>
                  <PlusGlyph />
                </button>
                <button type="button" className="btn btn-secondary btn-icon"
                        aria-label={t("Several icons")} title={t("Several icons")}
                        onClick={() => setGesture({ kind: "forge", mode: "set" })}>
                  <PlusManyGlyph />
                </button>
              </>
            )}
            {kind === "props" && (
              <button type="button" className="btn btn-secondary btn-icon" disabled={draw.isPending}
                      aria-label={t("Redraw")} title={draw.isPending ? t("Drawing…") : t("Redraw")}
                      onClick={() => draw.mutate(true)}>
                <RedrawAllGlyph />
              </button>
            )}
            {trashCount > 0 && (
              <button type="button" className="btn btn-ghost showcase-trash-btn"
                      aria-label={tn(trashCount, "Trash, {n} batch", "Trash, {n} batches")}
                      title={t("Trash")} onClick={() => setGesture({ kind: "trash" })}>
                <TrashGlyph />
                <span className="num">{trashCount}</span>
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Cards")} title={t("Cards")}
                    onClick={onCards}>
              <CardsGlyph />
            </button>
          </>
        )}
      />

      <Toolbar className="showcase-bar">
        <ToolGroup label={t("Background")}>
          <Seg value={look.backdrop} options={BACKDROPS} onChange={(backdrop) => setLook({ backdrop })} />
        </ToolGroup>
        <ToolGroup label={kind === "icons" ? t("Size (px)") : t("Size")}>
          {kind === "icons" ? (
            <Seg value={look.size} options={ICON_SIZES.map((size) => ({ value: size, label: size }))}
                 onChange={(size) => setLook({ size })} />
          ) : (
            <Seg value={look.zoom} options={ZOOMS} onChange={(zoom) => setLook({ zoom: zoom as Look["zoom"] })} />
          )}
        </ToolGroup>
        <ToolGroup label={t("View")}>
          <Flags options={[
            ...(kind === "icons"
              ? [
                  { label: t("Silhouette"), checked: look.silhouette, onChange: (silhouette: boolean) => setLook({ silhouette }) },
                  { label: t("Crisp pixels"), checked: look.crisp, onChange: (crisp: boolean) => setLook({ crisp }) },
                ]
              : [{ label: t("States"), checked: look.states, onChange: (states: boolean) => setLook({ states }) }]),
            { label: t("Guides"), checked: look.keylines, onChange: (keylines: boolean) => setLook({ keylines }) },
          ]} />
        </ToolGroup>
      </Toolbar>

      {kind === "icons" && <ForgePanel project={project} />}
      {draw.isPending && <Badge tone="quiet">{t("Godot is drawing the props…")}</Badge>}
      {draw.isError && <Badge tone="danger">{(draw.error as Error).message}</Badge>}

      {isLoading ? (
        <Empty title={t("Reading the game…")} />
      ) : error ? (
        <Empty title={(error as Error).message} />
      ) : data && data.total === 0 ? (
        <Empty title={kind === "icons" ? t("No icon in the game") : t("No prop in the game")} />
      ) : (
        families.map((family) => (
          <section key={family.id} className="showcase-family">
            <header className="showcase-family-head">
              <h2>{tr(family.label)}</h2>
              <span className="num">{family.items.length}</span>
              {family.folder && <span className="mono">{family.folder}/</span>}
              <span className="spacer" />
              {kind === "icons" && family.folder && (
                <button type="button" className="btn btn-ghost btn-icon"
                        aria-label={t("New icon in {label}", { label: family.label })} title={t("New icon")}
                        onClick={() => setGesture({ kind: "forge", mode: "one", folder: family.folder })}>
                  <PlusGlyph />
                </button>
              )}
              <button type="button" className="btn btn-ghost btn-icon"
                      aria-label={t("Delete the {label} family", { label: family.label })} title={t("Delete the family")}
                      onClick={() => setGesture({ kind: "delete", family })}>
                <TrashGlyph />
              </button>
            </header>
            <ul className={`showcase-grid is-${kind}`}>
              {family.items.map((item) => (
                <li key={item.id}>
                  <Tile project={project} kind={kind} item={item} look={look} data={data}
                        onOpen={() => setOpen(item.id)} onDiscuss={() => setDiscussing(item)}
                        onDelete={() => setGesture({ kind: "delete", item })} />
                </li>
              ))}
            </ul>
          </section>
        ))
      )}

      {open && data && createPortal(
        <Inspector project={project} kind={kind} data={data} id={open} order={order} look={look}
               paused={discussing !== null || gesture !== null} onMove={setOpen}
               onClose={() => setOpen(null)} onDiscuss={setDiscussing}
               onRedo={(item) => setGesture({ kind: "forge", mode: "redo", item })}
               onDelete={(item) => setGesture({ kind: "delete", item })} />,
        document.body,
      )}
      {/* After the room: the conversation opens on top of it. */}
      {discussing && createPortal(
        <ShowcaseHandoff project={project} kind={kind} item={discussing}
                         onClose={() => setDiscussing(null)} />,
        document.body,
      )}
      {gesture && createPortal(
        gesture.kind === "forge" ? (
          <ForgeDialog project={project} mode={gesture.mode} item={gesture.item} folder={gesture.folder}
                       onClose={() => setGesture(null)} />
        ) : gesture.kind === "delete" ? (
          <DeleteDialog project={project} kind={kind} item={gesture.item} family={gesture.family}
                        onClose={() => setGesture(null)}
                        onDeleted={() => gesture.item?.id === open && setOpen(null)} />
        ) : (
          <TrashDialog project={project} onClose={() => setGesture(null)} />
        ),
        document.body,
      )}
    </div>
  );
}

/* ------------------------------------------------------------- one element */

function Tile({ project, kind, item, look, data, onOpen, onDiscuss, onDelete }: {
  project: string;
  kind: ShowcaseKind;
  item: ShowcaseItem;
  look: Look;
  data: Showcase | undefined;
  onOpen: () => void;
  onDiscuss: () => void;
  onDelete: () => void;
}) {
  const bg = backdropOf(look, data);
  const states = look.states && item.states.length > 1 ? item.states : item.states.slice(0, 1);
  return (
    <div className="showcase-tile">
    <button type="button" className="showcase-open" onClick={onOpen} title={item.file}>
      <span className={`showcase-stage ${bg.className}`} style={bg.style}>
        {kind === "icons" || item.type === "image" ? (
          <IconView project={project} kind={kind} item={item} size={Number(look.size)} look={look} />
        ) : states.length === 0 ? (
          <span className="mono showcase-missing">{item.error || (item.stale ? t("to draw") : "—")}</span>
        ) : (
          <span className="showcase-states">
            {states.map((entry) => (
              <span key={entry.state} className="showcase-state">
                <PropView project={project} item={item} state={entry.state} zoom={Number(look.zoom)}
                          keylines={look.keylines} />
                {states.length > 1 && (
                  <span className={`showcase-state-label ${entry.same ? "is-same" : ""}`}>
                    {STATE_LABELS[entry.state]}{entry.same ? t(" = normal") : ""}
                  </span>
                )}
              </span>
            ))}
          </span>
        )}
      </span>
      <span className="showcase-cap">
        <b>{item.title}</b>
        <span className="mono">
          {item.width && item.height ? `${item.width}×${item.height}` : item.root ?? ""}
        </span>
      </span>
    </button>
    <span className="showcase-tools">
      <button type="button" className="btn btn-secondary btn-icon"
              aria-label={t("Discuss {title}", { title: item.title })} title={t("Discuss")} onClick={onDiscuss}>
        <ChatPlusGlyph />
      </button>
      <button type="button" className="btn btn-secondary btn-icon showcase-delete"
              aria-label={t("Delete {title}", { title: item.title })} title={t("Delete")} onClick={onDelete}>
        <TrashGlyph />
      </button>
    </span>
    </div>
  );
}

/** The agent conversation on an element: the brief is written, then the chosen agent receives it. */
function ShowcaseHandoff({ project, kind, item, onClose }: {
  project: string;
  kind: ShowcaseKind;
  item: ShowcaseItem;
  onClose: () => void;
}) {
  return (
    <HandoffDialog<ShowcaseBrief>
      title={t("Discuss this element")}
      eyebrow={item.title}
      load={() => api.showcaseBrief(project, kind, item.id)}
      rows={(brief) => [
        [t("Showcase"), brief.section_label],
        [t("Family"), brief.family],
        [t("File"), <span className="mono">{item.file}</span>],
        [t("Uses"), <span className="num">{item.users.length}</span>],
      ]}
      submit={(choice) => api.showcaseHandoff(project, kind, item.id, choice)}
      sent={(session) => t("{title} handed to {title2}", { title: item.title, title2: session.title })}
      onClose={onClose}
    />
  );
}

/** An icon, at the chosen display size, with silhouette and guides. */
function IconView({ project, kind, item, size, look }: {
  project: string;
  kind: ShowcaseKind;
  item: ShowcaseItem;
  size: number;
  look: Look;
}) {
  const [broken, setBroken] = useState(false);
  const filter = look.silhouette ? (darkBackdrop(look) ? "brightness(0) invert(1)" : "brightness(0)") : undefined;
  return (
    <span className="showcase-icon" style={{ width: size, height: size }}>
      {broken ? (
        <span className="mono showcase-missing">{item.format}</span>
      ) : (
        <img src={showcaseImageUrl(project, kind, item.id)} alt={item.title}
             draggable={false} onError={() => setBroken(true)}
             style={{ filter, imageRendering: look.crisp ? "pixelated" : undefined }} />
      )}
      {look.keylines && <Keylines />}
    </span>
  );
}

/** An icon's guides: its axes, its safe zone, its circle. */
function Keylines() {
  return (
    <svg className="showcase-keylines" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 0v24M0 12h24" />
      <rect x="2" y="2" width="20" height="20" />
      <circle cx="12" cy="12" r="10" />
    </svg>
  );
}

/** A prop in one state, at its game pixels times the zoom. */
function PropView({ project, item, state, zoom, keylines }: {
  project: string;
  item: ShowcaseItem;
  state: ShowcaseState;
  zoom: number;
  keylines: boolean;
}) {
  const drawn = item.states.find((entry) => entry.state === state);
  if (!drawn) return null;
  return (
    <img
      className={`showcase-prop ${keylines ? "has-keylines" : ""}`}
      src={showcaseImageUrl(project, "props", item.id, state, item.rendered_at ?? "")}
      alt={`${item.title} — ${STATE_LABELS[state]}`}
      draggable={false}
      style={{ width: (drawn.width / PROP_SCALE) * zoom }}
    />
  );
}

/* ----------------------------------------------------------- an element's room */

function Inspector({ project, kind, data, id, order, look, paused, onMove, onClose, onDiscuss, onRedo,
                 onDelete }: {
  project: string;
  kind: ShowcaseKind;
  data: Showcase;
  id: string;
  /** The elements shown, in page order: ← → move from one to the next. */
  order: string[];
  look: Look;
  /** A conversation is open on top: the keyboard belongs to it. */
  paused: boolean;
  onMove: (id: string) => void;
  onClose: () => void;
  onDiscuss: (item: ShowcaseItem) => void;
  onRedo: (item: ShowcaseItem) => void;
  onDelete: (item: ShowcaseItem) => void;
}) {
  const family = data.families.find((entry) => entry.items.some((item) => item.id === id));
  const item = family?.items.find((entry) => entry.id === id);
  const at = order.indexOf(id);

  useEffect(() => {
    if (paused) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement) return;
      if (event.key === "ArrowRight" && at >= 0 && at < order.length - 1) onMove(order[at + 1]!);
      if (event.key === "ArrowLeft" && at > 0) onMove(order[at - 1]!);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paused, at, order, onMove, onClose]);

  if (!item || !family) return null;
  const bg = backdropOf(look, data);
  const facts: [string, React.ReactNode][] = [
    [t("File"), <span className="mono">{item.file}</span>],
    ...(item.res_path ? [[t("Scene"), <span className="mono">{item.res_path}</span>] as [string, React.ReactNode]] : []),
    ...(item.root ? [[t("Root"), <span className="mono">{item.root}</span>] as [string, React.ReactNode]] : []),
    ...(item.width && item.height
      ? [[t("Source size"), <span className="num">{item.width} × {item.height}</span>] as [string, React.ReactNode]]
      : []),
    [t("Uses"), item.users.length
      ? <span className="showcase-users">{item.users.map((user) => <span key={user} className="mono">{user}</span>)}</span>
      : <span className="mono">—</span>],
  ];

  return (
    <div className="showcase-layer" role="dialog" aria-modal="true" aria-label={item.title}>
      <div className="showcase-inspect">
        <div className="showcase-inspect-stage">
          <header className="showcase-inspect-head">
            <div>
              <h2>{item.title}</h2>
              <p className="mono">{tr(family.label)} · {at + 1} / {order.length}</p>
            </div>
            <div className="showcase-inspect-nav">
              <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Previous")}
                      disabled={at <= 0} onClick={() => onMove(order[at - 1]!)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
              </button>
              <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Next")}
                      disabled={at < 0 || at >= order.length - 1} onClick={() => onMove(order[at + 1]!)}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
              </button>
              <button type="button" className="btn btn-ghost btn-icon" aria-label={t("Close")} onClick={onClose}>
                <CloseCross />
              </button>
            </div>
          </header>

          {item.type === "image" ? (
            <>
              <div className={`showcase-hero ${bg.className}`} style={bg.style}>
                <IconView project={project} kind={kind} item={item} size={kind === "icons" ? 192 : 320}
                          look={look} />
              </div>
              {kind === "icons" && (
                <>
                  <section className="showcase-ladder">
                    {(["dark", "light"] as const).map((backdrop) => (
                      <div key={backdrop} className={`showcase-bg is-${backdrop}`}>
                        {LADDER.map((size) => (
                          <span key={size} className="showcase-rung">
                            <IconView project={project} kind={kind} item={item} size={size}
                                      look={{ ...look, backdrop, keylines: false }} />
                            <span className="num">{size}</span>
                          </span>
                        ))}
                      </div>
                    ))}
                  </section>
                  <section className={`showcase-siblings ${bg.className}`} style={bg.style}>
                    {family.items.map((sibling) => (
                      <button key={sibling.id} type="button" title={sibling.title}
                              className={sibling.id === id ? "is-current" : ""}
                              onClick={() => onMove(sibling.id)}>
                        <IconView project={project} kind={kind} item={sibling} size={40}
                                  look={{ ...look, keylines: false }} />
                      </button>
                    ))}
                  </section>
                </>
              )}
            </>
          ) : item.states.length === 0 ? (
            <Empty title={item.error || t("Not drawn yet")} />
          ) : (
            <div className={`showcase-hero is-states ${bg.className}`} style={bg.style}>
              {item.states.map((entry) => (
                <figure key={entry.state} className="showcase-state">
                  <PropView project={project} item={item} state={entry.state}
                            zoom={Number(look.zoom) * 1.5} keylines={look.keylines} />
                  <figcaption className={`showcase-state-label ${entry.same ? "is-same" : ""}`}>
                    {STATE_LABELS[entry.state]}
                  </figcaption>
                  {entry.same && <Badge tone="warn">{t("same as Normal")}</Badge>}
                </figure>
              ))}
            </div>
          )}
          {item.error && item.states.length > 0 && <Badge tone="danger">{tr(item.error)}</Badge>}
        </div>

        <aside className="showcase-inspect-side">
          <div className="showcase-inspect-gestures">
            {kind === "icons" && (
              <button type="button" className="btn btn-primary" onClick={() => onRedo(item)}>
                <RedrawGlyph />
                {t("Redo")}
              </button>
            )}
            <button type="button" className={`btn ${kind === "icons" ? "btn-secondary" : "btn-primary"}`}
                    onClick={() => onDiscuss(item)}>
              <ChatPlusGlyph />
              {t("Discuss")}
            </button>
            <button type="button" className="btn btn-danger" onClick={() => onDelete(item)}>
              <TrashGlyph />
              {t("Delete")}
            </button>
          </div>
          <Facts rows={facts} />
        </aside>
      </div>
    </div>
  );
}
