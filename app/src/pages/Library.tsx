/**
 * Library: everything the project produced, in 2D and in 3D.
 *
 * The readable mirror of the store, sorted by kind and by entity: a card's
 * concepts, its mesh, its sprites. An item held by a world card opens the step
 * of its workbench where it is taken up again; a mesh is viewed in 3D, not by
 * its extension.
 *
 * Brief images -- those `render_scene` produces to illustrate a section's
 * cards -- have their own group: they are filed under `briefing/<section>/`,
 * and an image is viewed large in a viewer.
 *
 * The layout is not edited by hand here: renaming, moving and deleting go
 * through the server's curation, which refuses to touch an asset held by a
 * character or a style pack -- and says why.
 */

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, assetFileUrl, assetPreviewUrl, thumbUrl, type LibraryFile, type Outcome } from "../api";
import { useEntities, useSyncLibrary, useTree } from "../lib/queries";
import { cardHref } from "./World";
import { useStudio } from "../lib/store";
import { inTauri, pickFile, writeClipboard } from "../lib/host";
import { useReveal } from "../lib/paths";
import {
  CloseCross, Dialog, Empty, Facts, Field, Menu, PageHeader, Panel, Seg, Select, Setting, Toggle, ToolGroup, Toolbar,
  bytes,
} from "../components/ui";
import { t, tn, tr } from "../lib/i18n";

// three.js loads only when a mesh is opened.
const MeshPreview = lazy(() => import("../components/MeshPreview"));

const BRIEFING = "briefing/";

/**
 * The kinds of files, in the order the page shows them: what goes into a game
 * first, raw material after. Each one recognizes itself by its library path --
 * the layout is described once (`Librarian.index_project`), the page only
 * reads it.
 */
const KINDS = [
  {
    key: "sketch", label: t("Concepts"), title: t("Concept"),
    match: (file: LibraryFile) => file.folder.includes("/concepts/"),
    open: "concepts", openLabel: t("Open the concepts"),
  },
  {
    key: "concept", label: t("References"), title: t("Reference image"),
    match: (file: LibraryFile) => /^[23]d\//.test(file.folder) && file.name.startsWith("concept"),
    open: "concepts", openLabel: t("Open the concepts"),
  },
  {
    key: "mesh", label: t("3D meshes"), title: t("3D mesh"),
    match: (file: LibraryFile) => /\.(glb|gltf)$/i.test(file.name),
    open: "3d", openLabel: t("Open in 3D"),
  },
  {
    key: "sprites", label: t("Sprites"), title: t("Sprites"),
    match: (file: LibraryFile) => file.folder.includes("/sprites"),
    open: "3d", openLabel: t("Open in 3D"),
  },
  {
    key: "generation", label: t("Loose images"), title: t("Generated image"),
    match: (file: LibraryFile) => file.folder.startsWith("generations/"),
    open: "compare", openLabel: t("Compare"),
  },
  {
    key: "briefing", label: t("Briefing"), title: t("Brief image"),
    match: (file: LibraryFile) => file.folder.startsWith(BRIEFING),
    open: "compare", openLabel: t("Compare"),
  },
  {
    key: "icon", label: t("Sheets"), title: t("Sheet element"),
    match: (file: LibraryFile) => file.folder.startsWith("icons/"),
    open: "compare", openLabel: t("Compare"),
  },
  {
    key: "other", label: t("Others"), title: t("File"),
    match: () => true,
    open: "", openLabel: "",
  },
] as const;

/** 2D and 3D never mix in the library: the folder says which. */
type Media = "all" | "2d" | "3d";
const mediaOf = (file: LibraryFile): Exclude<Media, "all"> => (file.folder.startsWith("3d/") ? "3d" : "2d");
const isMesh = (file: LibraryFile) => /\.(glb|gltf)$/i.test(file.name);

type Kind = (typeof KINDS)[number];

const kindOf = (file: LibraryFile): Kind => KINDS.find((kind) => kind.match(file))!;

/** A file a browser can show as a thumbnail. */
const isPicture = (file: LibraryFile) =>
  Boolean(file.mime?.startsWith("image/")) || /\.(png|jpe?g|webp|svg)$/i.test(file.name);

/**
 * The folder that names a group: the entity (`2d/a-knight/…`) or the batch
 * (`icons/<sheet>`, `generations/<batch>`).
 */
const owner = (file: LibraryFile) => file.folder.split("/")[1] ?? "";

/**
 * A brief image's section: `briefing/design/interface` becomes
 * `design/interface` -- the shelf of the cards it illustrates, and what names
 * its group.
 */
const sectionOf = (file: LibraryFile) =>
  (file.folder.startsWith(BRIEFING) ? file.folder.slice(BRIEFING.length) : "").replace(/\/$/, "");

export default function Library() {
  const { project, notify } = useStudio();
  const [wanted, setWanted] = useState<string>("all");
  const [media, setMedia] = useState<Media>("all");
  const { data: entities } = useEntities(project);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<LibraryFile | null>(null);
  const [renaming, setRenaming] = useState<string>("");
  const [importing, setImporting] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number; file: LibraryFile } | null>(null);
  /** The image viewed large: its index among those the page shows. */
  const [viewing, setViewing] = useState<number | null>(null);
  const sync = useSyncLibrary();

  // The whole project at once: the page sorts by kind, not by folder, and a
  // studio project holds a few hundred files.
  const { data: tree, isLoading, error } = useTree(project, "", 6, "", 1000);

  const all = tree?.files ?? [];
  const files = useMemo(() => all.filter((file) => media === "all" || mediaOf(file) === media), [all, media]);
  /** The title of the card holding an entity (`2d/<entity>/…`), else its id. */
  const ownerLabel = (file: LibraryFile) => {
    if (kindOf(file).key === "briefing") return sectionOf(file);
    const entity = owner(file);
    const card = /^[23]d\//.test(file.folder) ? entities?.find((entry) => entry.entity === entity) : undefined;
    return card ? `${card.title} · ${card.section_label}` : entity;
  };
  /**
   * A mesh's poster in the grid: its entity's concept, filed next to it; else
   * its card's cover (an imported mesh has no `concept.png`).
   */
  const posterOf = (file: LibraryFile): string | null => {
    if (!isMesh(file)) return null;
    const base = file.folder.split("/").slice(0, 2).join("/");
    const concept = all.find((other) => other.folder === base && other.name === "concept.png");
    if (concept) return thumbUrl(concept, 200);
    const cover = entities?.find((entry) => entry.entity === owner(file))?.cover;
    return cover ? assetPreviewUrl(cover, 200) : null;
  };
  /** Where an item is taken up again: the step of its entity's card, or a page. */
  const openOf = (file: LibraryFile, kind: Kind) => {
    if (kind.open === "compare") return `compare:${file.asset_id}`;
    if (!kind.open || !/^[23]d\//.test(file.folder)) return null;
    return cardHref(entities, owner(file), kind.open as "concepts" | "3d")?.slice(1) ?? null;
  };
  const term = search.trim().toLowerCase();
  /** What the filters keep: the grid, and the images browsed in the viewer. */
  const shown = useMemo(
    () =>
      files.filter(
        (file) =>
          (wanted === "all" || kindOf(file).key === wanted) &&
          (!term || `${file.folder}/${file.name}`.toLowerCase().includes(term)),
      ),
    [files, wanted, term],
  );
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    files.forEach((file) => {
      const key = kindOf(file).key;
      result[key] = (result[key] ?? 0) + 1;
    });
    return result;
  }, [files]);
  const groups = useMemo(() => {
    const byKey = new Map<string, { kind: Kind; owner: string; files: LibraryFile[] }>();
    shown.forEach((file) => {
      const kind = kindOf(file);
      const key = `${kind.key}|${owner(file)}`;
      if (!byKey.has(key)) byKey.set(key, { kind, owner: owner(file), files: [] });
      byKey.get(key)!.files.push(file);
    });
    return [...byKey.values()].sort(
      (left, right) =>
        KINDS.indexOf(left.kind) - KINDS.indexOf(right.kind) || left.owner.localeCompare(right.owner),
    );
  }, [shown]);
  const shownCount = groups.reduce((total, group) => total + group.files.length, 0);
  const current = selected && files.find((file) => file.asset_id === selected.asset_id) ? selected : null;
  const currentKind = current ? kindOf(current) : null;
  const currentOpen = current && currentKind ? openOf(current, currentKind) : null;
  /** The grid's images: what the viewer browses. */
  const pictures = useMemo(() => shown.filter(isPicture), [shown]);
  const viewed = viewing === null ? null : pictures[viewing] ?? null;

  function select(file: LibraryFile) {
    setSelected(file);
    setRenaming(file.name.replace(/\.[^.]+$/, ""));
  }

  /** Opens an image large, at its place among what the page shows. */
  const open = useCallback(
    (file: LibraryFile) => {
      const at = pictures.findIndex((picture) => picture.asset_id === file.asset_id);
      if (at >= 0) setViewing(at);
    },
    [pictures],
  );

  /** Browsing the viewer keeps the right-hand panel on the same image. */
  const goTo = useCallback(
    (index: number) => {
      setViewing(index);
      const file = pictures[(index + pictures.length) % pictures.length];
      if (file) select(file);
    },
    [pictures],
  );

  /** Shows a file or folder in the file manager; a refusal is reported. */
  const reveal = useReveal();

  /** Copies a path; a refusing clipboard is reported. */
  function copy(text: string) {
    writeClipboard(text).then(
      () => notify({ kind: "success", title: t("Path copied"), body: text }),
      (error: unknown) => notify({ kind: "error", title: t("Copy failed"), body: String(error) }),
    );
  }

  async function curate(action: () => Promise<Outcome>, verb: string) {
    try {
      const outcome = await action();
      notify({
        kind: outcome.ok ? "success" : "error",
        title: `${verb}: ${tr(outcome.summary)}`,
        body: Object.entries(outcome.refused)
          .map(([id, reason]) => `${id.slice(0, 8)} — ${tr(reason)}`)
          .join("\n") || undefined,
      });
      setSelected(null);
      sync.mutate(project);
    } catch (exception) {
      notify({ kind: "error", title: t("{verb} failed", { verb }), body: (exception as ApiError).message });
    }
  }

  return (
    <>
      <PageHeader
        title={t("Library")}
        project={project}
        actions={
          <>
            <button
              className="btn btn-ghost"
              onClick={() => sync.mutate(project)}
              disabled={sync.isPending}
              title={t("Rewrites the project mirror from the store")}
            >
              {sync.isPending ? "…" : t("Sync")}
            </button>
            <button
              className="btn btn-secondary"
              disabled={!inTauri() || !tree}
              onClick={() => tree && void reveal(tree.root)}
            >
              {t("Open the project folder")}
            </button>
            <button className="btn btn-secondary" onClick={() => setImporting(true)}>
              {t("Import a sheet…")}
            </button>
          </>
        }
      />

      <Toolbar className="lib-bar">
        <ToolGroup>
          <Seg<Media>
            value={media}
            onChange={(value) => {
              setMedia(value);
              setWanted("all");
            }}
            options={[
              { value: "all", label: `2D + 3D ${all.length}` },
              { value: "2d", label: `2D ${all.filter((file) => mediaOf(file) === "2d").length}` },
              { value: "3d", label: `3D ${all.filter((file) => mediaOf(file) === "3d").length}` },
            ]}
          />
        </ToolGroup>
        <ToolGroup>
          <div className="chips" role="group" aria-label={t("Show")}>
            <button className="chip" aria-pressed={wanted === "all"} onClick={() => setWanted("all")}>
              {t("All")} <span className="n">{files.length}</span>
            </button>
            {KINDS.filter((kind) => counts[kind.key]).map((kind) => (
              <button
                className="chip"
                key={kind.key}
                aria-pressed={wanted === kind.key}
                onClick={() => setWanted(kind.key)}
              >
                {kind.label} <span className="n">{counts[kind.key]}</span>
              </button>
            ))}
          </div>
        </ToolGroup>
        <ToolGroup end>
          <div className="search">
            <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="11" cy="11" r="6" />
              <path d="M20 20l-4.5-4.5" />
            </svg>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("Search: head, a-knight, .glb…")}
              aria-label={t("Search the library")}
            />
          </div>
        </ToolGroup>
      </Toolbar>

      <div className="lib">
        <Panel
          eyebrow={tn(files.length, "{n} item · {project}", "{n} items · {project}", { project: project || "—" })}
          title={t("Files produced")}
          actions={<span className="hint">{shownCount ? tn(shownCount, "{n} shown", "{n} shown") : t("no results")}</span>}
        >
          {error && <Empty title={t("Project missing from the library")} hint={(error as ApiError).message} />}
          {isLoading && <div className="skeleton" style={{ height: 200 }} />}
          {tree && !files.length && <Empty title={t("Nothing yet")} />}

          <div className="stack-6">
            {groups.map((group) => (
              <div key={`${group.kind.key}|${group.owner}`}>
                <p className="group-title">
                  <span>
                    {group.kind.title}
                    {group.owner ? ` · ${ownerLabel(group.files[0]!)}` : ""}
                  </span>
                  <span className="n">{group.files.length}</span>
                </p>
                <div className={group.kind.key === "icon" ? "assets assets-sm" : "assets"}>
                  {group.files.map((file) => {
                    // An image opens large; anything else is selected, because it
                    // is taken up in a step (concepts, 3D) or a page.
                    const picture = isPicture(file);
                    return (
                      <button
                        className="asset"
                        key={file.asset_id + file.name}
                        aria-pressed={current?.asset_id === file.asset_id}
                        title={picture ? t("{name} — view large", { name: file.name }) : file.name}
                        onClick={() => (picture ? open(file) : select(file))}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          select(file);
                          setMenu({ x: event.clientX, y: event.clientY, file });
                        }}
                      >
                        <span className={picture || posterOf(file) ? "thumb" : "thumb plain"}>
                          {picture ? (
                            <img src={thumbUrl(file, 200)} alt={file.name} loading="lazy" />
                          ) : posterOf(file) ? (
                            <>
                              <img src={posterOf(file)!} alt={file.name} loading="lazy" />
                              <span className="tag mono">3D</span>
                            </>
                          ) : (
                            <span className="mono">{file.name.split(".").pop()}</span>
                          )}
                        </span>
                        <span className="meta">
                          <b title={file.name}>{file.name.replace(/\.[^.]+$/, "")}</b>
                          <span>{file.name} · {bytes(file.size_bytes)}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {tree?.truncated && <p className="hint">{t("List truncated — refine the search.")}</p>}
        </Panel>

        <Panel
          className="sticky"
          eyebrow={currentKind?.title ?? t("Selected item")}
          title={current ? current.name.replace(/\.[^.]+$/, "") : t("None")}
          onClose={current ? () => setSelected(null) : undefined}
          bodyClass="stack-4"
          foot={
            current ? (
              <div className="row row-wrap gap-3">
                {isPicture(current) && (
                  <button className="btn btn-primary btn-sm" onClick={() => open(current)}>
                    {t("View large")}
                  </button>
                )}
                {currentOpen && !isPicture(current) && (
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => { window.location.hash = currentOpen; }}
                  >
                    {currentKind?.openLabel}
                  </button>
                )}
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={!inTauri()}
                  onClick={() => void reveal(current.path)}
                >
                  {t("Show in the folder")}
                </button>
              </div>
            ) : undefined
          }
        >
          {current ? (
            <>
              <div
                className={isMesh(current) ? "stage3d lib-mesh" : "board check"}
                style={isMesh(current) ? undefined : { height: 260, display: "grid", placeItems: "center", padding: "var(--space-4)" }}
              >
                {isMesh(current) ? (
                  <Suspense fallback={null}>
                    <MeshPreview key={current.asset_id} url={assetFileUrl(current.asset_id)} spin />
                  </Suspense>
                ) : isPicture(current) ? (
                  <img
                    src={thumbUrl(current, 520)}
                    alt={current.name}
                    style={{ width: "100%", height: "100%", objectFit: "contain" }}
                  />
                ) : (
                  <span className="mono" style={{ color: "var(--ink-hint)" }}>{current.name}</span>
                )}
              </div>
              <Facts
                rows={[
                  [t("File"), current.name],
                  [t("Folder"), current.folder || t("root")],
                  [t("Size"), bytes(current.size_bytes)],
                ]}
              />
              <div className="path-box">
                <span className="mono">{current.path}</span>
                <button className="btn btn-ghost btn-sm" onClick={() => copy(current.path)}>
                  {t("Copy")}
                </button>
              </div>
              <details className="more">
                <summary>{t("Rename or delete")}</summary>
                <div className="more-body stack-3">
                  <div className="row gap-3">
                    <input
                      value={renaming}
                      onChange={(event) => setRenaming(event.target.value)}
                      className="grow"
                      aria-label={t("New name")}
                    />
                    <button
                      className="btn btn-secondary btn-sm"
                      disabled={!renaming.trim()}
                      onClick={() => curate(() => api.rename(current.asset_id, renaming.trim()), t("Renamed"))}
                    >
                      {t("Rename")}
                    </button>
                  </div>
                  <button
                    className="btn btn-danger btn-block"
                    onClick={() => curate(() => api.remove([current.asset_id]), t("Deleted"))}
                  >
                    {t("Delete")}
                  </button>
                  <p className="hint">
                    {t("Protected if a character or a style pack holds it.")}
                  </p>
                </div>
              </details>
            </>
          ) : null}
        </Panel>
      </div>

      {menu && (
        <Menu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          <button onClick={() => { copy(menu.file.path); setMenu(null); }}>
            {t("Copy the path")}
          </button>
          <button disabled={!inTauri()} onClick={() => { void reveal(menu.file.path); setMenu(null); }}>
            {t("Show in the folder")}
          </button>
          <div className="sep" />
          <button className="danger" onClick={() => { void curate(() => api.remove([menu.file.asset_id]), t("Deleted")); setMenu(null); }}>
            {t("Delete")}
          </button>
        </Menu>
      )}

      {viewed && viewing !== null && (
        <ImageViewer
          files={pictures}
          at={viewing}
          onGo={goTo}
          onReveal={(path) => void reveal(path)}
          onClose={() => setViewing(null)}
        />
      )}

      {importing && <SheetImport project={project} onClose={() => setImporting(false)} />}
    </>
  );
}

/** The viewer's zoom: from the whole thumbnail to single-pixel detail. */
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 8;
/** The size requested from the studio: renders are larger than the window. */
const VIEW_SIZE = 2048;

/** The whole image at its resolution: an SVG is served raw, the rest as a preview. */
function viewUrl(file: LibraryFile): string {
  const vector = file.mime === "image/svg+xml" || file.name.toLowerCase().endsWith(".svg");
  return vector ? assetFileUrl(file.asset_id) : assetPreviewUrl(file.asset_id, VIEW_SIZE);
}

type View = { s: number; x: number; y: number };

/**
 * A library image, large, on the light table.
 *
 * It is viewed at its resolution -- a game screen's render is not judged in a
 * 200 px thumbnail: wheel to zoom under the cursor, drag to pan, `0` to refit,
 * and the arrows to go from one image to the next among those the page shows.
 */
function ImageViewer({ files, at, onGo, onReveal, onClose }: {
  files: LibraryFile[];
  at: number;
  onGo: (index: number) => void;
  onReveal: (path: string) => void;
  onClose: () => void;
}) {
  const file = files[at];
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const stage = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  // Another image is shown whole: keeping the previous zoom would frame the new
  // one on an unrelated detail.
  useEffect(() => setView({ s: 1, x: 0, y: 0 }), [file?.asset_id]);

  // The page does not scroll behind the image: wheel and arrows belong to
  // zooming and browsing, not to scrolling the grid.
  useEffect(() => {
    const held = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = held; };
  }, []);

  const go = useCallback(
    (delta: number) => onGo((at + delta + files.length) % files.length),
    [at, files.length, onGo],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight") go(1);
      else if (event.key === "ArrowLeft") go(-1);
      else if (event.key === "+" || event.key === "=") {
        setView((held) => ({ ...held, s: Math.min(ZOOM_MAX, held.s * 1.25) }));
      } else if (event.key === "-") {
        setView((held) => ({ ...held, s: Math.max(ZOOM_MIN, held.s / 1.25) }));
      } else if (event.key === "0") setView({ s: 1, x: 0, y: 0 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onClose]);

  /** Zoom follows the cursor: the point under it stays put. */
  function zoomAt(clientX: number, clientY: number, factor: number) {
    const box = stage.current?.getBoundingClientRect();
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.s * factor));
    if (!box || next === view.s) {
      setView((held) => ({ ...held, s: next }));
      return;
    }
    const x = clientX - box.left - box.width / 2;
    const y = clientY - box.top - box.height / 2;
    setView((held) => {
      const ratio = 1 - next / held.s;
      return { s: next, x: held.x + (x - held.x) * ratio, y: held.y + (y - held.y) * ratio };
    });
  }

  if (!file) return null;
  const many = files.length > 1;

  return (
    <div
      className="viewer"
      role="dialog"
      aria-modal="true"
      aria-label={file.name}
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <header className="viewer-head" onMouseDown={(event) => event.stopPropagation()}>
        <div className="grow">
          <p className="eyebrow">{file.folder}</p>
          <h2>{file.name}</h2>
        </div>
        <span className="mono viewer-count">{many ? `${at + 1} / ${files.length}` : ""}</span>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setView({ s: 1, x: 0, y: 0 })}
          title={t("Refit (0)")}
        >
          {t("Refit")}
        </button>
        <button
          className="btn btn-ghost btn-sm"
          disabled={!inTauri()}
          onClick={() => onReveal(file.path)}
        >
          {t("Show in the folder")}
        </button>
        <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label={t("Close (Esc)")}>
          <CloseCross />
        </button>
      </header>

      <div
        className="viewer-stage board check"
        ref={stage}
        onDoubleClick={() => setView({ s: 1, x: 0, y: 0 })}
        onWheel={(event) => {
          event.preventDefault();
          zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 1 / 1.12);
        }}
        onMouseDown={(event) => {
          event.preventDefault();
          drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
          setDragging(true);
        }}
        onMouseMove={(event) => {
          const held = drag.current;
          if (!held) return;
          setView((current) => ({
            ...current,
            x: held.vx + (event.clientX - held.x),
            y: held.vy + (event.clientY - held.y),
          }));
        }}
        onMouseUp={() => { drag.current = null; setDragging(false); }}
        onMouseLeave={() => { drag.current = null; setDragging(false); }}
      >
        <img
          className={dragging ? "viewed dragging" : "viewed"}
          src={viewUrl(file)}
          alt={file.name}
          draggable={false}
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}
        />
      </div>

      <footer className="viewer-foot">
        <span className="mono">×{view.s.toFixed(2)}</span>
        <span className="spacer" />
        <span className="mono">{bytes(file.size_bytes)}</span>
        {many && (
          <div className="row gap-3">
            <button className="btn btn-ghost btn-icon" onClick={() => go(-1)} aria-label={t("Previous image")}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
            </button>
            <button className="btn btn-ghost btn-icon" onClick={() => go(1)} aria-label={t("Next image")}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>
            </button>
          </div>
        )}
      </footer>
    </div>
  );
}

/** The split strategies the server knows (`sheet/`). */
const STRATEGIES = ["auto", "symbols", "groups", "clusters", "grid", "blobs", "single"];

/**
 * Importing a sheet: inspect first (free, nothing is written), then import.
 * Seeing the split before accepting it avoids producing fifty wrong files to
 * clean up afterwards.
 */
function SheetImport({ project, onClose }: { project: string; onClose: () => void }) {
  const { notify } = useStudio();
  const sync = useSyncLibrary();
  const [path, setPath] = useState("");
  const [strategy, setStrategy] = useState("auto");
  const [gap, setGap] = useState("");
  const [matting, setMatting] = useState(true);
  const [preview, setPreview] = useState<{ strategy: string; count: number; pieces: { name: string }[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const body = () => ({
    path,
    strategy,
    gap: gap ? Number(gap) : null,
    matting,
    project,
    multi: true,
  });

  async function run(kind: "inspect" | "import") {
    setBusy(true);
    try {
      if (kind === "inspect") {
        setPreview((await api.inspectSheet(body())) as never);
      } else {
        const result = await api.importSheet(body());
        notify({ kind: "success", title: t("{count} element(s) imported", { count: result.count }), body: String(result.sheet ?? "") });
        sync.mutate(project);
        onClose();
      }
    } catch (error) {
      notify({ kind: "error", title: t("Split failed"), body: (error as ApiError).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      eyebrow={t("Multi-element sheet")}
      title={t("Import a sheet")}
      onClose={onClose}
      wide
      foot={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-secondary" disabled={!path || busy} onClick={() => run("inspect")}>
            {t("Inspect")}
          </button>
          <button className="btn btn-primary" disabled={!path || busy} onClick={() => run("import")}>
            {t("Import")}
          </button>
        </>
      }
    >
      <Field label={t("File")}>
        <div className="row gap-3">
          <input
            className="grow"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="/path/to/sheet.svg"
          />
          <button
            className="btn btn-secondary"
            onClick={async () => {
              const chosen = await pickFile([
                { name: t("Sheets"), extensions: ["svg", "png", "jpg", "jpeg", "webp"] },
              ]);
              if (chosen) setPath(chosen);
            }}
            disabled={!inTauri()}
            title={inTauri() ? "" : t("outside the app: paste the path")}
          >
            {t("Browse…")}
          </button>
        </div>
      </Field>

      <div className="row gap-3" style={{ alignItems: "flex-start" }}>
        <Field label={t("Strategy")}>
          <Select
            label={t("Strategy")}
            value={strategy}
            options={STRATEGIES.map((entry) => ({ value: entry, label: entry }))}
            onChange={setStrategy}
          />
        </Field>
        <Field label={t("Gap")}>
          <input
            value={gap}
            onChange={(event) => setGap(event.target.value)}
            placeholder="auto"
            style={{ width: 110 }}
          />
        </Field>
      </div>

      <Setting label={t("Cut-out by the local model")}>
        <Toggle checked={matting} onChange={setMatting} label={t("local cut-out")} />
      </Setting>

      {preview && (
        <div className="panel">
          <div className="panel-head">
            <div className="grow">
              <p className="eyebrow">{t("Proposed split")}</p>
              <h2>{t("{count} element(s) — strategy {strategy}", { count: preview.count, strategy: preview.strategy })}</h2>
            </div>
          </div>
          <div className="panel-body scroll" style={{ maxHeight: 140 }}>
            <p className="hint" style={{ margin: 0 }}>{preview.pieces.map((piece) => piece.name).join(" · ")}</p>
          </div>
        </div>
      )}
    </Dialog>
  );
}
