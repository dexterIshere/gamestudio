/**
 * At work: the agents at work, and what they display, live.
 *
 * Each tab of the Chats window is an agent in its terminal -- opened by hand,
 * or started by a handoff (a VFX, an entity's rig and animations). This page
 * shows them all at once without driving them: each screen follows its tab's
 * grid, typing stays in the Chats, and "Open" leads there.
 *
 * Lazy-loaded: xterm and its stylesheet weigh on the studio window only when
 * this page is visited.
 */

import "@xterm/xterm/css/xterm.css";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type TerminalSession } from "../api";
import TerminalPane from "../chat/TerminalPane";
import { HARNESS_LOGOS } from "../chat/logos";
import { focusChat, showChat } from "../lib/host";
import { folderName } from "../lib/paths";
import { useStudio } from "../lib/store";
import { Empty, PageHeader, Seg, State } from "../components/ui";
import { t, tc } from "../lib/i18n";

/** Where a tab stands, in the studio's state vocabulary. */
function phase(session: TerminalSession): { state: string; label: string; rank: number } {
  if (session.state === "interrupted") return { state: "failed", label: t("interrupted"), rank: 4 };
  if (session.state !== "running") return { state: "done", label: t("done"), rank: 3 };
  if (session.turn?.state === "working") return { state: "running", label: t("working"), rank: 0 };
  // Its turn is over: the user's move -- a review waiting.
  if (session.turn?.state === "waiting") return { state: "needs_review", label: t("waiting"), rank: 1 };
  return { state: "pending", label: t("open"), rank: 2 };
}

/** How long the tab has been running, in readable units. */
function since(created: number, now: number): string {
  const minutes = Math.max(0, Math.floor((now - created * 1000) / 60_000));
  if (minutes < 60) return t("{minutes} min", { minutes });
  const hours = Math.floor(minutes / 60);
  return t("{hours} h {minutes}", { hours, minutes: String(minutes % 60).padStart(2, "0") });
}

type Filter = "running" | "all";

export default function Agents() {
  const { project } = useStudio();
  const [filter, setFilter] = useState<Filter>("running");
  const [wide, setWide] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const { data: sessions } = useQuery({
    queryKey: ["terminal", "sessions"],
    queryFn: api.terminalSessions,
    refetchInterval: 2000,
  });

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const shown = (sessions ?? [])
    .filter((entry) => filter === "all" || entry.state === "running")
    .sort((a, b) => phase(a).rank - phase(b).rank || b.created_at - a.created_at);

  return (
    <div className="stack-5">
      <PageHeader
        title={t("At work")}
        project={project}
        actions={
          <Seg<Filter>
            value={filter}
            onChange={setFilter}
            options={[
              { value: "running", label: t("Running") },
              { value: "all", label: tc("agents", "All") },
            ]}
          />
        }
      />
      {shown.length ? (
        <div className="agents-grid">
          {shown.map((session) => {
            const step = phase(session);
            const large = wide === session.id;
            return (
              <section
                key={session.id}
                className={`agent-tile${large ? " is-wide" : ""}`}
                aria-label={session.title}
              >
                <header className="agent-head">
                  <span className="harness-logo" aria-hidden="true">
                    {HARNESS_LOGOS[session.harness]
                      ?? <span className="mono">{(session.command.split("/").pop() ?? "").slice(0, 1)}</span>}
                  </span>
                  <b className="grow truncate" title={session.title}>{session.title}</b>
                  <State state={step.state} label={step.label} />
                  <span className="mono">{since(session.created_at, now)}</span>
                  <button
                    className="ibtn"
                    aria-label={large ? t("Zoom out") : t("Zoom in")}
                    title={large ? t("Zoom out") : t("Zoom in")}
                    aria-pressed={large}
                    onClick={() => setWide(large ? null : session.id)}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      {large
                        ? <path d="M4 9h5V4M20 9h-5V4M4 15h5v5M20 15h-5v5" />
                        : <path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" />}
                    </svg>
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={() => void focusChat(session.id)}>
                    {t("Open")}
                  </button>
                </header>
                <div className="agent-screen">
                  <TerminalPane session={session} active watch />
                </div>
                <footer className="agent-foot">
                  <span className="mono truncate" title={session.cwd}>{folderName(session.cwd)}</span>
                  {session.effort && <span className="mono">{t("effort {effort}", { effort: session.effort })}</span>}
                </footer>
              </section>
            );
          })}
        </div>
      ) : (
        <Empty
          title={filter === "running" ? t("No agent at work") : t("No tab")}
          action={<button className="btn btn-secondary btn-sm" onClick={() => void showChat()}>{t("Open the Chats")}</button>}
        />
      )}
    </div>
  );
}
