/**
 * Picking an image for a comparator slot.
 *
 * Four origins, because it is four times the same question: what just came
 * out (store outputs, grouped by folder), a file filed in the project library
 * (browsed by folder), a sprite sheet rendered from a mesh, or a brief image
 * (those a brief produced to illustrate a section's cards, grouped by
 * section). Only one tab is mounted at a time: the library resyncs on every
 * read, and paying for four disk walks to look at one would be wasted work.
 *
 * The image goes whole (`assetFileUrl`), not as a thumbnail: pixels are
 * compared, and a shrunk thumbnail has already lost what one is looking for.
 */

import { useMemo, useState, type ReactNode } from "react";
import { assetFileUrl, thumbUrl, type Asset, type LibraryFile, type LibraryTree } from "../api";
import { useTree } from "../lib/queries";
import { Empty, Panel, Seg, shortDate } from "./ui";
import type { CompareSource } from "./CompareStage";
import { t, tn } from "../lib/i18n";

type Origin = "recent" | "library" | "sprite" | "briefing";

const ORIGINS: { value: Origin; label: string }[] = [
  { value: "recent", label: t("Recent") },
  { value: "library", label: t("Library") },
  { value: "sprite", label: t("Sprites") },
  { value: "briefing", label: t("Briefing") },
];

/** What the comparator can read: the rest (mesh, clip, .tscn) is not an image. */
export function isImage(file: { mime: string | null; name: string }): boolean {
  if (file.mime?.startsWith("image/")) return true;
  return /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(file.name);
}

/** A store output, seen as a comparator source. */
export function sourceFromAsset(asset: Asset): CompareSource {
  return {
    assetId: asset.id,
    name: shortDate(asset.created_at),
    origin: t("store · {slice}", { slice: asset.id.slice(0, 12) }),
    url: assetFileUrl(asset.id),
  };
}

function fromFile(file: LibraryFile): CompareSource {
  return {
    assetId: file.asset_id,
    name: file.name,
    origin: t("library · {folder}", { folder: file.folder || t("root") }),
    url: assetFileUrl(file.asset_id),
  };
}

/* ---------------------------------------------------------------- grid */

interface Entry {
  source: CompareSource;
  thumb: string;
  cap: string;
}

/** The same grid for every origin: a thumbnail, a name, a click. */
function Grid({ entries, selected, hidden, onPick, small = false, cap }: {
  entries: Entry[];
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
  /** A folder grid: shorter thumbnails, and a cap. */
  small?: boolean;
  cap?: number;
}) {
  // The image already chosen in the other slot is not offered here: two
  // images are compared, not one image with itself.
  let shown = hidden ? entries.filter((entry) => entry.source.assetId !== hidden) : entries;
  // The cap counts after that removal: a folder does not announce an image it
  // does not show.
  const rest = cap ? Math.max(0, shown.length - cap) : 0;
  if (cap) shown = shown.slice(0, cap);
  return (
    <div className={small ? "cmp-grid cmp-grid-sm" : "cmp-grid"}>
      {shown.map((entry) => (
        <button
          className="ref"
          key={entry.source.assetId + entry.source.name}
          aria-pressed={selected === entry.source.assetId}
          title={entry.source.origin}
          onClick={() => onPick(entry.source)}
        >
          <span className="thumb">
            <img src={entry.thumb} alt="" loading="lazy" />
          </span>
          <span className="cap">{entry.cap}</span>
        </button>
      ))}
      {rest > 0 && <span className="hint cmp-rest">+{rest}</span>}
    </div>
  );
}

/**
 * A folded folder: its name, its count, its content when unfolded.
 *
 * It organizes an origin without giving it the library's full navigation: the
 * groups fold (`<details>`), and the first one is open to show that they open.
 * Thumbnails carry the date, so the order stays most recent first.
 */
function FolderGroup({ path, entries, selected, hidden, onPick, open = false }: {
  path: string;
  entries: Entry[];
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
  open?: boolean;
}) {
  return (
    <details className="cmp-group" open={open}>
      <summary>
        <span className="mono grow truncate">{path || t("root")}</span>
        <span className="n">{tn(entries.length, "{n} image", "{n} images")}</span>
      </summary>
      <Grid entries={entries} selected={selected} hidden={hidden} onPick={onPick} small cap={12} />
    </details>
  );
}

/* -------------------------------------------------------------- origins */

/** The images the library files, with their folder, from newest to oldest. */
function useLibraryImages(project: string, depth = 6) {
  const { data: tree, isLoading } = useTree(project, "", depth, "", 1000);
  const files = useMemo(() => (tree?.files ?? []).filter(isImage), [tree]);
  return { tree, files, isLoading };
}

/** Each image's folder, in arrival order: most recent first. */
function byFolder(tree: LibraryTree | undefined, files: LibraryFile[]) {
  const place = new Map((tree?.files ?? []).map((file) => [file.asset_id, file.folder]));
  const groups = new Map<string, LibraryFile[]>();
  files.forEach((file) => {
    // An asset the index files nowhere: it exists, but no folder claims it.
    const path = place.get(file.asset_id) ?? "";
    const held = groups.get(path);
    if (held) held.push(file);
    else groups.set(path, [file]);
  });
  // What is filed nowhere goes last: the question is "what just came out, and
  // from where".
  return [...groups.entries()].sort(
    (left, right) => Number(left[0] === "") - Number(right[0] === ""),
  );
}

const fileEntry = (file: LibraryFile): Entry => ({
  source: fromFile(file),
  thumb: thumbUrl(file, 200),
  cap: file.name,
});

/**
 * Store outputs, grouped by library folder: what just came out, and from
 * where. The list comes from the library (the actual filing), not from the
 * store, where nothing says where an asset was filed.
 */
function Recent({ project, selected, hidden, onPick }: {
  project: string;
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
}) {
  const { tree, files, isLoading } = useLibraryImages(project);
  if (isLoading) return <div className="skeleton" style={{ height: 160 }} />;
  if (!files.length) return <Empty title={t("No image in the library")} />;

  const groups = byFolder(tree, files);
  return (
    <div className="col gap-3">
      {groups.map(([path, held], index) => (
        <FolderGroup
          key={path || "unsorted"}
          path={path || t("unsorted")}
          entries={held.map(fileEntry)}
          selected={selected}
          hidden={hidden}
          onPick={onPick}
          open={index === 0}
        />
      ))}
    </div>
  );
}

function Library({ project, selected, hidden, onPick }: {
  project: string;
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
}) {
  const [folder, setFolder] = useState("");
  const { data: tree, isLoading } = useTree(project, folder, 1);
  const crumbs = folder ? folder.split("/") : [];

  if (isLoading) return <div className="skeleton" style={{ height: 160 }} />;

  const entries = (tree?.files ?? []).filter(isImage).map((file) => ({
    source: fromFile(file),
    thumb: thumbUrl(file, 200),
    cap: file.name,
  }));
  const folders = tree?.folders ?? [];

  return (
    <div className="col gap-4">
      <nav className="crumbs" aria-label={t("Current folder")}>
        <button onClick={() => setFolder("")}><b>{project || "—"}</b></button>
        {crumbs.map((part, index) => (
          <span className="row" key={part + index} style={{ gap: 2 }}>
            <span>/</span>
            <button onClick={() => setFolder(crumbs.slice(0, index + 1).join("/"))}>{part}</button>
          </span>
        ))}
      </nav>

      {folders.length > 0 && (
        <div className="cmp-folders">
          {folders.map((entry) => (
            <button className="chip" key={entry.path} onClick={() => setFolder(entry.path)}>
              {entry.name} <span className="n">{entry.files}</span>
            </button>
          ))}
        </div>
      )}

      {entries.length ? (
        <Grid entries={entries} selected={selected} hidden={hidden} onPick={onPick} />
      ) : (
        <Empty
          title={folders.length ? t("No image in this folder") : t("Folder without images")}
        />
      )}
    </div>
  );
}

function Sprites({ project, selected, hidden, onPick }: {
  project: string;
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
}) {
  // Sheets rendered from a mesh live under `3d/<entity>/sprites/`: one walk
  // finds them all, without querying each character.
  const { data: tree, isLoading } = useTree(project, "", 4, "sprites");
  if (isLoading) return <div className="skeleton" style={{ height: 160 }} />;

  const entries = (tree?.files ?? [])
    .filter((file) => file.folder.includes("sprites") && isImage(file))
    .map((file) => ({
      source: fromFile(file),
      thumb: thumbUrl(file, 200),
      cap: file.name,
    }));

  if (!entries.length) {
    return (
      <Empty
        title={t("No sprite sheet")}
      />
    );
  }
  return <Grid entries={entries} selected={selected} hidden={hidden} onPick={onPick} />;
}

/**
 * Brief images, grouped by section: what a brief had produced to illustrate a
 * section's cards (a screen, a panel, an entity), filed under
 * `briefing/<section>/` (`store/library.py:render_folder`).
 *
 * It is the "before / after" origin: the same card rendered twice, or a
 * screen's image and the concept that inspired it.
 */
function Briefings({ project, selected, hidden, onPick }: {
  project: string;
  selected: string | null;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
}) {
  // The folder is enough: every brief image is there, without going down into
  // concepts or sprites (depth 3, `briefing/<section>/<image>`).
  const { data: tree, isLoading } = useTree(project, "briefing", 3, "", 1000);
  if (isLoading) return <div className="skeleton" style={{ height: 160 }} />;

  const files = (tree?.files ?? []).filter(isImage);
  if (!files.length) {
    return <Empty title={t("No brief image")} />;
  }
  const groups = byFolder(tree, files);
  // A single section: its name says nothing the tab does not already say.
  const named = groups.length > 1;

  return (
    <div className="col gap-3">
      {groups.map(([path, held], index) => (
        <FolderGroup
          key={path}
          path={named ? path.replace(/^briefing\//, "") : ""}
          entries={held.map(fileEntry)}
          selected={selected}
          hidden={hidden}
          onPick={onPick}
          open={index === 0 || !named}
        />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- picker */

export function ComparePicker({ slot, source, project, hidden, onPick, onClear, note }: {
  slot: "A" | "B";
  source: CompareSource | null;
  project: string;
  hidden: string | null;
  onPick: (source: CompareSource) => void;
  onClear: () => void;
  note?: ReactNode;
}) {
  const [origin, setOrigin] = useState<Origin>("recent");

  return (
    <Panel
      eyebrow={t("Slot {slot}", { slot })}
      title={source ? source.name : t("empty")}
      actions={
        <>
          <Seg value={origin} options={ORIGINS} onChange={setOrigin} />
          {source && (
            <button className="btn btn-ghost btn-sm" onClick={onClear}>
              {t("Remove")}
            </button>
          )}
        </>
      }
      bodyClass="scroll"
      style={{ maxHeight: 420 }}
      foot={
        <span className="hint truncate" title={source?.origin ?? ""}>
          {source ? source.origin : t("No image chosen — click a thumbnail.")}
        </span>
      }
    >
      <div className="col gap-4">
        {note}
        {origin === "recent" && (
          <Recent project={project} selected={source?.assetId ?? null} hidden={hidden} onPick={onPick} />
        )}
        {origin === "library" && (
          <Library project={project} selected={source?.assetId ?? null} hidden={hidden} onPick={onPick} />
        )}
        {origin === "sprite" && (
          <Sprites project={project} selected={source?.assetId ?? null} hidden={hidden} onPick={onPick} />
        )}
        {origin === "briefing" && (
          <Briefings project={project} selected={source?.assetId ?? null} hidden={hidden} onPick={onPick} />
        )}
      </div>
    </Panel>
  );
}
