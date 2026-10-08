/**
 * Application-wide state: active project, messages, job queue.
 *
 * The active project is the only truly global state: every page reads it, the
 * workspace selector changes it. Everything else comes from the server and
 * lives in React Query, which handles caching, refetching and invalidation.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { jobStreamUrl, type QueueStatus } from "../api";

export interface Toast {
  id: number;
  kind: "info" | "error" | "success";
  title: string;
  body?: string;
  /** When it was last sent: a duplicate refreshes it and restarts the gauge. */
  at: number;
  /** How many times the same message arrived while it was on screen. */
  count: number;
}

interface StudioState {
  project: string;
  setProject: (project: string) => void;
  toasts: Toast[];
  notify: (toast: Omit<Toast, "id" | "at" | "count">) => void;
  dismiss: (id: number) => void;
}

const StudioContext = createContext<StudioState | null>(null);

const PROJECT_KEY = "gamestudio.project";

/**
 * The active project, read outside the React context.
 *
 * For the Chats window: it shares the studio's origin, hence its storage, but
 * not its component tree.
 */
export function activeProject(): string {
  try {
    return localStorage.getItem(PROJECT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function StudioProvider({ children }: { children: ReactNode }) {
  // The project survives a restart: resuming work should not start by
  // selecting it again.
  const [project, setProjectState] = useState(activeProject);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const setProject = useCallback((next: string) => {
    setProjectState(next);
    try {
      localStorage.setItem(PROJECT_KEY, next);
    } catch {
      /* Without storage the project lasts for this session: the next start
         falls back to the first workspace of the list. */
    }
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  // The same message sent again while on screen does not stack: it counts.
  // The display time belongs to the card (`Toasts.tsx`), which pauses it on
  // hover.
  const notify = useCallback((toast: Omit<Toast, "id" | "at" | "count">) => {
    const at = Date.now();
    setToasts((current) => {
      const same = current.find((entry) =>
        entry.kind === toast.kind && entry.title === toast.title && entry.body === toast.body);
      if (same) {
        return current.map((entry) => (entry === same ? { ...entry, at, count: entry.count + 1 } : entry));
      }
      return [...current, { ...toast, id: at + Math.random(), at, count: 1 }];
    });
  }, []);

  const value = useMemo(
    () => ({ project, setProject, toasts, notify, dismiss }),
    [project, setProject, toasts, notify, dismiss],
  );

  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>;
}

export function useStudio(): StudioState {
  const value = useContext(StudioContext);
  if (value === null) throw new Error("useStudio outside StudioProvider");
  return value;
}

/**
 * Plugs the application into the job queue stream.
 *
 * The server only emits on a real change: an event means something moved, so
 * whatever depends on it (characters, library, assets) must be read again,
 * with no timer and no idle polling.
 */
export function useJobStream(): { connected: boolean } {
  const client = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource(jobStreamUrl());

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (event) => {
      const snapshot = JSON.parse(event.data) as QueueStatus;
      client.setQueryData(["queue", undefined], snapshot);
      client.invalidateQueries({ queryKey: ["jobs"] });
      // A finished job produced files: what the interface shows of the
      // project may be stale.
      if (snapshot.recent.some((job) => job.state === "done" || job.state === "failed")) {
        client.invalidateQueries({ queryKey: ["assets"] });
        client.invalidateQueries({ queryKey: ["characters"] });
        client.invalidateQueries({ queryKey: ["tree"] });
        // The workspace cards and a project's queue say the same as the
        // stream: without this, the control room and the footer would diverge.
        client.invalidateQueries({ queryKey: ["workspace"] });
        client.invalidateQueries({
          predicate: (query) => query.queryKey[0] === "queue" && query.queryKey[1] !== undefined,
        });
      }
    };

    return () => source.close();
  }, [client]);

  return { connected };
}
