/**
 * The update window: what the studio shows at startup when its code has changed.
 *
 * It opens alone, instead of the studio and the Chats, and without a server: it
 * only talks to the shell. The build starts as soon as it opens; once done, the
 * studio restarts on the new binary. Any failure, from the build or from the
 * shell itself (launching, restarting), leaves a choice: retry, or open the
 * studio as it was. A window stuck on "Ready" without a button would be a dead
 * end.
 */

import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Callout } from "./components/ui";
import { UpdateGauge, useUpdateBuild } from "./components/UpdateProgress";
import { syncShellLang, updateNow, updateSkip } from "./lib/host";
import "./styles.css";
import { t, tr } from "./lib/i18n";

/** Why the shell refused: it rejects with a string. */
const why = (error: unknown): string => (error instanceof Error ? error.message : String(error));

function UpdateWindow() {
  const { progress, start } = useUpdateBuild();
  const started = useRef(false);
  // What the shell refused: starting the build, restarting, opening.
  const [refusal, setRefusal] = useState<string | null>(null);

  const attempt = useCallback(async (action: () => Promise<unknown>) => {
    setRefusal(null);
    try {
      await action();
    } catch (error) {
      setRefusal(why(error));
    }
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void attempt(start);
  }, [attempt, start]);

  // Linger briefly on "Ready": the full gauge shows before the window goes.
  useEffect(() => {
    if (progress?.state !== "done") return;
    const timer = window.setTimeout(() => void attempt(updateNow), 700);
    return () => window.clearTimeout(timer);
  }, [progress?.state, attempt]);

  const failed = progress?.state === "failed" || refusal !== null;
  // Retry redoes what failed: the restart if the build succeeded, the build
  // otherwise.
  const retry = () => void attempt(progress?.state === "done" ? updateNow : start);
  return (
    <div className="update-window col">
      <header className="update-head" data-tauri-drag-region>
        <p className="eyebrow" data-tauri-drag-region>gamestudio</p>
        <h1 data-tauri-drag-region>{failed ? t("Update failed") : t("Studio update")}</h1>
      </header>
      {progress ? <UpdateGauge progress={progress} /> : <div className="gauge" />}
      {refusal && <Callout>{tr(refusal)}</Callout>}
      {failed && (
        <footer className="update-foot">
          <button className="btn btn-ghost" onClick={() => void attempt(updateSkip)}>
            {t("Open without updating")}
          </button>
          <button className="btn btn-primary" onClick={retry}>
            {t("Retry")}
          </button>
        </footer>
      )}
    </div>
  );
}

// The shell writes the relauncher's notifications itself: it needs the
// language even when this window is the only one open.
void syncShellLang();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <UpdateWindow />
  </StrictMode>,
);
