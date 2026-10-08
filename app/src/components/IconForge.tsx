/**
 * The icon forge in the showcase: create, redo, split, adopt, and remove.
 *
 * The same path an agent follows (`service/forge.py`): a paid request,
 * confirmed with its amount; candidates to choose from; for a set, the chosen
 * sheet split into named icons; then adoption, which cuts out, scales to the
 * family and writes into the game folder. Removing an element or a family goes
 * through the project trash, from which it can come back.
 */

import { useMemo, useState, type ReactNode } from "react";
import {
  api, forgeImageUrl, showcaseImageUrl, type ForgeRequest, type ForgeFamily, type ForgeMode, type ShowcaseItem,
  type ShowcaseKind, type TrashBatch,
} from "../api";
import { IMAGE_MODELS } from "../lib/catalog";
import { useForge, useForgeFamilies, useGameGesture, useShowcase, useTrash } from "../lib/queries";
import ModelPicker from "./ModelPicker";
import { useStudio } from "../lib/store";
import {
  Badge, Callout, Dialog, Empty, Facts, Field, Select, State, cost, shortDate,
} from "./ui";
import { t, tn, tr } from "../lib/i18n";

/* -------------------------------------------------------------------- glyphs */

/** The trash: remove from the game. */
export function TrashGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10 10.5v6M14 10.5v6" />
    </svg>
  );
}

/** The "+": create. */
export function PlusGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5.5v13M5.5 12h13" />
    </svg>
  );
}

/** Several "+": create several icons at once. */
export function PlusManyGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 8.5v11M3.5 14h11M18 3.5v7M14.5 7h7" />
    </svg>
  );
}

/** The section's written cards. */
export function CardsGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3.5h12a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5zM8.5 8.5h7M8.5 12h7M8.5 15.5h4" />
    </svg>
  );
}

/** Two arrows in a circle: redraw everything. */
export function RedrawAllGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M19.5 12a7.5 7.5 0 0 1-12.8 5.3M4.5 12a7.5 7.5 0 0 1 12.8-5.3M17.5 3.5v3.5H14M6.5 20.5V17H10" />
    </svg>
  );
}

/** The brush: redo an icon. */
export function RedrawGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14.5 4.5l5 5-9 9H5.5v-5zM12.5 6.5l5 5" />
    </svg>
  );
}

/* ------------------------------------------------------------------- request */

const MODE_TITLES: Record<ForgeMode, string> = {
  one: t("New icon"),
  set: t("Several icons"),
  redo: t("Redo the icon"),
};

/** The price of a one-megapixel image, and what a larger sheet costs. */
function priceOf(model: string, count: number, mode: ForgeMode, names: number): number {
  const usd = IMAGE_MODELS.find((entry) => entry.air === model)?.usd ?? 0;
  const area = mode === "set" && names > 9 ? 2.25 : 1;
  return usd * count * area;
}

const NEW_FAMILY = "\u0000new";

export function ForgeDialog({ project, mode, item, folder, onClose }: {
  project: string;
  mode: ForgeMode;
  /** The icon to redo. */
  item?: ShowcaseItem;
  /** The family chosen beforehand (a family's "+"). */
  folder?: string;
  onClose: () => void;
}) {
  const { notify } = useStudio();
  const { data: families } = useForgeFamilies(project);
  const { data: showcase } = useShowcase(project, "icons");
  const itemFolder = item ? item.file.split("/").slice(0, -1).join("/") : "";
  const [chosen, setChosen] = useState(folder ?? itemFolder);
  const [newFolder, setNewFolder] = useState("");
  const [name, setName] = useState("");
  const [names, setNames] = useState("");
  const [description, setDescription] = useState("");
  const [style, setStyle] = useState<string | null>(null);
  const [model, setModel] = useState(IMAGE_MODELS[0]!.air);
  const [count, setCount] = useState(mode === "set" ? "1" : "2");
  const [reference, setReference] = useState<"family" | "element" | "none">(
    mode === "redo" ? "element" : "family");
  const send = useGameGesture(project, () => api.forgeRequest(project, {
    mode, folder: target, names: list, description, style: style ?? undefined, model,
    count: Number(count), reference, element: item?.id, confirm: true,
  }));

  const family: ForgeFamily | undefined = families?.find((entry) =>
    entry.folder === (chosen === NEW_FAMILY ? "" : chosen || families[0]?.folder));
  const target = mode === "redo" ? itemFolder
    : chosen === NEW_FAMILY ? newFolder.trim().replace(/^\/+|\/+$/g, "") : (chosen || family?.folder || "");
  const list = mode === "set"
    ? names.split(/\n|,/).map((entry) => entry.trim()).filter(Boolean)
    : mode === "one" ? [name.trim()].filter(Boolean) : [];
  const shownStyle = style ?? family?.profile.style ?? "";
  const total = priceOf(model, Number(count), mode, list.length);
  const ready = Boolean(target) && (mode === "redo" || (mode === "one"
    ? list.length === 1 : list.length >= 2 && list.length <= 16)) && !send.isPending;
  const profile = family?.profile;

  // What the showcase already shows of the family: its icons, and the one used
  // as a starting point (`profile.exemplar`).
  const members = showcase?.families.find((entry) => entry.folder === target)?.items ?? [];
  const exemplar = members.find((entry) => entry.file === profile?.exemplar);
  const imageOf = (entry: ShowcaseItem) => showcaseImageUrl(project, "icons", entry.id);
  const start = reference === "element" ? item : reference === "family" ? exemplar : undefined;
  const images = Number(count);

  const familyOptions = [
    ...(families ?? []).map((entry) => ({
      value: entry.folder, label: entry.label,
      detail: <span className="mono">{entry.folder}/</span>,
    })),
    { value: NEW_FAMILY, label: t("New family…"), divider: true },
  ];

  const starts: { value: "family" | "element" | "none"; label: string; image?: ShowcaseItem }[] = [
    ...(mode === "redo" && item ? [{ value: "element" as const, label: t("The icon"), image: item }] : []),
    { value: "family", label: t("The family"), image: exemplar },
    { value: "none", label: t("None") },
  ];

  return (
    <Dialog
      wide
      title={MODE_TITLES[mode]}
      eyebrow={item?.title}
      onClose={onClose}
      foot={(
        <>
          {send.isError && <Badge tone="danger">{(send.error as Error).message}</Badge>}
          <button type="button" className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!ready}
            onClick={() => send.mutate(undefined, {
              onSuccess: () => {
                notify({ kind: "success", title: t("{mode}: request sent to Runware", { mode: MODE_TITLES[mode] }) });
                onClose();
              },
            })}
          >
            {send.isPending ? t("sending…") : t("Pay {total} and start", { total: cost(total) })}
          </button>
        </>
      )}
    >
      <div className="forge">
        <aside className="forge-preview">
          <div className="forge-flow board check">
            <figure className="forge-slot">
              {start ? <img src={imageOf(start)} alt={start.title} draggable={false} />
                : <span className="forge-empty"><EmptyGlyph /></span>}
              <figcaption>{start ? t("start") : t("no start")}</figcaption>
            </figure>
            <span className="forge-arrow" aria-hidden="true"><ArrowGlyph /></span>
            <figure className="forge-slot is-new">
              <span className="forge-empty">
                <SparkGlyph />
                {images > 1 && <span className="forge-times num">×{images}</span>}
              </span>
              <figcaption className="mono">
                {mode === "redo" ? item?.title : mode === "one" ? (list[0] || "…") : t("{length} icons", { length: list.length })}
              </figcaption>
            </figure>
          </div>

          <section className="forge-family">
            <header>
              <span className="forge-family-name">{family?.label ?? (newFolder || t("New family"))}</span>
              <span className="num">{members.length}</span>
            </header>
            {members.length > 0 ? (
              <ul className="forge-members board">
                {members.slice(0, 15).map((entry) => (
                  <li key={entry.id} className={entry.id === start?.id ? "is-start" : ""} title={entry.title}>
                    <img src={imageOf(entry)} alt={entry.title} draggable={false} />
                  </li>
                ))}
                {mode === "one" && <li className="is-new" title={list[0] || undefined}><PlusGlyph /></li>}
              </ul>
            ) : (
              <p className="forge-family-none">{t("No icon")}</p>
            )}
            {profile && (
              <Facts rows={[
                [t("Scale"), profile.mode === "framed"
                  ? <span className="num">{t("{width} × {height} · margin {round}%", { width: profile.width, height: profile.height, round: Math.round(profile.margin * 100) })}</span>
                  : <span className="num">{t("cropped · {width} px", { width: profile.width })}</span>],
                [t("Folder"), <span className="mono">{target}/</span>],
              ]} />
            )}
          </section>
        </aside>

        <div className="forge-steps">
          <ForgeStep n={1} glyph={<TagGlyph />} title={t("Subject")}>
            {mode !== "redo" && (
              <Field label={t("Family")}>
                <Select label={t("Family")} value={chosen || family?.folder || NEW_FAMILY}
                        options={familyOptions} onChange={setChosen} />
              </Field>
            )}
            {chosen === NEW_FAMILY && mode !== "redo" && (
              <Field label={t("Folder, from the game root")}>
                <input className="mono" value={newFolder} placeholder="client/assets/icons/new-family"
                       onChange={(event) => setNewFolder(event.target.value)} />
              </Field>
            )}
            {mode === "one" && (
              <Field label={t("File name")}>
                <input className="mono" value={name} placeholder="shield"
                       onChange={(event) => setName(event.target.value)} />
              </Field>
            )}
            {mode === "set" && (
              <Field label={t("Names, one per line · {length} / 16", { length: list.length })}>
                <textarea className="mono" rows={4} value={names} placeholder={"fire\nwater\nearth\nair"}
                          onChange={(event) => setNames(event.target.value)} />
              </Field>
            )}
            <Field label={mode === "redo" ? t("What changes") : t("What it shows")}>
              <textarea rows={2} value={description}
                        placeholder={mode === "redo" ? t("sharper facets, brighter purple") : t("a round wooden shield")}
                        onChange={(event) => setDescription(event.target.value)} />
            </Field>
          </ForgeStep>

          <ForgeStep n={2} glyph={<BrushGlyph />} title={t("Style")}>
            <textarea rows={2} value={shownStyle} aria-label={t("Family style")}
                      placeholder={t("glossy cartoon, thick dark outline, soft top light")}
                      onChange={(event) => setStyle(event.target.value)} />
            <div className="forge-starts" role="radiogroup" aria-label={t("Starting image")}>
              {starts.map((entry) => (
                <button key={entry.value} type="button" role="radio" aria-checked={reference === entry.value}
                        className={`forge-start${reference === entry.value ? " is-checked" : ""}`}
                        onClick={() => setReference(entry.value)}>
                  <span className="forge-start-art board check">
                    {entry.image ? <img src={imageOf(entry.image)} alt="" draggable={false} /> : <EmptyGlyph />}
                  </span>
                  <span className="forge-start-label">{entry.label}</span>
                </button>
              ))}
            </div>
          </ForgeStep>

          <ForgeStep n={3} glyph={<SparkGlyph />} title={t("Model")}
                     aside={(
                       <div className="forge-count" role="radiogroup" aria-label={mode === "set" ? t("Sheets") : t("Variants")}>
                         <span className="tool-label">{mode === "set" ? t("Sheets") : t("Variants")}</span>
                         {(mode === "set" ? ["1", "2"] : ["1", "2", "3", "4"]).map((value) => (
                           <button key={value} type="button" role="radio" aria-checked={count === value}
                                   className={count === value ? "is-checked" : ""} onClick={() => setCount(value)}>
                             <span className="forge-pips" aria-hidden="true">
                               {Array.from({ length: Number(value) }, (_, index) => <i key={index} />)}
                             </span>
                             <span className="num">{value}</span>
                           </button>
                         ))}
                       </div>
                     )}>
            <ModelPicker models={IMAGE_MODELS} value={model} onChange={setModel} />
          </ForgeStep>
        </div>
      </div>
    </Dialog>
  );
}

/** A step of the request: its rank, its glyph, its title, and what is set to the right of the title. */
function ForgeStep({ n, glyph, title, aside, children }: {
  n: number;
  glyph: ReactNode;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="forge-step">
      <header className="forge-step-head">
        <span className="forge-step-mark" aria-hidden="true">{glyph}</span>
        <span className="forge-step-n num">{String(n).padStart(2, "0")}</span>
        <h3>{title}</h3>
        {aside && <div className="forge-step-aside">{aside}</div>}
      </header>
      <div className="forge-step-body">{children}</div>
    </section>
  );
}

function TagGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 4.5h7.5l8.5 8.5-7 7-8.5-8.5V4.5zM8.5 8.5v.01" />
    </svg>
  );
}

function BrushGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M19.5 4.5l-8 8M13.5 14.5l-4-4M9.5 13c-2.5 0-4 1.5-4 4 0 1.2-.6 2.2-2 2.5 4 1.5 8-.5 8-4.5" />
    </svg>
  );
}

function SparkGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3.5c.6 4.2 2.3 5.9 6.5 6.5-4.2.6-5.9 2.3-6.5 6.5-.6-4.2-2.3-5.9-6.5-6.5 4.2-.6 5.9-2.3 6.5-6.5z" />
      <path d="M18.5 15.5v4M16.5 17.5h4" />
    </svg>
  );
}

function ArrowGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 12h15M14 6.5l5.5 5.5-5.5 5.5" />
    </svg>
  );
}

/** A crossed-out frame: no image. */
function EmptyGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 5h14v14H5zM5 19L19 5" />
    </svg>
  );
}

/* ---------------------------------------------------------------- candidates */

/** The running or ready requests: choose, split, adopt. */
export function ForgePanel({ project }: { project: string }) {
  const { data: requests } = useForge(project);
  if (!requests?.length) return null;
  return (
    <section className="forge-panel">
      {requests.map((request) => <RequestCard key={request.id} project={project} request={request} />)}
    </section>
  );
}

const STATUS: Record<ForgeRequest["status"], { state: string; label: string }> = {
  running: { state: "running", label: t("generating…") },
  ready: { state: "needs_review", label: t("to choose") },
  split: { state: "needs_review", label: t("to choose") },
  adopted: { state: "done", label: t("in the game") },
  failed: { state: "failed", label: t("failed") },
  empty: { state: "failed", label: t("no image") },
};

function RequestCard({ project, request }: { project: string; request: ForgeRequest }) {
  const [picked, setPicked] = useState<string | null>(null);
  const [name, setName] = useState(request.names[0] ?? "");
  const [pieces, setPieces] = useState<Record<number, string | null>>({});
  const close = useGameGesture(project, () => api.forgeClose(project, request.id));
  const split = useGameGesture(project, (asset: string) => api.forgeSplit(project, request.id, asset));
  const adopt = useGameGesture(project, (picks: { source: string; name: string }[]) =>
    api.forgeAdopt(project, request.id, picks));
  const status = STATUS[request.status];
  const title = request.mode === "redo"
    ? t("Redo “{names}”", { names: request.names[0] ?? "" })
    : request.mode === "set"
      ? t("{length} icons", { length: request.names.length })
      : t("“{name}”", { name: request.names[0] ?? "" });

  // The pieces of a split sheet are all kept by default, under their cell's
  // name, except those already in the game.
  const written = new Set(request.adopted.map((entry) => entry.name));
  const chosenPieces = useMemo(() => (request.split?.pieces ?? [])
    .filter((piece) => !written.has(piece.name))
    .map((piece) => ({ piece, name: pieces[piece.index] === undefined ? piece.name : pieces[piece.index] }))
    .filter((entry) => entry.name !== null) as { piece: { index: number }; name: string }[],
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [request.split, request.adopted, pieces]);
  const busy = split.isPending || adopt.isPending;
  const failure = [split, adopt, close].find((gesture) => gesture.isError)?.error as Error | undefined;

  return (
    <article className="forge-request">
      <header className="forge-request-head">
        <b>{title}</b>
        <span className="mono">{request.folder}/</span>
        <State state={status.state} label={status.label} />
        <span className="num">{cost(request.estimate_usd)}</span>
        <span className="spacer" />
        <button type="button" className="btn btn-ghost btn-sm" disabled={close.isPending}
                onClick={() => close.mutate(undefined)}>
          {t("Archive")}
        </button>
      </header>
      {request.status === "failed" && <Callout>{request.error ? tr(request.error) : t("the generation failed")}</Callout>}
      {request.notes.map((note) => <Badge key={note} tone="warn">{tr(note)}</Badge>)}
      {failure && <Badge tone="danger">{failure.message}</Badge>}

      {request.split ? (
        <>
          <ul className="forge-pieces">
            {request.split.pieces.map((piece) => {
              const done = written.has(piece.name);
              const current = done ? piece.name
                : pieces[piece.index] === undefined ? piece.name : pieces[piece.index];
              return (
                <li key={piece.index} className={current === null ? "is-off" : done ? "is-done" : ""}>
                  <button type="button" className="forge-thumb board check" disabled={done}
                          onClick={() => setPieces((all) => ({ ...all, [piece.index]: current === null ? piece.name : null }))}>
                    <img src={forgeImageUrl(project, request.id, { piece: piece.index })} alt={piece.name} />
                  </button>
                  <input className="mono" value={current ?? ""} disabled={current === null || done}
                         onChange={(event) => setPieces((all) => ({ ...all, [piece.index]: event.target.value }))} />
                </li>
              );
            })}
          </ul>
          {request.split.warnings.map((warning) => <Badge key={warning} tone="warn">{tr(warning)}</Badge>)}
          <div className="forge-actions">
            <button type="button" className="btn btn-primary btn-sm"
                    disabled={busy || chosenPieces.length === 0}
                    onClick={() => adopt.mutate(chosenPieces.map((entry) =>
                      ({ source: `piece:${entry.piece.index}`, name: entry.name })))}>
              {adopt.isPending ? t("writing…") : tn(chosenPieces.length, "Add {n} icon to the game", "Add {n} icons to the game")}
            </button>
          </div>
        </>
      ) : request.status === "running" ? (
        <ul className="forge-candidates">
          {Array.from({ length: request.count }, (_, index) => (
            <li key={index}><span className="forge-thumb is-waiting" /></li>
          ))}
        </ul>
      ) : request.candidates.length > 0 ? (
        <>
          <ul className="forge-candidates">
            {request.candidates.map((candidate) => (
              <li key={candidate.asset_id}>
                <button type="button"
                        className={`forge-thumb board ${picked === candidate.asset_id ? "is-picked" : ""}`}
                        aria-pressed={picked === candidate.asset_id}
                        onClick={() => setPicked(candidate.asset_id)}>
                  <img src={forgeImageUrl(project, request.id, { asset: candidate.asset_id })} alt="" />
                </button>
              </li>
            ))}
          </ul>
          <div className="forge-actions">
            {request.mode === "set" ? (
              <button type="button" className="btn btn-primary btn-sm" disabled={!picked || busy}
                      onClick={() => picked && split.mutate(picked)}>
                {split.isPending ? t("splitting…") : t("Split the sheet")}
              </button>
            ) : (
              <>
                {request.mode === "one" && (
                  <input className="mono forge-name" value={name}
                         onChange={(event) => setName(event.target.value)} />
                )}
                <button type="button" className="btn btn-primary btn-sm" disabled={!picked || busy || !name.trim()}
                        onClick={() => picked && adopt.mutate([{ source: `asset:${picked}`, name: name.trim() }])}>
                  {adopt.isPending ? t("cutting out…") : request.mode === "redo" ? t("Replace the icon") : t("Add to the game")}
                </button>
              </>
            )}
          </div>
        </>
      ) : null}
      {request.adopted.length > 0 && (
        <p className="forge-written">
          {request.adopted.map((entry) => <span key={entry.file} className="mono">{entry.file}</span>)}
        </p>
      )}
    </article>
  );
}

/* ---------------------------------------------------------- remove, restore */

/** Removing an element or a family from the game: the gesture, confirmed, to the trash. */
export function DeleteDialog({ project, kind, item, family, onClose, onDeleted }: {
  project: string;
  kind: ShowcaseKind;
  item?: ShowcaseItem;
  family?: { id: string; label: string; folder: string; items: ShowcaseItem[] };
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const { notify } = useStudio();
  const remove = useGameGesture(project, () => item
    ? api.deleteShowcase(project, kind, item.id)
    : api.deleteShowcaseFamily(project, kind, family!.id));
  const files = item ? [item.file] : family!.items.map((entry) => entry.file);
  const users = [...new Set((item ? [item] : family!.items).flatMap((entry) => entry.users))]
    .filter((user) => !files.includes(user));
  const title = item ? t("Delete “{title}”?", { title: item.title }) : t("Delete the “{label}” family?", { label: family!.label });

  return (
    <Dialog
      title={title}
      eyebrow={item ? item.file : t("{family} · {n} file(s)", { family: family!.folder || family!.label, n: files.length })}
      onClose={onClose}
      foot={(
        <>
          {remove.isError && <Badge tone="danger">{(remove.error as Error).message}</Badge>}
          <button type="button" className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button type="button" className="btn btn-danger" disabled={remove.isPending}
                  onClick={() => remove.mutate(undefined, {
                    onSuccess: () => {
                      notify({ kind: "success", title: t("{name}: in the project trash", { name: item ? item.title : family!.label }) });
                      onDeleted?.();
                      onClose();
                    },
                  })}>
            {remove.isPending ? t("deleting…") : t("Delete")}
          </button>
        </>
      )}
    >
      <Facts rows={[
        [t("Files"), <span className="num">{files.length}</span>],
        [t("Uses"), users.length
          ? <span className="showcase-users">{users.map((user) => <span key={user} className="mono">{user}</span>)}</span>
          : <span className="mono">—</span>],
      ]} />
      {users.length > 0 && <Callout>{t("These files will load a missing file.")}</Callout>}
      <Callout>{t("Removed from the game, kept in the project trash: it can be put back as it was.")}</Callout>
    </Dialog>
  );
}

/** The project trash: each removed batch, and the gesture that puts it back. */
export function TrashDialog({ project, onClose }: { project: string; onClose: () => void }) {
  const { data: batches } = useTrash(project);
  const restore = useGameGesture(project, (batch: string) => api.restoreTrash(project, batch));
  return (
    <Dialog wide title={t("Project trash")} onClose={onClose}>
      {restore.isError && <Badge tone="danger">{(restore.error as Error).message}</Badge>}
      {!batches?.length ? (
        <Empty title={t("Empty trash")} />
      ) : (
        <ul className="forge-trash">
          {batches.map((batch: TrashBatch) => (
            <li key={batch.id}>
              <div>
                <b>{tr(batch.label)}</b>
                <span className="num">{shortDate(batch.at)}</span>
                <span className="mono">{batch.files.filter((file) => !file.endsWith(".import")).join(", ")}</span>
              </div>
              <button type="button" className="btn btn-secondary btn-sm" disabled={restore.isPending}
                      onClick={() => restore.mutate(batch.id)}>
                {t("Put back")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

/** The number of trash batches, for the button that opens it. */
export function useTrashCount(project: string): number {
  return useTrash(project).data?.length ?? 0;
}
