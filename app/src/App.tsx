/**
 * Application frame: navigation rail, active project, status bar.
 *
 * The current page lives in the URL fragment, so reloading the web view (as
 * hot reload does during development) does not go back to the first page.
 */

import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, workspaceLogoUrl } from "./api";
import {
  useHealth, useQueueStatus, useRecipes, useUpdateStatus, useWorkspaceDeclaration, useWorld,
} from "./lib/queries";
import { useJobStream, useStudio } from "./lib/store";
import { inTauri, pickFolder, showChat, updateNow, type UpdateStatus } from "./lib/host";
import { Dialog, Facts, Field, Select, State, cost } from "./components/ui";
import { UpdateGauge, useUpdateBuild } from "./components/UpdateProgress";
import Control from "./pages/Control";
import Context from "./pages/Context";
import Documents, { Shelf } from "./pages/Documents";
import Library from "./pages/Library";
import Vfx from "./pages/Vfx";
import Lookdev from "./pages/Lookdev";
import ShowcasePage from "./pages/Showcase";
import Compare from "./pages/Compare";
import WorldPage, { SectionDialog, WorldGlyph } from "./pages/World";
import ActivityBar from "./components/ActivityBar";
import Toasts from "./components/Toasts";
import { t, tn } from "./lib/i18n";

// xterm only loads when visiting the agents at work.
const Agents = lazy(() => import("./pages/Agents"));

/* The rail icons: a stroke in the text color. The page name always goes with
   them; when the rail is collapsed, it stays as a tooltip. */
const ICONS = {
  studio: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </>
  ),
  context: (
    <>
      <path d="M5 4.5h9l5 5v10a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 19.5v-13A1.5 1.5 0 0 1 5.5 5z" />
      <path d="M14 4.5v5h5M8 13h8M8 16.5h5" />
    </>
  ),
  agents: (
    <>
      <rect x="3" y="4.5" width="18" height="12" rx="2" />
      <path d="M7 9l2.5 2L7 13M12 13h4M8 20h8M12 16.5V20" />
    </>
  ),
  documents: (
    <>
      <path d="M6 4.5h7l5 5v10a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5V6A1.5 1.5 0 0 1 6.5 4.5z" />
      <path d="M13 4.5v5h5M8.5 13h7M8.5 16.5h4" />
    </>
  ),
  vfx: (
    <>
      <circle cx="12" cy="12" r="2.5" />
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
    </>
  ),
  library: <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  compare: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <path d="M12 4.5v15" />
    </>
  ),
  mechanics: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <circle cx="9" cy="9" r="1" />
      <circle cx="15" cy="9" r="1" />
      <circle cx="9" cy="15" r="1" />
      <circle cx="15" cy="15" r="1" />
    </>
  ),
  interface: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <path d="M3.5 9h17M8 9v10.5" />
    </>
  ),
  icons: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <circle cx="17" cy="7" r="3.5" />
      <path d="M7 13.5l3.5 6.5h-7zM13.5 13.5h7v7h-7z" />
    </>
  ),
  props: (
    <>
      <rect x="3.5" y="7.5" width="12" height="9" rx="4.5" />
      <path d="M17.5 9l3 3-3 3" />
    </>
  ),
  artdir: (
    <>
      <path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.3-1-1.6-1-2.8 0-1 .8-1.7 1.8-1.7h2.2a3.7 3.7 0 0 0 3.7-3.7c0-4-3.8-7.1-8.5-7.1z" />
      <circle cx="7.5" cy="11" r="1" />
      <circle cx="10" cy="7.5" r="1" />
      <circle cx="14.5" cy="7.5" r="1" />
    </>
  ),
  ideas: <path d="M9 18h6M10 21h4M12 3.5a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-.6c0-.8.4-1.5 1-2A6 6 0 0 0 12 3.5z" />,
  notes: <path d="M6 3.5h12a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V5A1.5 1.5 0 0 1 6 3.5zM8.5 8.5h7M8.5 12h7M8.5 15.5h4" />,
  devlog: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  chats: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <path d="M7 9.5l3 2.5-3 2.5M13 15h4" />
    </>
  ),
  collapse: <path d="M11 7l-5 5 5 5M18 7l-5 5 5 5" />,
  update: (
    <>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.5 4.5v4h-4M12 8.5V13l2.5 1.5" />
    </>
  ),
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <span className="glyph">
      <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>
    </span>
  );
}

/** A document shelf as a page: its title, its folder, its template. */
const shelf = (title: string, folder: string, template: string) =>
  function ShelfPage() {
    return <Shelf title={title} folder={folder} template={template} />;
  };

// Five titled categories. "The world" has no fixed page: its entries are the
// sections the user declares (`service/world.py`). The team is the agents
// working for the project.
const SECTIONS = [
  { id: "discover", label: t("Discover") },
  { id: "team", label: t("Team") },
  { id: "world", label: t("The world") },
  { id: "design", label: t("Game design") },
  { id: "dev", label: t("Development") },
] as const;

const PAGES = [
  // Overview, projects, job log and connections: one page, because they
  // answer one question: where the studio stands, and what to do next.
  { key: "studio", label: t("Control room"), section: "discover", component: Control },
  { key: "context", label: t("Context"), section: "discover", component: Context },
  { key: "agents", label: t("At work"), section: "team", component: Agents },
  {
    key: "mechanics", label: t("Mechanics"), section: "design",
    component: shelf(t("Mechanics"), "design/mechanics", "mechanic"),
  },
  {
    key: "interface", label: t("Interface"), section: "design",
    component: shelf(t("Interface"), "design/interface", "interface"),
  },
  // Icons and props are parts of the interface: filed under it, each with its
  // own shelf. Their page is a showcase of the game's elements, to be judged;
  // their written cards are behind "Cards".
  {
    key: "icons", label: t("Icons"), section: "design", parent: "interface",
    component: () => <ShowcasePage kind="icons" />,
  },
  {
    key: "props", label: "Props", section: "design", parent: "interface",
    component: () => <ShowcasePage kind="props" />,
  },
  { key: "vfx", label: "VFX", section: "design", component: Vfx },
  {
    key: "artdir", label: t("Art direction"), section: "design",
    component: Lookdev,
  },
  { key: "ideas", label: t("Ideas"), section: "dev", component: shelf(t("Ideas"), "ideas", "idea") },
  { key: "notes", label: t("Notes"), section: "dev", component: shelf(t("Notes"), "notes", "note") },
  { key: "devlog", label: "Devlog", section: "dev", component: shelf("Devlog", "devlog", "devlog") },
  { key: "documents", label: t("Documents"), section: "dev", component: Documents },
  { key: "library", label: t("Library"), section: "dev", component: Library },
  // The end of the line: everything produced ends up here, to be judged, not
  // just looked at.
  { key: "compare", label: t("Compare"), section: "dev", component: Compare },
] as const;

const RAIL_KEY = "gs-rail";

/** Whether the rail is collapsed, remembered across sessions. */
function useRailCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === "collapsed";
    } catch {
      return false;
    }
  });
  const toggle = () =>
    setCollapsed((current) => {
      try {
        localStorage.setItem(RAIL_KEY, current ? "open" : "collapsed");
      } catch {
        /* A convenience: without storage, the rail just comes back expanded. */
      }
      return !current;
    });
  return [collapsed, toggle];
}

// `world` is the page of a world section: the section is its zone.
type PageKey = (typeof PAGES)[number]["key"] | "world";

function usePage(): [PageKey, string, (key: string) => void] {
  // `#page:zone`: the zone only concerns the page, which scrolls to it itself.
  const read = (): [PageKey, string] => {
    const [hash = "", zone = ""] = window.location.hash.replace("#", "").split(":");
    if (hash === "world" && zone) return ["world", zone];
    return PAGES.some((page) => page.key === hash) ? [hash as PageKey, zone] : ["studio", ""];
  };
  const [where, setWhere] = useState<[PageKey, string]>(read);

  useEffect(() => {
    const onHash = () => setWhere(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return [where[0], where[1], (key) => { window.location.hash = key; }];
}

// The four selector entries that are gestures, not projects.
const OPEN_FOLDER = "\u0000open-folder";
const UNHIDE = "\u0000unhide";
const SET_LOGO = "\u0000logo";
const CLEAR_LOGO = "\u0000logo-clear";

/**
 * `/home/me/games/studio/x` -> `…/studio/x`: the start of a path says nothing,
 * the end names the folder. The whole path stays in the tooltip.
 */
function tildify(path: string): string {
  const short = path.replace(/^\/home\/[^/]+/, "~").replace(/^\/Users\/[^/]+/, "~");
  const parts = short.split("/").filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-2).join("/")}` : short;
}

function FolderDialog({ onClose, onOpen }: { onClose: () => void; onOpen: (path: string) => void }) {
  const [path, setPath] = useState("");
  const ready = path.trim().startsWith("/");
  return (
    <Dialog
      title={t("Open a folder")}
      onClose={onClose}
      foot={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!ready} onClick={() => onOpen(path.trim())}>
            {t("Open")}
          </button>
        </>
      }
    >
      <Field label={t("Absolute path")}>
        <input
          className="mono"
          autoFocus
          value={path}
          placeholder="/home/…/my-game"
          onChange={(event) => setPath(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && ready) onOpen(path.trim());
          }}
        />
      </Field>
    </Dialog>
  );
}

/**
 * Updating from inside the studio: what lags behind, the rebuild followed on a
 * gauge while everything stays open, then the restart, when the user chooses.
 * The Chats agents are children of the server: only the restart interrupts
 * them, and their tabs can be resumed afterwards.
 */
function UpdateDialog({ status, build, onClose }: {
  status: UpdateStatus | null | undefined;
  build: ReturnType<typeof useUpdateBuild>;
  onClose: () => void;
}) {
  const [error, setError] = useState("");
  const [restarting, setRestarting] = useState(false);
  const { data: sessions } = useQuery({ queryKey: ["terminal", "sessions"], queryFn: api.terminalSessions });
  const running = sessions?.filter((session) => session.state === "running").length ?? 0;
  const { progress, start } = build;
  const phase = progress?.state ?? "idle";

  const restart = async () => {
    setRestarting(true);
    try {
      await updateNow();
    } catch (failure) {
      setError((failure as Error).message ?? String(failure));
      setRestarting(false);
    }
  };

  const foot =
    phase === "running" ? (
      <>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>{t("Close")}</button>
      </>
    ) : phase === "done" ? (
      <>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>{t("Later")}</button>
        <button className="btn btn-primary" disabled={restarting} onClick={() => void restart()}>
          {t("Restart")}
        </button>
      </>
    ) : phase === "failed" ? (
      <>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>{t("Close")}</button>
        <button className="btn btn-primary" onClick={() => void start()}>{t("Retry")}</button>
      </>
    ) : (
      <>
        <span className="spacer" />
        <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
        <button className="btn btn-primary" onClick={() => void start()}>{t("Update")}</button>
      </>
    );

  return (
    <Dialog title={t("Update the studio")} onClose={onClose} foot={foot}>
      {phase === "idle" ? (
        <Facts
          rows={[
            [t("Application"), status?.app ? t("needs rebuilding") : t("up to date")],
            [t("Server"), status?.server ? t("needs restarting") : t("up to date")],
          ]}
        />
      ) : (
        progress && <UpdateGauge progress={progress} />
      )}
      {phase === "done" && running > 0 && (
        <p className="status is-warn" style={{ marginTop: "var(--space-4)" }}>
          {tn(running, "{n} running agent, interrupted by the restart",
            "{n} running agents, interrupted by the restart")}
        </p>
      )}
      {error && <p className="status is-danger" style={{ marginTop: "var(--space-3)" }}>{error}</p>}
    </Dialog>
  );
}

export default function App() {
  const [page, zone, setPage] = usePage();
  const [collapsed, toggleRail] = useRailCollapsed();
  const { project, setProject, notify } = useStudio();
  const { data: recipes } = useRecipes();
  const client = useQueryClient();
  // Outside the shell there is no native picker: the path is typed.
  const [typing, setTyping] = useState(false);
  const [addingSection, setAddingSection] = useState(false);
  const logoInput = useRef<HTMLInputElement>(null);
  const { data: declaration } = useWorkspaceDeclaration(project);
  const { data: sections } = useWorld(project);
  const activeRecipe = recipes?.find((recipe) => recipe.project === project);
  // A workspace removed from the list no longer shows, even if it was active:
  // the studio then moves to the next one (see the recovery effect below).
  const shown = (recipes ?? []).filter((recipe) => !recipe.hidden);
  const hiddenCount = (recipes ?? []).filter((recipe) => recipe.hidden).length;

  const openFolder = async (path: string) => {
    try {
      const opened = await api.openFolder(path);
      await client.invalidateQueries();
      setProject(opened.project);
      notify({
        kind: "success",
        title: opened.created ? t("Project {project} created", { project: opened.project }) : t("Project {project} opened", { project: opened.project }),
        body: opened.root,
      });
    } catch (error) {
      notify({ kind: "error", title: t("Folder refused"), body: (error as Error).message });
    }
  };

  // Removing a workspace from the list: the studio forgets it, nothing is
  // erased. If it was the active one, move to the next one still shown.
  const forget = async (name: string) => {
    try {
      const done = await api.forgetWorkspace(name);
      await client.invalidateQueries();
      if (name === project) {
        const next = recipes?.find((recipe) => recipe.project !== name && !recipe.hidden);
        setProject(next?.project ?? "");
      }
      notify({
        kind: "info",
        title: t("{name} removed from the list", { name }),
        body: done.root ? t("{root} — untouched", { root: done.root }) : t("No file deleted"),
      });
    } catch (error) {
      notify({ kind: "error", title: t("Removal refused"), body: (error as Error).message });
    }
  };

  const setLogo = async (file: File) => {
    try {
      await api.setWorkspaceLogo(project, file);
      await client.invalidateQueries({ queryKey: ["workspace"] });
    } catch (error) {
      notify({ kind: "error", title: t("Logo refused"), body: (error as Error).message });
    }
  };

  const chooseProject = async (value: string) => {
    if (value === SET_LOGO) {
      // An `<input type=file>` rather than the shell dialog: bytes are sent,
      // and it also works outside Tauri.
      logoInput.current?.click();
      return;
    }
    if (value === CLEAR_LOGO) {
      try {
        await api.clearWorkspaceLogo(project);
        await client.invalidateQueries({ queryKey: ["workspace"] });
      } catch (error) {
        notify({ kind: "error", title: t("Logo not removed"), body: (error as Error).message });
      }
      return;
    }
    if (value === OPEN_FOLDER) {
      if (!inTauri()) {
        setTyping(true);
        return;
      }
      let path: string | null = null;
      try {
        path = await pickFolder(t("Project folder"));
      } catch (error) {
        notify({ kind: "error", title: t("Folder refused"), body: String(error) });
      }
      if (path) await openFolder(path);
      return;
    }
    if (value === UNHIDE) {
      try {
        await api.unhideWorkspaces();
        await client.invalidateQueries();
      } catch (error) {
        notify({ kind: "error", title: t("Showing hidden workspaces refused"), body: (error as Error).message });
      }
      return;
    }
    setProject(value);
  };
  const { data: health } = useHealth();
  const { data: update } = useUpdateStatus();
  const build = useUpdateBuild();
  const [updating, setUpdating] = useState(false);
  const building = build.progress?.state === "running";
  const outdated =
    (!!update && (update.app || update.server)) ||
    building || build.progress?.state === "done" || build.progress?.state === "failed";
  const { data: queue } = useQueueStatus(project || undefined);
  const { connected } = useJobStream();

  // The project remembered from the last session may have gone since (recipe
  // deleted or renamed). Without this recovery, the selector shows the first
  // entry while the application queries a ghost project, and every page looks
  // empty without saying why.
  useEffect(() => {
    if (!recipes?.length) return;
    if (recipes.some((recipe) => recipe.project === project && !recipe.hidden)) return;
    const first =
      recipes.find((recipe) => !recipe.error && !recipe.hidden) ??
      recipes.find((recipe) => !recipe.hidden);
    // Everything is hidden: no project, rather than one the user removed.
    if ((first?.project ?? "") !== project) setProject(first?.project ?? "");
  }, [project, recipes, setProject]);

  const Current = PAGES.find((entry) => entry.key === page)?.component;
  const workers = health ? health.workers : null;
  const logo = declaration?.logo ?? null;
  const name = declaration?.title || project;

  return (
    <>
      <div className={collapsed ? "app rail-collapsed" : "app"}>
        <aside className="rail">
          {/* The workspace is chosen where the eye looks first: at the top of
              the rail, in place of the brand. The button shows the project and
              its folder; the menu lists them, opens another folder, or removes
              one from the list. */}
          <Select
            className={logo ? "ws-switch has-logo" : "ws-switch"}
            listClassName="ws-switch-list"
            value={project}
            onChange={(value) => void chooseProject(value)}
            label={t("Workspace")}
            title={activeRecipe?.root ?? "gamestudio"}
            face={
              <>
                {logo ? (
                  <img
                    className="ws-logo"
                    src={workspaceLogoUrl(project, logo.version)}
                    alt=""
                    draggable={false}
                  />
                ) : (
                  <span className="ws-mark" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                      <path d="M12 3.5l8.5 8.5-8.5 8.5L3.5 12z" />
                    </svg>
                  </span>
                )}
                <span className="ws-text">
                  <span className="ws-name truncate">{name || t("no workspace")}</span>
                  <span className="ws-path mono truncate">
                    {activeRecipe?.root ? tildify(activeRecipe.root) : "gamestudio"}
                  </span>
                </span>
                <svg className="ws-chev" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M8 9.5l4-4 4 4M8 14.5l4 4 4-4" />
                </svg>
              </>
            }
            options={[
              ...shown.map((recipe) => ({
                value: recipe.project,
                label: recipe.error ? t("{project} (invalid)", { project: recipe.project }) : recipe.project,
                detail: recipe.root ? tildify(recipe.root) : "gamestudio",
                muted: Boolean(recipe.error),
                remove: { label: t("Remove {project} from the list", { project: recipe.project }), run: () => void forget(recipe.project) },
              })),
              // The remembered project is set before the list arrives: without
              // its entry, the selector would show the first option and lie
              // about the project actually queried.
              ...(project && !recipes?.some((recipe) => recipe.project === project)
                ? [{ value: project, label: project }]
                : []),
              ...(!shown.length ? [{ value: "", label: t("no workspace"), muted: true }] : []),
              ...(project
                ? [
                    { value: SET_LOGO, label: logo ? t("Change the logo…") : t("Set a logo…"), divider: true },
                    ...(logo ? [{ value: CLEAR_LOGO, label: t("Remove the logo") }] : []),
                  ]
                : []),
              { value: OPEN_FOLDER, label: t("Open a folder…"), divider: true },
              ...(hiddenCount
                ? [{ value: UNHIDE, label: t("Show {hiddenCount} hidden", { hiddenCount }) }]
                : []),
            ]}
          />

          <input
            ref={logoInput}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void setLogo(file);
            }}
          />

          <nav aria-label={t("Studio pages")}>
            {SECTIONS.map((section) => (
              <div className="col nav-group" key={section.id} style={{ gap: 2 }}>
                <div className="rail-label">
                  <span className="grow truncate">{section.label}</span>
                  {section.id === "world" && (
                    <button
                      className="rail-add"
                      disabled={!project}
                      title={project ? t("New section") : t("No active workspace")}
                      aria-label={t("New world section")}
                      onClick={() => setAddingSection(true)}
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">{ICONS.plus}</svg>
                    </button>
                  )}
                </div>
                {/* An empty world shows its gesture as a rail entry: the "+"
                    in the title alone goes unnoticed. */}
                {section.id === "world" && !sections?.length && (
                  <button
                    className="nav-item nav-add"
                    disabled={!project}
                    title={project ? t("New section") : t("No active workspace")}
                    onClick={() => setAddingSection(true)}
                  >
                    <Icon name="plus" />
                    <span className="txt">{project ? t("New section") : t("No workspace")}</span>
                  </button>
                )}
                {section.id === "world"
                  ? (sections ?? []).map((entry) => (
                      <button
                        key={entry.id}
                        className="nav-item"
                        title={entry.label}
                        aria-current={page === "world" && zone.split("/")[0] === entry.id ? "page" : undefined}
                        onClick={() => setPage(`world:${entry.id}`)}
                      >
                        <WorldGlyph icon={entry.icon} />
                        <span className="txt">{entry.label}</span>
                      </button>
                    ))
                  : PAGES.filter((entry) => entry.section === section.id).map((entry) => (
                      <button
                        key={entry.key}
                        className={`nav-item ${"parent" in entry ? "is-sub" : ""}`}
                        title={entry.label}
                        aria-current={entry.key === page ? "page" : undefined}
                        onClick={() => setPage(entry.key)}
                      >
                        <Icon name={entry.key} />
                        <span className="txt">{entry.label}</span>
                      </button>
                    ))}
              </div>
            ))}
          </nav>

          {/* Not a page but another window: hence no `aria-current`, which
              would pass it off as the current page, and the arrow. */}
          <div className="rail-foot col" style={{ gap: 2 }}>
            {outdated && (
              <button
                className="nav-item is-update"
                onClick={() => setUpdating(true)}
                title={
                  building ? t("Rebuilding")
                    : build.progress?.state === "done" ? t("Ready to restart")
                      : update?.app ? t("Application needs rebuilding") : t("Server needs restarting")
                }
              >
                <Icon name="update" />
                <span className="txt">{t("Update")}</span>
                {building && build.progress && (
                  <span className="num mono">{Math.round(build.progress.progress * 100)} %</span>
                )}
              </button>
            )}
            <button
              className="nav-item"
              onClick={() => void showChat()}
              title="Chats"
            >
              <Icon name="chats" />
              <span className="txt">Chats</span>
              <svg className="ext" viewBox="0 0 24 24" role="img" aria-label={t("separate window")}>
                <path d="M8 16l8-8M9.5 8H16v6.5" />
              </svg>
            </button>
            <button
              className="nav-item rail-toggle"
              aria-expanded={!collapsed}
              title={collapsed ? t("Expand the menu") : t("Collapse the menu")}
              onClick={toggleRail}
            >
              <Icon name="collapse" />
              <span className="txt">{collapsed ? t("Expand") : t("Collapse")}</span>
            </button>
          </div>
        </aside>

        <main className="main">
          <Suspense fallback={<div className="skeleton" style={{ height: 240 }} />}>
            {Current ? (
              <Current />
            ) : (
              // The key follows the section, not the step: changing step keeps
              // what was typed in the workbench.
              <WorldPage key={`${project}:${zone.split("/").slice(0, 2).join("/")}`} id={zone}
                         onGone={() => setPage("studio")} />
            )}
          </Suspense>
        </main>

        <footer className="status-bar">
          <span className="status" style={{ color: "var(--fg-2)" }}>
            {workers === null ? "…" : t("{workers} worker(s)", { workers })}
          </span>
          <span className="sep">·</span>
          <span className={health?.runware_key ? "status" : "status is-warn"}>
            {health?.runware_key ? "Runware" : t("Runware missing")}
          </span>
          <span className="sep">·</span>
          <State state={connected ? "running" : "pending"} label={connected ? t("queue live") : t("queue offline")} />
          <ActivityBar project={project} />
          <span className="spacer" />
          <span className="optional">
            {t("project")} <b>{project || t("none")}</b>
          </span>
          <span className="sep optional">·</span>
          <span>
            {t("project cost")} <b className="mono">{cost(queue?.cost_usd ?? 0)}</b>
          </span>
        </footer>
      </div>

      {addingSection && (
        <SectionDialog
          project={project}
          onClose={() => setAddingSection(false)}
          onDone={(section) => {
            setAddingSection(false);
            setPage(`world:${section.id}`);
          }}
        />
      )}

      {updating && <UpdateDialog status={update} build={build} onClose={() => setUpdating(false)} />}

      {typing && (
        <FolderDialog
          onClose={() => setTyping(false)}
          onOpen={(path) => {
            setTyping(false);
            void openFolder(path);
          }}
        />
      )}

      <Toasts />
    </>
  );
}
