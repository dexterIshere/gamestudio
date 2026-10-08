/**
 * The studio's messages: one card per event, bottom right, above the status bar.
 *
 * A card shows its kind by its badge (a path, never a character) and its
 * meta line (kind · time), then the title and, if any, the detail (a path
 * reads in `.mono`). It leaves on its own: the bottom gauge shows the time it
 * has left, and hovering pauses it, to read a long message. A failure stays
 * twice as long and can be copied, to give it to an agent. The same message
 * sent again does not stack: it counts (×2).
 */

import { useEffect, useRef, useState } from "react";
import { writeClipboard } from "../lib/host";
import { useStudio, type Toast } from "../lib/store";
import { CloseCross } from "./ui";
import { locale, t } from "../lib/i18n";

/** How long a card shows, by kind. */
const DURATION: Record<Toast["kind"], number> = { info: 6000, success: 6000, error: 12000 };
/** Beyond this, the oldest gives way: a pile of messages is no longer read. */
const SHOWN = 4;
/** The exit duration, matched to `toast-out` in styles.css. */
const LEAVE_MS = 180;

const KIND_LABELS: Record<Toast["kind"], string> = { info: t("Info"), success: t("Done"), error: t("Failed") };

export default function Toasts() {
  const { toasts, dismiss } = useStudio();
  return (
    <section className="toasts" aria-label={t("Messages")}>
      {toasts.slice(-SHOWN).map((toast) => (
        <ToastCard key={toast.id} toast={toast} onDone={() => dismiss(toast.id)} />
      ))}
    </section>
  );
}

function ToastCard({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  const [leaving, setLeaving] = useState(false);
  const [paused, setPaused] = useState(false);
  const [copied, setCopied] = useState(false);
  const duration = DURATION[toast.kind];
  // The time left, and the moment it started running again: hovering stops
  // it, leaving resumes it where it was.
  const left = useRef(duration);
  const since = useRef(Date.now());

  const leave = () => {
    setLeaving(true);
    window.setTimeout(onDone, LEAVE_MS);
  };

  // A duplicate arriving meanwhile gives the card its full duration back.
  useEffect(() => {
    left.current = duration;
    since.current = Date.now();
  }, [toast.at, duration]);

  useEffect(() => {
    if (paused || leaving) return;
    since.current = Date.now();
    const timer = window.setTimeout(leave, left.current);
    return () => {
      window.clearTimeout(timer);
      left.current = Math.max(0, left.current - (Date.now() - since.current));
    };
    // `leave` changes nothing read here: only the card's state restarts the count.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, leaving, toast.at]);

  const copy = async () => {
    try {
      await writeClipboard(toast.body ? `${toast.title}\n${toast.body}` : toast.title);
      setCopied(true);
    } catch {
      /* Without a clipboard, the message stays readable on screen. */
    }
  };

  const time = new Date(toast.at).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const path = toast.body !== undefined && isPath(toast.body);

  return (
    <article
      className={`toast is-${toast.kind}${leaving ? " is-leaving" : ""}${paused ? " is-paused" : ""}`}
      role={toast.kind === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <span className="toast-badge" aria-hidden="true"><KindGlyph kind={toast.kind} /></span>
      <div className="toast-main">
        <p className="toast-meta mono">
          <span className="toast-kind">{KIND_LABELS[toast.kind]}</span>
          <span>{time}</span>
          {toast.count > 1 && <span className="toast-count num">×{toast.count}</span>}
        </p>
        <p className="toast-title">{toast.title}</p>
        {toast.body && <p className={`toast-body${path ? " mono" : ""}`}>{toast.body}</p>}
        {toast.kind === "error" && (
          <button type="button" className="toast-action" onClick={() => void copy()}>
            {copied ? t("Copied") : t("Copy the message")}
          </button>
        )}
      </div>
      <button type="button" className="btn btn-ghost btn-icon toast-close" aria-label={t("Close")} onClick={leave}>
        <CloseCross />
      </button>
      {/* The key restarts the gauge when a duplicate refreshes the card. */}
      <span key={toast.at} className="toast-timer" style={{ animationDuration: `${duration}ms` }} aria-hidden="true" />
    </article>
  );
}

/** A path, a file or an id: a single word, read in a fixed-width font. */
function isPath(text: string): boolean {
  return !/\s/.test(text.trim()) && /[/\\.]/.test(text);
}

function KindGlyph({ kind }: { kind: Toast["kind"] }) {
  return (
    <svg viewBox="0 0 24 24">
      {kind === "success" ? (
        <path d="M5 12.5l4.5 4.5L19 7.5" />
      ) : kind === "error" ? (
        <>
          <path d="M12 3.5l9 16H3z" />
          <path d="M12 10v4.5M12 17.5v.01" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 11v5.5M12 7.5v.01" />
        </>
      )}
    </svg>
  );
}
