/**
 * The global progress bar: what the studio is doing, along the status bar.
 *
 * A render advances against the duration its scene's previous renders took
 * (an estimate: it stops short of the end until the render returns); the
 * screens queued behind it are counted. The agent writing the game's fake data
 * has no measurable progress: the bar runs without an end, with the time
 * elapsed, and a click shows its tab.
 */

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { focusChat } from "../lib/host";
import { useActivity } from "../lib/queries";
import { t, tn } from "../lib/i18n";

/** An estimate never reaches the end on its own: only the render's return does. */
const CEILING = 0.95;

function seconds(value: number): string {
  const total = Math.max(0, Math.round(value));
  return total < 60 ? `${total} s` : `${Math.floor(total / 60)} min ${total % 60} s`;
}

function sceneName(scene: string): string {
  return scene.split("/").pop() ?? scene;
}

export default function ActivityBar({ project }: { project: string }) {
  const { data, dataUpdatedAt } = useActivity(project);
  const client = useQueryClient();
  const [tick, setTick] = useState(() => Date.now());
  const renders = data?.renders ?? [];
  const agents = data?.agents ?? [];
  const pending = (data?.queued ?? []).reduce((sum, entry) => sum + entry.pending, 0);
  const active = renders.length > 0 || agents.length > 0 || pending > 0;

  // Smooth between two reads: the elapsed time is advanced locally.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTick(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [active]);

  // A render that finished changed an image somewhere: the screens are reread.
  const before = useRef(0);
  useEffect(() => {
    if (renders.length < before.current) {
      void client.invalidateQueries({ queryKey: ["screen"] });
      void client.invalidateQueries({ queryKey: ["previewData"] });
    }
    before.current = renders.length;
  }, [renders.length, client]);

  if (!data || !active) return null;

  const now = data.now + Math.max(0, tick - dataUpdatedAt) / 1000;
  const render = renders[0];
  const agent = agents[0];
  const progress = render
    ? Math.min(CEILING, (now - render.started_at) / Math.max(render.expected, 1))
    : null;
  const waiting = pending - (render ? 1 : 0);

  return (
    <>
      <span className={`activity-line ${progress === null ? "is-endless" : ""}`} aria-hidden="true">
        {progress !== null && <span style={{ width: `${progress * 100}%` }} />}
      </span>
      <span className="activity" role="status">
        {render && (
          <span>
            {t("Rendering {scene}", { scene: sceneName(render.scene) })}{" "}
            <span className="num">
              {seconds(now - render.started_at)} / ~{seconds(render.expected)}
            </span>
            {waiting > 0 && <> · {tn(waiting, "{n} screen waiting", "{n} screens waiting")}</>}
          </span>
        )}
        {!render && waiting > 0 && <span>{tn(waiting, "{n} screen waiting", "{n} screens waiting")}</span>}
        {agent && (
          <button type="button" className="activity-agent" onClick={() => void focusChat(agent.id)}>
            {t("Fake data being written")} <span className="num">{seconds(now - agent.started_at)}</span>
          </button>
        )}
      </span>
    </>
  );
}
