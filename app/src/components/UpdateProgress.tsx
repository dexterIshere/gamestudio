/**
 * The studio rebuild, followed: its phases, a gauge, the current line.
 *
 * Shared by the startup update window and by the rail's dialog: it is the same
 * build, followed through the same shell event (`update-progress`), so two
 * open windows show the same gauge.
 */

import { useCallback, useEffect, useState } from "react";
import {
  onUpdateProgress, updateBuild, updateProgress, type UpdateProgress,
} from "../lib/host";
import { State } from "./ui";
import { t, tr } from "../lib/i18n";

/** The rebuild state, kept up to date by the shell. */
export function useUpdateBuild() {
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  useEffect(() => {
    let stop: (() => void) | undefined;
    let alive = true;
    void onUpdateProgress((next) => alive && setProgress(next)).then((unlisten) => {
      if (alive) stop = unlisten;
      else unlisten();
    });
    // A build may already be running: started from the other window, or
    // before the dialog was reopened.
    void updateProgress().then((current) => alive && current && setProgress(current));
    return () => {
      alive = false;
      stop?.();
    };
  }, []);
  const start = useCallback(async () => {
    const first = await updateBuild();
    if (first) setProgress(first);
  }, []);
  return { progress, start };
}

/** A duration in minutes and seconds: "1:07". */
const clock = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
};

const PHASE_STATE = { pending: "pending", running: "running", done: "done" } as const;

export function UpdateGauge({ progress }: { progress: UpdateProgress }) {
  const failed = progress.state === "failed";
  const done = progress.state === "done";
  const percent = Math.round(progress.progress * 100);
  return (
    <div className="update-progress col">
      {progress.phases.length > 0 && (
        <div className="update-phases">
          {progress.phases.map((phase) => (
            <State
              key={phase.id}
              state={failed && phase.state === "running" ? "failed" : PHASE_STATE[phase.state]}
              label={tr(phase.label)}
            />
          ))}
        </div>
      )}

      <div
        className={`gauge ${failed ? "is-danger" : done ? "is-done" : ""}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div className="gauge-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="update-readout">
        <span className="mono num">{percent} %</span>
        <span className="update-line mono" title={progress.line}>
          {failed ? tr(progress.error) : done ? t("Ready") : tr(progress.line)}
        </span>
        <span className="mono num">{clock(progress.elapsed)}</span>
      </div>

      {failed && progress.tail.length > 0 && (
        <pre className="update-tail mono">{progress.tail.join("\n")}</pre>
      )}
    </div>
  );
}
