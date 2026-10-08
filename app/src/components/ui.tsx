/**
 * The studio's interface vocabulary.
 *
 * Each building block carries a design rule so that no page has to rediscover
 * it: one height per control, Outfit capitals for labels, tabular figures for
 * measurements, and above all the meaning of colors: orange for action and
 * selection, emerald for what is finished, amber for what costs or needs a
 * review, red for what failed.
 */

import {
  useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { decimal, lang, locale, t } from "../lib/i18n";

export const STATE_LABELS: Record<string, string> = {
  pending: t("pending"),
  running: t("running"),
  done: t("done"),
  failed: t("failed"),
  needs_review: t("to review"),
};

/** A job's name in plain words, for the main line of a log. */
export const JOB_TITLES: Record<string, string> = {
  build_character: t("Character build"),
  create_entity: t("Entity creation"),
  generate_image: t("Image generation"),
  explore_style: t("Style exploration"),
  train_style: t("Style training"),
  recut_apply: t("Re-split"),
  render_sprites: t("Sprite rendering"),
  ping: t("Connection test"),
};

/**
 * The close cross, drawn rather than typed.
 *
 * A `×` (U+00D7) is a character: its size depends on the font, its ink fills
 * only half its em, and its centering follows the baseline, so it floats in a
 * 32 px box. This path is exactly the requested size, with the same weight as
 * the other icons.
 */
export function CloseCross() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

/** A dropdown's chevron, drawn: `▾` would depend on the font. */
export function Chevron() {
  return (
    <svg className="chev" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** The tick of the chosen option, drawn like the other glyphs. */
function Tick() {
  return (
    <svg className="tick" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

/**
 * "Entity creation · a knight": the job's label first, then the subject its
 * step carries (`entity:a knight` → `a knight`).
 */
export function jobTitle(job: { kind: string; step: string }): string {
  const base = JOB_TITLES[job.kind] ?? job.kind;
  // A character build names its step after the character, without a prefix.
  const subject = job.step.includes(":")
    ? job.step.slice(job.step.indexOf(":") + 1)
    : job.kind === "build_character"
      ? job.step
      : "";
  return subject ? `${base} · ${subject}` : base;
}

/* ------------------------------------------------------------------ skeleton */

/**
 * A page's header: the breadcrumb (project › page), the title, and its actions
 * on the right. No sentence under the title: the page reads by its labels.
 */
export function PageHeader({ title, project, trail = [], actions }: {
  title: string;
  project?: string;
  /** The breadcrumb steps between the project and the page: a section, for example. */
  trail?: { label: string; href?: string }[];
  actions?: ReactNode;
}) {
  // The windows have no title bar (`decorations: false`): the page header
  // replaces it as the drag handle of a floating window.
  return (
    <header className="page-head" data-tauri-drag-region>
      <div className="titles">
        {project !== undefined && (
          <p className="where">
            {project || t("no project")} <span>›</span>{" "}
            {trail.map((step) => (
              <span key={step.label} className="crumb">
                {step.href ? <a href={step.href}>{step.label}</a> : step.label} <span>›</span>{" "}
              </span>
            ))}
            <b>{title}</b>
          </p>
        )}
        <h1>{title}</h1>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}


/**
 * The title line of a panel or dialog: one line, the title then its context
 * (an id, a count, the card concerned) in a discreet tone. An eyebrow stacked
 * above the title would double every header's height for a word nobody
 * reads. Without a title, the context stands in for it.
 */
function HeadLine({ step, title, context }: { step?: number; title?: ReactNode; context?: ReactNode }) {
  return (
    <div className="head-line">
      {step !== undefined && <span className="step-num">{step}</span>}
      {title ? <h2>{title}</h2> : context ? <h2 className="is-context">{context}</h2> : null}
      {title && context && <span className="head-context">{context}</span>}
    </div>
  );
}

export function Panel({ eyebrow, title, step, actions, onClose, foot, children, className = "", bodyClass = "", style }: {
  eyebrow?: ReactNode;
  title?: ReactNode;
  /** The panel's rank when order matters: it shows before the title. */
  step?: number;
  actions?: ReactNode;
  onClose?: () => void;
  foot?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  style?: React.CSSProperties;
}) {
  const head = eyebrow || title || actions || onClose;
  return (
    <section className={`panel ${className}`} style={style}>
      {head && (
        <div className="panel-head">
          <HeadLine step={step} title={title} context={eyebrow} />
          {actions}
          {onClose && (
            <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label={t("Close")}>
              <CloseCross />
            </button>
          )}
        </div>
      )}
      <div className={`panel-body ${bodyClass}`}>{children}</div>
      {foot && <div className="panel-foot">{foot}</div>}
    </section>
  );
}

/** A group of settings under its label, stacked. */
export function Section({ title, children, className = "" }: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`col gap-4 ${className}`}>
      {title && <div className="eyebrow">{title}</div>}
      {children}
    </div>
  );
}

/**
 * Name → value lines. A missing value shows "—": nothing is made up to fill a
 * cell.
 */
export function Facts({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([name, value], index) => (
        <div key={index}>
          <dt>{name}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ controls */

/** Label on the left, control on the right: the shape of every settings line. */
export function Setting({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="setting">
      <span className="label">{label}</span>
      {children}
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label }: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      className="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  );
}

/** A slider thumb's diameter (`styles.css`): the usable track is that much shorter. */
const SLIDER_THUMB = 16;

/**
 * A slider between its bounds, dragged as in Blender: the gesture follows the
 * mouse, and a right click or Escape during the drag undoes it (the value
 * returns to where it started and the slider is released). The drag is ours,
 * not the browser's, which no API can interrupt; the keyboard stays the
 * input's. `onCommit` ends a gesture that changed the value, `onCancel`
 * reports its abandonment (`onChange` has already restored the start value).
 *
 * The filled part of the track is computed here (`--fill`) rather than left to
 * the browser: only Firefox paints the covered part natively.
 */
export function Slider({ value, min, max, step = 0.01, onChange, onCommit, onCancel, bounds = true }: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  onCommit?: () => void;
  onCancel?: () => void;
  bounds?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const props = useRef({ value, min, max, step, onChange, onCommit, onCancel });
  props.current = { value, min, max, step, onChange, onCommit, onCancel };
  const stop = useRef<(() => void) | null>(null);
  // A slider that unmounts mid-gesture releases the mouse with it.
  useEffect(() => () => stop.current?.(), []);

  const fill = max === min ? 0 : ((value - min) / (max - min)) * 100;
  const round = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const track = input.current;
    if (event.button !== 0 || !track || stop.current) return;
    const rect = track.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right) return;
    event.preventDefault();
    track.focus({ preventScroll: true });
    const holder = event.currentTarget;
    const pointer = event.pointerId;
    holder.setPointerCapture(pointer);
    const start = props.current.value;
    let current = start;

    const follow = (clientX: number) => {
      const { min: low, max: high, step: unit, onChange: set } = props.current;
      const box = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - box.left - SLIDER_THUMB / 2)
        / Math.max(1, box.width - SLIDER_THUMB)));
      const last = Math.floor((high - low) / unit + 1e-9);
      const steps = Math.min(last, Math.round((ratio * (high - low)) / unit));
      const next = Number((low + steps * unit).toPrecision(12));
      if (next === current) return;
      current = next;
      set(next);
    };
    const finish = () => {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onUp, true);
      window.removeEventListener("mousedown", onMouseDown, true);
      window.removeEventListener("contextmenu", onMenu, true);
      window.removeEventListener("keydown", onKey, true);
      if (holder.hasPointerCapture(pointer)) holder.releasePointerCapture(pointer);
      stop.current = null;
    };
    const cancel = () => {
      finish();
      if (current !== start) {
        props.current.onChange(start);
        props.current.onCancel?.();
      }
      track.blur();
      // The context menu comes on press under Linux, on release elsewhere: it
      // is swallowed until the button goes up.
      const swallow = (menu: MouseEvent) => menu.preventDefault();
      const release = (up: MouseEvent) => {
        if (up.button !== 2) return;
        window.removeEventListener("mouseup", release, true);
        setTimeout(() => window.removeEventListener("contextmenu", swallow, true), 0);
      };
      window.addEventListener("contextmenu", swallow, true);
      window.addEventListener("mouseup", release, true);
    };
    function onMove(move: PointerEvent) {
      if (move.pointerId !== pointer) return;
      // A second button pressed during the drag arrives as `pointermove`.
      if (move.button === 2 || (move.buttons & 2) !== 0) cancel();
      else follow(move.clientX);
    }
    function onUp(up: PointerEvent) {
      if (up.pointerId !== pointer || (up.type === "pointerup" && up.button !== 0)) return;
      finish();
      if (current !== start) props.current.onCommit?.();
    }
    function onMouseDown(down: MouseEvent) {
      if (down.button === 2) cancel();
    }
    function onMenu(menu: MouseEvent) {
      menu.preventDefault();
      cancel();
    }
    function onKey(key: KeyboardEvent) {
      if (key.key !== "Escape") return;
      // Escape undoes the gesture and nothing else: the room around stays open.
      key.preventDefault();
      key.stopPropagation();
      cancel();
    }
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onUp, true);
    window.addEventListener("mousedown", onMouseDown, true);
    window.addEventListener("contextmenu", onMenu, true);
    window.addEventListener("keydown", onKey, true);
    stop.current = finish;
    follow(event.clientX);
  };

  return (
    <div className="slider" onPointerDown={onPointerDown}>
      {bounds && <span className="bound">{round(min)}</span>}
      <input
        ref={input}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        style={{ ["--fill" as string]: `${fill}%` }}
        onChange={(event) => onChange(Number(event.target.value))}
        onKeyUp={onCommit}
      />
      {bounds && <span className="bound">{round(max)}</span>}
    </div>
  );
}

/** A choice in a list: dot, label, detail, and its price on the right. */
export function Choice({ checked, label, sub, price, badge, disabled, onSelect }: {
  checked: boolean;
  label: ReactNode;
  sub?: ReactNode;
  price?: ReactNode;
  badge?: ReactNode;
  disabled?: boolean;
  onSelect?: () => void;
}) {
  return (
    <button
      type="button"
      className="choice"
      role="radio"
      aria-checked={checked}
      disabled={disabled}
      onClick={onSelect}
    >
      <span className="tick" />
      <span className="who">
        <b>{label}</b>
        {sub && <span>{sub}</span>}
      </span>
      {badge}
      {price && <span className="price">{price}</span>}
    </button>
  );
}

/** Exclusive modes: what to produce, an image size, a style. */
export function Seg<T extends string>({ value, options, onChange }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="seg" role="group">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A toolbar: groups of controls on one line.
 *
 * It wraps group by group, never inside a group, and all its controls have the
 * height of a `Seg`: a switch is written as `Flags` there, not as a `Toggle`
 * followed by text, which would align with nothing. An `end` group goes to the
 * end of the line (filters, count, shortcut).
 */
export function Toolbar({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`toolbar ${className}`}>{children}</div>;
}

/**
 * A toolbar group. With a label it becomes a module: its own frame, the label
 * separated from the options by a rule, which sets the parts of a bar apart
 * better than a margin.
 */
export function ToolGroup({ label, end, children }: { label?: ReactNode; end?: boolean; children: ReactNode }) {
  return (
    <div className={`tool-group${label ? " has-label" : ""}${end ? " is-end" : ""}`}>
      {label && <span className="tool-label">{label}</span>}
      {children}
    </div>
  );
}

/**
 * Independent switches in a `Seg` frame: each lights up on its own (its LED),
 * where a `Seg` keeps only one.
 */
export function Flags({ options }: {
  options: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean; title?: string }[];
}) {
  return (
    <div className="seg flags" role="group">
      {options.map((option) => (
        <button
          key={option.label}
          type="button"
          aria-pressed={option.checked}
          disabled={option.disabled}
          title={option.title}
          onClick={() => option.onChange(!option.checked)}
        >
          <span className="flag-led" aria-hidden="true" />
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The studio's dropdown. A native `<select>` opens the system list (white under
 * WebKitGTK, out of the console look): this one is drawn here.
 *
 * The list is `position: fixed`, set under the button, so a scrolling
 * container (the rail) does not clip it.
 */
export function Select({ value, options, onChange, label, title, className = "", listClassName, face }: {
  value: string;
  // `detail` only shows in the list (a path, a measurement);
  // `divider` draws a rule above the option (a group of actions).
  // `remove` adds a cross to the option: a gesture on the item, not a choice.
  options: {
    value: string;
    label: ReactNode;
    muted?: boolean;
    detail?: ReactNode;
    divider?: boolean;
    remove?: { label: string; run: () => void };
  }[];
  onChange: (value: string) => void;
  label: string;
  title?: string;
  className?: string;
  // The list's class, which lives outside the button (in a portal). By
  // default, the first word of `className` followed by `-list`: `ws-switch
  // has-logo` gives `ws-switch-list`, not `ws-switch has-logo-list`.
  listClassName?: string;
  // The button's content, when it says more than the chosen value (the
  // workspace selector: mark, name, folder). Without it, the value.
  face?: ReactNode;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  // `top` or `bottom`: the list opens under its button, or above it when room
  // is short below (a dialog's foot); `room` caps its height.
  const [place, setPlace] = useState<
    { left: number; top?: number; bottom?: number; width: number; room: number } | null>(null);
  const [active, setActive] = useState(0);
  const current = options.find((option) => option.value === value);
  const first = className.trim().split(/\s+/)[0];
  const listClass = listClassName ?? (first ? `${first}-list` : "");

  const close = () => setPlace(null);
  const open = () => {
    const box = trigger.current?.getBoundingClientRect();
    if (!box) return;
    setActive(Math.max(0, options.findIndex((option) => option.value === value)));
    const below = window.innerHeight - box.bottom - 12;
    const above = box.top - 12;
    setPlace(below < 240 && above > below
      ? { left: box.left, bottom: window.innerHeight - box.top + 4, width: box.width, room: above }
      : { left: box.left, top: box.bottom + 4, width: box.width, room: below });
  };
  const pick = (index: number) => {
    const option = options[index];
    if (option) onChange(option.value);
    close();
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!place) return;
    const away = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!list.current?.contains(target) && !trigger.current?.contains(target)) close();
    };
    // A scroll or a resize would move the list away from its button: better
    // close it than leave it floating elsewhere.
    window.addEventListener("mousedown", away, true);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    list.current?.focus();
    return () => {
      window.removeEventListener("mousedown", away, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [place]);

  const onKey = (event: ReactKeyboardEvent) => {
    if (!place) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
        event.preventDefault();
        open();
      }
      return;
    }
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      // Escape closes the list and only the list: the room or dialog around
      // stays open.
      if (event.key === "Escape") event.stopPropagation();
      close();
      trigger.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(options.length - 1, index + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pick(active);
    }
  };

  return (
    <div className={`dropdown ${className}`}>
      <button
        ref={trigger}
        type="button"
        className="dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={place !== null}
        aria-label={label}
        title={title}
        onClick={() => (place ? close() : open())}
        onKeyDown={onKey}
      >
        {face ?? (
          <>
            <span className="dropdown-value truncate">{current?.label ?? "—"}</span>
            <Chevron />
          </>
        )}
      </button>
      {/* In a portal: the list is `position: fixed`, but a scrolling ancestor
          (the rail, `overflow-y: auto`) would clip it at its edge. On `body`,
          it goes above everything. */}
      {place &&
        createPortal(
          <ul
            ref={list}
            className={`dropdown-list ${listClass}`}
            role="listbox"
            aria-label={label}
            tabIndex={-1}
            style={{
              left: place.left, top: place.top, bottom: place.bottom, minWidth: place.width,
              maxHeight: `min(360px, 60vh, ${Math.round(place.room)}px)`,
            }}
            onKeyDown={onKey}
          >
            {options.map((option, index) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                className={`${index === active ? "is-active" : ""} ${option.muted ? "is-muted" : ""} ${option.divider ? "is-divided" : ""}`}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(index)}
              >
                {option.detail ? (
                  <span className="opt-stack">
                    <span className="truncate">{option.label}</span>
                    <span className="opt-detail mono truncate">{option.detail}</span>
                  </span>
                ) : (
                  <span className="truncate">{option.label}</span>
                )}
                {option.value === value && <Tick />}
              {option.remove && (
                <button
                  type="button"
                  className="opt-remove"
                  aria-label={option.remove.label}
                  title={option.remove.label}
                  onClick={(event) => {
                    event.stopPropagation();
                    option.remove?.run();
                  }}
                >
                  <CloseCross />
                </button>
              )}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}

/**
 * A label above its control. An input is wrapped in a `<label>`; a group of
 * buttons (`group`: choices, a `Seg`, a slider) in a named group, since a
 * `<label>` would pass its click to the first button it contains.
 */
export function Field({ label, hint, group = false, children }: {
  label: ReactNode;
  hint?: string;
  group?: boolean;
  children: ReactNode;
}) {
  const id = useId();
  if (group) {
    return (
      <div className="field" role="group" aria-labelledby={id}>
        <span id={id}>{label}</span>
        {children}
        {hint && <span className="hint">{hint}</span>}
      </div>
    );
  }
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

/* ------------------------------------------------------------------- signals */

export function Badge({ children, tone }: { children: ReactNode; tone?: "warn" | "danger" | "quiet" }) {
  const mod = tone === "warn" ? "is-warn" : tone === "danger" ? "is-danger" : tone === "quiet" ? "is-quiet" : "";
  return <span className={`badge ${mod}`}>{children}</span>;
}

/** What became of a job. Hue and shape say the same thing. */
export function State({ state, label }: { state: string; label?: string }) {
  const mod =
    state === "running"
      ? "is-run"
      : state === "failed"
        ? "is-danger"
        : state === "needs_review"
          ? "is-warn"
          : state === "pending"
            ? "is-idle"
            : "";
  return <span className={`status ${mod}`}>{label ?? STATE_LABELS[state] ?? state}</span>;
}

/** What needs attention without blocking: amber, laid over the content. */
export function Callout({ children, action, onAction }: {
  children: ReactNode;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="callout">
      <span className="grow">{children}</span>
      {action && (
        <span className="action" style={{ cursor: onAction ? "pointer" : undefined }} onClick={onAction}>
          {action}
        </span>
      )}
    </div>
  );
}

/** A reading: the LED figure on top, its name below. */
export function Metric({ label, value, plain }: { label: string; value: ReactNode; plain?: boolean }) {
  return (
    <div className={`metric ${plain ? "plain" : ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

/** The band of readings: one panel, four separated columns. */
export function MetricGrid({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`} style={{ marginBottom: "var(--space-5)" }}>
      <div className="metric-grid">{children}</div>
    </section>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="what">{title}</div>
      {hint && <div className="hint">{hint}</div>}
      {action}
    </div>
  );
}

/* ------------------------------------------------------------------ overlays */

export function Dialog({ title, eyebrow, hint, children, foot, onClose, wide = false }: {
  title: string;
  eyebrow?: ReactNode;
  hint?: string;
  children: ReactNode;
  foot?: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  // Escape closes: a workbench opens many short dialogs, and aiming at a
  // button for each one breaks the flow.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`dialog ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="dialog-head">
          <HeadLine title={title} context={eyebrow} />
          {hint && <p className="hint dialog-hint">{hint}</p>}
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label={t("Close")}>
            <CloseCross />
          </button>
        </header>
        <div className="dialog-body">{children}</div>
        {foot && <div className="dialog-foot">{foot}</div>}
      </div>
    </div>
  );
}

/** A context menu anchored at a point of the screen, closed by the first click outside. */
export function Menu({ x, y, onClose, children, className }: {
  x: number;
  y: number;
  onClose: () => void;
  children: ReactNode;
  /** A menu variant: a list of sessions does not have the width of a list of actions. */
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    // In capture: a click on a button that opens another menu must first close
    // this one, not end up behind it.
    window.addEventListener("mousedown", away, true);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", away, true);
      window.removeEventListener("keydown", escape);
    };
  }, [onClose]);

  return (
    <div
      className={className ? `menu ${className}` : "menu"}
      ref={ref}
      style={{ left: x, top: y }}
      role="menu"
    >
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- formatting */

export function cost(usd: number): string {
  // The separator and symbol follow the language: "0,250 $", "$0.250".
  // Three decimals under a dollar: an image costs a few thousandths.
  const amount = decimal(usd, usd < 1 ? 3 : 2);
  return lang === "fr" ? `${amount} $` : `$${amount}`;
}

export function bytes(size: number | null): string {
  if (size === null) return "—";
  if (size < 1024) return t("{size} B", { size });
  if (size < 1024 * 1024) return t("{v} KB", { v: (size / 1024).toFixed(0) });
  return t("{v} MB", { v: (size / 1024 / 1024).toFixed(1) });
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleString(locale, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
