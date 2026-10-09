/**
 * Server reads, in one place.
 *
 * Each cache key is stable and named after its resource: the job stream
 * invalidates it when a production finishes.
 */

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api, type CardGenerateRequest, type DirectionAspect, type ScreenValue, type ShowcaseKind,
} from "../api";
import { updateStatus } from "./host";

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15000 });

/**
 * How far the studio lags behind its code. A change shows within twenty
 * seconds, or as soon as the window is focused again.
 */
export const useUpdateStatus = () =>
  useQuery({
    queryKey: ["update"],
    queryFn: updateStatus,
    refetchInterval: 20000,
    refetchOnWindowFocus: true,
  });

/** The briefing: what an agent reads on arrival, and what the Context page shows. */
export const useContext = (project = "") =>
  useQuery({ queryKey: ["context", project], queryFn: () => api.context(project) });

export const useContextNotes = () =>
  useQuery({ queryKey: ["contextNotes"], queryFn: api.contextNotes });

/** Rewrites `context/briefing.md` on disk, then refreshes what shows it. */
export function useRefreshContext() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (project: string) => api.contextRefresh(project),
    onSuccess: (_result, project) => {
      client.invalidateQueries({ queryKey: ["context", project] });
      client.invalidateQueries({ queryKey: ["contextNotes"] });
    },
  });
}

/** Saves a hand-written note: that is what changes the next context. */
export function useSaveContextNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ name, text }: { name: string; text: string }) => api.saveContextNote(name, text),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["contextNotes"] });
      client.invalidateQueries({ queryKey: ["context"] });
    },
  });
}

/** The environment diagnosis: it changes (a key is set, a tool is installed). */
export const useDoctor = () =>
  useQuery({ queryKey: ["doctor"], queryFn: api.doctor, staleTime: 20_000 });

/** The workbench prompts: they do not change while the studio runs. */
export const usePrompts = () =>
  useQuery({ queryKey: ["prompts"], queryFn: api.prompts, staleTime: Infinity });

/**
 * The studio's skills, and the verdict on their index and mirror. An agent
 * writes a skill from its own process, so both lists re-read themselves, like
 * the documents (`LIVE`, below).
 */
export const useSkills = () =>
  useQuery({ queryKey: ["skills"], queryFn: api.skills, ...LIVE });

export const useSkillsCheck = () =>
  useQuery({ queryKey: ["skillsCheck"], queryFn: api.skillsCheck, ...LIVE });

/** The MCP connections: read, never changed. */
export const useConnections = () =>
  useQuery({ queryKey: ["connections"], queryFn: api.connections });

/** Regenerates the skills index from the files, then re-reads the verdict. */
export function useWriteSkillsIndex() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.skillsIndex,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["skills"] });
      client.invalidateQueries({ queryKey: ["skillsCheck"] });
    },
  });
}

/** Repairs the `.agents/skills` mirror (symbolic links). */
export function useSyncSkills() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.skillsSync,
    onSuccess: () => client.invalidateQueries({ queryKey: ["skillsCheck"] }),
  });
}


/**
 * What an agent writes from its own process (a card, a devlog entry) does not
 * go through this window, so nothing invalidates it. Document lists therefore
 * re-read themselves while they are on screen.
 */
const LIVE = { refetchInterval: 4000, refetchOnWindowFocus: true } as const;
/** The world sits in the rail, so it is read all the time: a slower pace is enough. */
const LIVE_RAIL = { refetchInterval: 10000, refetchOnWindowFocus: true } as const;

/**
 * The documents of a shelf: what is written, and when. The shelf is part of
 * the key, so Notes and Devlog do not share their cache.
 */
export const useDocuments = (project: string, folder = "") =>
  useQuery({
    queryKey: ["documents", project, folder],
    queryFn: () => api.documents(project, folder),
    enabled: Boolean(project),
    ...LIVE,
  });

export const useDocumentTemplates = () =>
  useQuery({
    queryKey: ["documentTemplates"],
    queryFn: api.documentTemplates,
    staleTime: Infinity,
  });

type Shelf = { project: string; folder: string };

/** What a changed document makes stale: its list, the world sections, the workspace card. */
function refreshShelf(client: ReturnType<typeof useQueryClient>, { project, folder }: Shelf) {
  client.invalidateQueries({ queryKey: ["documents", project, folder] });
  client.invalidateQueries({ queryKey: ["world", project] });
  client.invalidateQueries({ queryKey: ["workspace"] });
}

/** Saves a document, then refreshes the list and the project's workspace card. */
export function useSaveDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name, text }: Shelf & { name: string; text: string }) =>
      api.saveDocument(project, name, text, folder),
    onSuccess: (_result, shelf) => refreshShelf(client, shelf),
  });
}

export function useCreateDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, title, template }:
      Shelf & { title: string; template: string }) =>
      api.createDocument(project, title, template, folder),
    onSuccess: (_result, shelf) => refreshShelf(client, shelf),
  });
}

/** Deletes a document: a human gesture, no MCP tool exposes it. */
export function useDeleteDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name }: Shelf & { name: string }) =>
      api.deleteDocument(project, name, folder),
    onSuccess: (_result, shelf) => refreshShelf(client, shelf),
  });
}

/** The world sections: none by default, the user declares them. */
export const useWorld = (project: string) =>
  useQuery({
    queryKey: ["world", project],
    queryFn: () => api.world(project),
    enabled: Boolean(project),
    ...LIVE_RAIL,
  });

/** The project's axes, and the sections that use each one. */
export const useWorldAxes = (project: string) =>
  useQuery({
    queryKey: ["world", project, "axes"],
    queryFn: () => api.worldAxes(project),
    enabled: Boolean(project),
  });

/** The world cards and their entities. */
export const useEntities = (project: string) =>
  useQuery({
    queryKey: ["entities", project],
    queryFn: () => api.entities(project),
    enabled: Boolean(project),
    ...LIVE_RAIL,
  });

/**
 * A card's workbench. While one of the card's jobs runs it re-reads itself, so
 * concepts arrive without reloading the page.
 */
export const useWorkbench = (project: string, section: string, name: string) =>
  useQuery({
    queryKey: ["workbench", project, section, name],
    queryFn: () => api.workbench(project, section, name),
    enabled: Boolean(project && section && name),
    refetchInterval: (query) => (query.state.data?.pending.length ? 2000 : false),
  });

/** A workspace's declaration (title, logo) without recomputing its card. */
export const useWorkspaceDeclaration = (project: string) =>
  useQuery({
    queryKey: ["workspace", "meta", project],
    queryFn: () => api.workspaceDeclaration(project),
    enabled: Boolean(project),
  });

/** The workspace: projects as cards, with their steps. */
export const useWorkspace = () =>
  useQuery({ queryKey: ["workspace"], queryFn: api.workspace });

export const useRecipes = () => useQuery({ queryKey: ["recipes"], queryFn: api.recipes });

export const useCharacters = (project: string) =>
  useQuery({
    queryKey: ["characters", project],
    queryFn: () => api.characters(project),
    enabled: Boolean(project),
  });

export const useCharacter = (project: string, id: string) =>
  useQuery({
    queryKey: ["character", project, id],
    queryFn: () => api.character(project, id),
    enabled: Boolean(project && id),
  });

export const useAssets = (kind?: string, limit = 60, project?: string) =>
  useQuery({
    queryKey: ["assets", kind, limit, project],
    queryFn: () => api.assets(kind, limit, project),
  });

export const useQueueStatus = (project?: string) =>
  useQuery({ queryKey: ["queue", project], queryFn: () => api.queue(project) });

export const useJobs = (project?: string, state?: string) =>
  useQuery({ queryKey: ["jobs", project, state], queryFn: () => api.jobs(project, state) });

export const useTree = (project: string, folder: string, depth = 1, pattern = "", limit = 200) =>
  useQuery({
    queryKey: ["tree", project, folder, depth, pattern, limit],
    queryFn: () => api.tree(project, folder, depth, pattern, limit),
    enabled: Boolean(project),
  });

export const usePoses = () => useQuery({ queryKey: ["poses"], queryFn: api.poses });

export const useSpriteStyles = () =>
  useQuery({ queryKey: ["spriteStyles"], queryFn: api.spriteStyles, staleTime: Infinity });

/** Invalidates what a production just changed in a project. */
export function useRefreshProject() {
  const client = useQueryClient();
  return (project: string) => {
    client.invalidateQueries({ queryKey: ["characters", project] });
    client.invalidateQueries({ queryKey: ["tree", project] });
    client.invalidateQueries({ queryKey: ["assets"] });
    client.invalidateQueries({ queryKey: ["jobs"] });
    client.invalidateQueries({ queryKey: ["workbench", project] });
    client.invalidateQueries({ queryKey: ["entities", project] });
  };
}

export function useSyncLibrary() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.syncLibrary,
    onSuccess: (_result, project) => {
      client.invalidateQueries({ queryKey: ["tree", project] });
    },
  });
}


/**
 * A game design card's media: its render, its sketch, its generated images.
 * It re-reads itself (a generation arrives, an agent redoes the render), like
 * the document list.
 */
export const useCardMedia = (project: string, folder: string, name: string | null) =>
  useQuery({
    queryKey: ["cardMedia", project, folder, name],
    queryFn: () => api.cardMedia(project, folder, name!),
    enabled: Boolean(project && folder && name),
    ...LIVE,
  });

/** A card's Excalidraw scene: read once on opening, never re-read under the user's hand. */
export const useCardSketch = (project: string, folder: string, name: string) =>
  useQuery({
    queryKey: ["cardSketch", project, folder, name],
    queryFn: () => api.cardSketch(project, folder, name),
    enabled: Boolean(project && folder && name),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

type Card = { project: string; folder: string; name: string };

const refreshCard = (client: ReturnType<typeof useQueryClient>, { project, folder, name }: Card) =>
  client.invalidateQueries({ queryKey: ["cardMedia", project, folder, name] });

/** Saves the sketch and its PNG export; the cached scene stays the editor's. */
export function useSaveCardSketch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name, scene, png }:
      Card & { scene: Record<string, unknown> | null; png: string | null }) =>
      api.saveCardSketch(project, folder, name, scene, png),
    onSuccess: (_result, card) => refreshCard(client, card),
  });
}

/** Adds reference images to the card, one by one; returns those that went in. */
export function useAddCardReferences() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ project, folder, name, files, paths = [] }:
      Card & { files: File[]; paths?: string[] }) => {
      const added = [];
      for (const path of paths) {
        added.push(await api.addCardReferencePath(project, folder, name, path));
      }
      for (const file of files) {
        added.push(await api.addCardReference(project, folder, name, file, file.name || "reference.png"));
      }
      return added;
    },
    // Even a drop interrupted by a refused image may have let others in.
    onSettled: (_result, _error, card) => refreshCard(client, card),
  });
}

/** Removes a reference from the card. Nothing brings it back. */
export function useDeleteCardReference() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name, file }: Card & { file: string }) =>
      api.deleteCardReference(project, folder, name, file),
    onSuccess: (_result, card) => refreshCard(client, card),
  });
}

/** Renders the card again with the game's engine. Free, local. */
export function useRerenderCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name }: Card) => api.rerenderCard(project, folder, name),
    onSuccess: (_result, card) => {
      refreshCard(client, card);
      client.invalidateQueries({ queryKey: ["tree", card.project] });
    },
  });
}

/** The screen editor of an interface card: its branch, its preview, its elements. */
export const useScreen = (project: string, folder: string, name: string) =>
  useQuery({
    queryKey: ["screen", project, folder, name],
    queryFn: () => api.screenState(project, folder, name),
    enabled: Boolean(project && folder && name),
    refetchOnWindowFocus: true,
  });

/** An element of the screen and its properties, as the branch writes them. */
export const useScreenNode = (project: string, folder: string, name: string,
                              path: string | null, version: string) =>
  useQuery({
    queryKey: ["screenNode", project, folder, name, path, version],
    queryFn: () => api.screenNode(project, folder, name, path!),
    enabled: Boolean(project && folder && name && path),
  });

/** What the studio is doing: followed closely while something runs. */
export const useActivity = (project: string) =>
  useQuery({
    queryKey: ["activity", project],
    queryFn: () => api.activity(project),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && (data.renders.length || data.queued.length || data.agents.length)
        ? 1000 : 4000;
    },
  });

// The sections already drawn ahead in this window: once per project and section.
const warmed = new Set<string>();

/** Draw a section's screens in the background as soon as it is shown. */
export function useWarmScreens(project: string, folder: string, enabled: boolean) {
  const client = useQueryClient();
  useEffect(() => {
    const key = `${project}\u0000${folder}`;
    if (!enabled || !project || warmed.has(key)) return;
    warmed.add(key);
    api.screensWarm(project, folder).then(
      () => client.invalidateQueries({ queryKey: ["activity"] }),
      () => warmed.delete(key),
    );
  }, [project, folder, enabled, client]);
}

/** A networked game's preview data (fake server answers). */
export const usePreviewData = (project: string) =>
  useQuery({
    queryKey: ["previewData", project],
    queryFn: () => api.previewData(project),
    enabled: Boolean(project),
    refetchOnWindowFocus: true,
    // Followed while its agent works: the screen is redrawn when it is done.
    refetchInterval: (query) => (query.state.data?.agent?.working ? 4000 : false),
  });

export function usePreviewDataEnable(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.previewDataEnable(project, enabled),
    onSuccess: (data) => client.setQueryData(["previewData", project], data),
  });
}

/** The comments on a screen's elements; followed while some are with the agent. */
export const useScreenComments = (project: string, folder: string, name: string) =>
  useQuery({
    queryKey: ["screenComments", project, folder, name],
    queryFn: () => api.screenComments(project, folder, name),
    enabled: Boolean(project && folder && name),
    refetchInterval: (query) =>
      query.state.data?.comments.some((c) => c.state === "queued" || c.state === "sent")
        ? 3000 : false,
  });

type CommentGesture =
  | { kind: "add"; path: string; text: string; send: boolean }
  | { kind: "send"; ids: number[] }
  | { kind: "remove"; ids: number[] };

export function useScreenCommentGesture(project: string, folder: string, name: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (gesture: CommentGesture) => {
      switch (gesture.kind) {
        case "add":
          return api.screenCommentAdd(project, folder, name, gesture.path, gesture.text,
                                      gesture.send);
        case "send":
          return api.screenCommentsSend(project, folder, name, gesture.ids);
        default:
          return api.screenCommentsRemove(project, folder, name, gesture.ids);
      }
    },
    onSuccess: (comments) => {
      client.setQueryData(["screenComments", project, folder, name], comments);
      // Sending makes the branch: the screen's state changes with it.
      client.invalidateQueries({ queryKey: ["screen", project, folder, name] });
      client.invalidateQueries({ queryKey: ["terminal", "sessions"] });
    },
  });
}

type ScreenGesture =
  | { kind: "open"; scene: string }
  | { kind: "render" }
  | { kind: "undo" }
  | { kind: "edit"; path: string; changes: Record<string, ScreenValue> };

/** Open, redraw, edit, undo: each gesture returns the screen's new state. */
export function useScreenGesture(project: string, folder: string, name: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (gesture: ScreenGesture) => {
      switch (gesture.kind) {
        case "open":
          return api.screenOpen(project, folder, name, gesture.scene);
        case "render":
          return api.screenRender(project, folder, name);
        case "undo":
          return api.screenUndo(project, folder, name);
        default:
          return api.screenEdit(project, folder, name, gesture.path, gesture.changes);
      }
    },
    onSuccess: (state) => {
      client.setQueryData(["screen", project, folder, name], state);
      client.invalidateQueries({ queryKey: ["screenNode", project, folder, name] });
      // A render may have called the preview data's agent.
      client.invalidateQueries({ queryKey: ["previewData", project] });
    },
  });
}

/** PAID: only called after the dialog that states the amount. */
export function useGenerateCard() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, folder, name, body }: Card & { body: CardGenerateRequest }) =>
      api.generateCard(project, folder, name, body),
    onSuccess: (_result, card) => {
      refreshCard(client, card);
      client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

/* ------------------------------------------------------------------- lookdev */

/** The game's specimens, and the dress that presents them. */
export const useLookdev = (project: string) =>
  useQuery({
    queryKey: ["lookdev", project],
    queryFn: () => api.lookdev(project),
    enabled: Boolean(project),
  });

/** An aspect's board of influences: followed while images are on their way, or while `live`. */
export const useDirectionBoard = (project: string, aspect: DirectionAspect, live: boolean) =>
  useQuery({
    queryKey: ["direction", project, aspect],
    queryFn: () => api.directionBoard(project, aspect),
    enabled: Boolean(project),
    refetchInterval: (query) =>
      live || query.state.data?.proposals.some((entry) => entry.status === "queued") ? 2000 : false,
  });

/** The thread with the agent: followed closely while it answers. */
export const useDirectionThread = (project: string, aspect: DirectionAspect) =>
  useQuery({
    queryKey: ["directionThread", project, aspect],
    queryFn: () => api.directionThread(project, aspect),
    enabled: Boolean(project),
    refetchInterval: (query) => (query.state.data?.running ? 500 : false),
  });

/** A specimen in detail: the first read starts the Godot bench (one to two seconds). */
export const useLookdevSpecimen = (project: string, specimen: string | null) =>
  useQuery({
    queryKey: ["lookdevSpecimen", project, specimen],
    queryFn: () => api.lookdevSpecimen(project, specimen!),
    enabled: Boolean(project && specimen),
    staleTime: 60_000,
  });

/** The showcase of the game's icons or props. */
export const useShowcase = (project: string, kind: ShowcaseKind) =>
  useQuery({
    queryKey: ["showcase", project, kind],
    queryFn: () => api.showcase(project, kind),
    enabled: Boolean(project),
    refetchOnWindowFocus: true,
  });

/** Draws the props that are missing or stale: an offscreen Godot, a few seconds. */
export function useRenderShowcase(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) => api.renderShowcase(project, force),
    onSuccess: (showcase) => client.setQueryData(["showcase", project, "props"], showcase),
  });
}

/** The forge requests: re-read while a generation is running. */
export const useForge = (project: string) =>
  useQuery({
    queryKey: ["forge", project],
    queryFn: () => api.forge(project),
    enabled: Boolean(project),
    refetchInterval: (query) =>
      query.state.data?.some((request) => request.status === "running") ? 2500 : false,
  });

export const useForgeFamilies = (project: string) =>
  useQuery({
    queryKey: ["forgeFamilies", project],
    queryFn: () => api.forgeFamilies(project),
    enabled: Boolean(project),
  });

export const useTrash = (project: string) =>
  useQuery({
    queryKey: ["trash", project],
    queryFn: () => api.trash(project),
    enabled: Boolean(project),
  });

/** After a gesture on the game, what shows it is read again: showcase, forge, trash. */
function refreshGame(client: ReturnType<typeof useQueryClient>, project: string) {
  for (const key of ["showcase", "forge", "forgeFamilies", "trash", "jobs"]) {
    client.invalidateQueries({ queryKey: [key, project] });
  }
  client.invalidateQueries({ queryKey: ["jobs"] });
}

/** Showcase and forge gestures: each re-reads what it changed. */
export function useGameGesture<T, R>(project: string, run: (input: T) => Promise<R>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: run, onSuccess: () => refreshGame(client, project) });
}

/** Writes how a lookdev specimen is presented: use shown, shape, staging. */
export function useSetLookdevState() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ project, specimen, body }: {
      project: string; specimen: string; body: Parameters<typeof api.setLookdevState>[2];
    }) => api.setLookdevState(project, specimen, body),
    onSuccess: (_result, { project, specimen }) => {
      client.invalidateQueries({ queryKey: ["lookdev", project] });
      client.invalidateQueries({ queryKey: ["lookdevSpecimen", project, specimen] });
    },
  });
}
