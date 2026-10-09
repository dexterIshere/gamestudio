/**
 * The editor of a game screen: point at an element on the render, say what it should become.
 *
 * It is the "Edit" tab of an Interface or Props card, modelled on design
 * tools: on the left the screen as Godot draws it, where an element is hovered
 * and picked, its toolbar on top; on the right three panes -- its layers,
 * the selected element, the comments. The tab draws the screen as soon as it
 * opens. The agent's edits land on the card's branch (`studio/screen|prop-<card>`,
 * in a separate copy of the game: the user's own checkout does not move); the
 * user merges the branch when the screen suits them.
 *
 * An element takes comments: what the user wants of it, in words. A
 * comment is saved, or handed to the screen's agent at once; the list on the
 * right sends the saved ones, one at a time (`service/screen_comments.py`).
 *
 * The service is `service/screens.py`; the rectangles come from the engine
 * (`render_scene.gd`, survey of the Controls), in preview pixels.
 */

import {
  useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent, type RefObject,
} from "react";
import {
  api, screenPreviewUrl, type CardRender, type ScreenComment, type ScreenNode, type ScreenState,
} from "../api";
import { focusChat } from "../lib/host";
import {
  usePreviewData, usePreviewDataEnable, useScreen, useScreenCommentGesture, useScreenComments,
  useScreenGesture,
} from "../lib/queries";
import HandoffDialog from "./Handoff";
import { ChatPlusGlyph } from "../pages/Documents";
import { RedrawAllGlyph, TrashGlyph } from "./IconForge";
import { Badge, Callout, Empty, Panel, Seg, Select, State, Toggle, ToolGroup, Toolbar, shortDate } from "./ui";
import { t, tn } from "../lib/i18n";

/** The sections whose cards are edited on the render: a screen, a prop. */
export const SCREEN_FOLDERS = ["design/interface", "design/props"];

type Zoom = "fit" | "half" | "full";

/** Fitted to the space, or in preview pixels: a HUD reads poorly on the whole screen. */
const ZOOMS: { value: Zoom; label: string }[] = [
  { value: "fit", label: t("Fitted") },
  { value: "half", label: "50 %" },
  { value: "full", label: "100 %" },
];


export default function ScreenEditor({ project, folder, name, render }: {
  project: string;
  folder: string;
  name: string;
  render: CardRender | null;
}) {
  const screen = useScreen(project, folder, name);
  const gesture = useScreenGesture(project, folder, name);
  const state = screen.data;

  if (screen.error && !state) return <Callout>{(screen.error as Error).message}</Callout>;
  if (!state) return <Empty title={t("Reading…")} />;
  if (!state.preview) return <FirstRender state={state} render={render} gesture={gesture} />;
  return <Editor project={project} folder={folder} name={name} state={state} render={render}
                 gesture={gesture} />;
}

type Gesture = ReturnType<typeof useScreenGesture>;

/** The game's screens, the card's own first (the one its current render shows). */
function useSceneOptions(state: ScreenState, render: CardRender | null) {
  const initial = state.scene || render?.scene || "";
  const options = useMemo(() => {
    const found = state.scenes.map((scene) => ({
      value: scene.file, label: scene.file, detail: <span className="mono">{scene.root}</span>,
    }));
    if (initial && !found.some((entry) => sameScene(entry.value, initial))) {
      found.unshift({ value: initial, label: initial, detail: <span className="mono">{t("render")}</span> });
    }
    return found;
  }, [state.scenes, initial]);
  const current = options.find((entry) => sameScene(entry.value, initial))?.value
    ?? options[0]?.value ?? "";
  return { options, current };
}

/**
 * Nothing drawn yet: the card's own screen -- the one its render shows, else
 * the one its text cites -- is drawn at once; a card that names none, or a
 * failure, lets one be chosen. Never the first screen of the list: it would
 * be another screen than the card's.
 */
function FirstRender({ state, render, gesture }: {
  state: ScreenState;
  render: CardRender | null;
  gesture: Gesture;
}) {
  const { options, current } = useSceneOptions(state, render);
  const known = state.scene || render?.scene || "";
  const [scene, setScene] = useState(current);
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !known) return;
    started.current = true;
    gesture.mutate({ kind: "open", scene: known });
  }, [known, gesture]);

  if (options.length === 0) return <Empty title={t("No screen in the game")} />;
  if (gesture.isPending || (!gesture.isError && known)) {
    return <Empty title={t("Rendering…")} />;
  }
  return (
    <div className="screen-open">
      <div className="screen-open-row">
        <Select label={t("Screen")} value={scene} options={options} onChange={setScene} />
        <button
          type="button"
          className="btn btn-primary"
          disabled={!scene}
          onClick={() => gesture.mutate({ kind: "open", scene })}
        >
          {t("Render")}
        </button>
      </div>
      {gesture.isError && <Badge tone="danger">{(gesture.error as Error).message}</Badge>}
    </div>
  );
}

/** `client/scenes/hud.tscn` and `res://scenes/hud.tscn` name the same scene. */
function sameScene(a: string, b: string): boolean {
  const tail = (value: string) => value.replace(/^res:\/\//, "");
  return a === b || a.endsWith(`/${tail(b)}`) || b.endsWith(`/${tail(a)}`);
}

/** The topmost element under a point: the last surveyed one that contains it. */
function nodeAt(nodes: ScreenNode[], x: number, y: number): ScreenNode | null {
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index]!;
    const [left, top, width, height] = node.rect;
    if (width > 0 && height > 0 && x >= left && y >= top && x <= left + width && y <= top + height) {
      return node;
    }
  }
  return null;
}

function Editor({ project, folder, name, state, render, gesture }: {
  project: string;
  folder: string;
  name: string;
  state: ScreenState;
  render: CardRender | null;
  gesture: Gesture;
}) {
  const preview = state.preview!;
  const scenes = useSceneOptions(state, render);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [zoom, setZoom] = useState<Zoom>("fit");
  const [commenting, setCommenting] = useState(false);
  useEffect(() => setCommenting(false), [selected]);
  const stage = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const pan = usePan(frameRef, zoom !== "fit");
  const nodes = state.nodes;
  const byPath = useMemo(() => new Map(nodes.map((node) => [node.path, node])), [nodes]);
  const current = selected ? byPath.get(selected) ?? null : null;
  const undoable = state.commits[0]?.studio ?? false;

  // An element gone from the survey (hidden in the render, renamed) is no longer selected.
  useEffect(() => {
    if (selected && !byPath.has(selected)) setSelected(null);
  }, [byPath, selected]);

  const point = (event: MouseEvent<HTMLDivElement>) => {
    const box = stage.current?.getBoundingClientRect();
    if (!box || box.width === 0) return null;
    const ratio = preview.width / box.width;
    return nodeAt(nodes, (event.clientX - box.left) * ratio, (event.clientY - box.top) * ratio);
  };
  const frame = (node: ScreenNode) => {
    const [x, y, w, h] = node.rect;
    return {
      left: `${(x / preview.width) * 100}%`, top: `${(y / preview.height) * 100}%`,
      width: `${(w / preview.width) * 100}%`, height: `${(h / preview.height) * 100}%`,
    };
  };
  const hover = hovered && hovered !== selected ? byPath.get(hovered) : null;
  // The element's tag stays inside the image: above the element when there
  // is room, else under it, else inside its top edge (an element that covers
  // the whole screen); aligned on its right edge past the middle.
  const tagPlace = (node: ScreenNode) => {
    const [x, y, w, h] = node.rect;
    const scale = zoom === "half" ? 0.5 : zoom === "full" ? 1
      : (stage.current?.getBoundingClientRect().width ?? 0) / preview.width || 0.4;
    const room = TAG_PX / scale;
    const place = y >= room ? "" : preview.height - (y + h) >= room ? "is-below" : "is-inside";
    return `${place} ${x + w / 2 > preview.width / 2 ? "is-end" : ""}`;
  };

  return (
    <div className="screen-editor">
      <section className="panel screen-pane screen-stage-pane">
        <div className="panel-head screen-stage-head">
          <Toolbar className="screen-toolbar">
            <ToolGroup>
              <Select label={t("Screen")} className="screen-pick" value={scenes.current}
                      options={scenes.options}
                      onChange={(scene) => {
                        if (!gesture.isPending) gesture.mutate({ kind: "open", scene });
                      }} />
            </ToolGroup>
            <ToolGroup end>
              {gesture.isPending && <Badge tone="quiet">{t("rendering…")}</Badge>}
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                aria-label={t("Undo")}
                disabled={!undoable || gesture.isPending}
                title={undoable ? `${t("Undo")} · ${state.commits[0]!.subject}` : t("Undo")}
                onClick={() => gesture.mutate({ kind: "undo" })}
              >
                <UndoGlyph />
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-icon"
                aria-label={t("Redraw")}
                title={t("Redraw")}
                disabled={gesture.isPending}
                onClick={() => gesture.mutate({ kind: "render" })}
              >
                <RedrawAllGlyph />
              </button>
            </ToolGroup>
            <FakeData project={project} gesture={gesture} />
          </Toolbar>
        </div>
        <div className="screen-stage-body">
          <div className="screen-zoom" title={t("Zoom")}>
            <Seg<Zoom> value={zoom} options={ZOOMS} onChange={setZoom} />
          </div>
          <div ref={frameRef} className={`screen-stage board is-${zoom}${pan.active ? " is-panning" : ""}`}
               {...pan.handlers}>
            <div
              ref={stage}
              className={`screen-canvas ${zoom === "fit" ? "" : "is-sized"}`}
              onMouseMove={(event) => setHovered(pan.active ? null : point(event)?.path ?? null)}
              onMouseLeave={() => setHovered(null)}
              onClick={(event) => {
                // The end of a drag is not a pick.
                if (pan.consumeDrag()) return;
                setSelected(point(event)?.path ?? null);
              }}
            >
              <img
                style={zoom === "fit" ? undefined
                  : { width: preview.width / (zoom === "half" ? 2 : 1), maxWidth: "none",
                      maxHeight: "none" }}
                src={screenPreviewUrl(project, folder, name, preview.rendered_at)}
                alt={state.scene}
                draggable={false}
              />
              {hover && <span className="screen-box is-hover" style={frame(hover)} />}
              {current && (
                <span className="screen-box is-selected" style={frame(current)}>
                  <span className={`screen-tag mono ${tagPlace(current)}`}>
                    {current.name}
                    <CommentButton node={current} open={commenting}
                                   onToggle={() => setCommenting((open) => !open)} />
                  </span>
                </span>
              )}
            </div>
          </div>
        </div>
        <footer className="screen-stage-foot">
          {state.opened && (
            <>
              <span className="mono" title={state.checkout}>{state.branch}</span>
              <span className="num">{tn(state.commits.length, "{n} change", "{n} changes")}</span>
            </>
          )}
          <span className="num">{shortDate(preview.rendered_at)}</span>
          {gesture.isError && <Badge tone="danger">{(gesture.error as Error).message}</Badge>}
        </footer>
      </section>

      <div className="screen-side">
        <Layers nodes={nodes} selected={selected} hovered={hovered}
                onSelect={setSelected} onHover={setHovered} />
        <Inspector project={project} folder={folder} name={name} node={current}
                   commenting={commenting} onCommenting={setCommenting} />
        <Comments project={project} folder={folder} name={name} selected={selected}
                  onSelect={setSelected} />
      </div>
    </div>
  );
}

/** Room a tag needs above or under its element, in screen pixels. */
const TAG_PX = 28;
/** How far the pointer travels before a press becomes a drag rather than a pick. */
const DRAG_PX = 4;

/**
 * Moving around a zoomed render by dragging it, as in a design tool: a press
 * that travels pans the frame, a press that stays put is a pick. The middle
 * button always pans. Fitted, there is nothing to move.
 */
function usePan(frame: RefObject<HTMLDivElement | null>, enabled: boolean) {
  const start = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null);
  const dragged = useRef(false);
  const [active, setActive] = useState(false);

  const handlers = enabled ? {
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      dragged.current = false;
      if ((event.button !== 0 && event.button !== 1) || !frame.current) return;
      start.current = {
        x: event.clientX, y: event.clientY,
        left: frame.current.scrollLeft, top: frame.current.scrollTop,
        moved: event.button === 1,
      };
      if (event.button === 1) {
        // The middle button would start the engine's autoscroll.
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        setActive(true);
      }
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      const from = start.current;
      if (!from || !frame.current) return;
      const dx = event.clientX - from.x;
      const dy = event.clientY - from.y;
      if (!from.moved && Math.hypot(dx, dy) < DRAG_PX) return;
      if (!from.moved) {
        // Captured only once it is a drag: a plain click keeps reaching the
        // image, which picks the element under it.
        from.moved = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        setActive(true);
      }
      frame.current.scrollLeft = from.left - dx;
      frame.current.scrollTop = from.top - dy;
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (start.current?.moved && event.button === 0) dragged.current = true;
      start.current = null;
      setActive(false);
    },
    onPointerCancel: () => {
      start.current = null;
      setActive(false);
    },
  } : {};

  return {
    handlers,
    active,
    /** True once after a drag: the click that ends it selects nothing. */
    consumeDrag: () => {
      const was = dragged.current;
      dragged.current = false;
      return was;
    },
  };
}

/** The layers: the tree of visible elements, in scene order. */
function Layers({ nodes, selected, hovered, onSelect, onHover }: {
  nodes: ScreenNode[];
  selected: string | null;
  hovered: string | null;
  onSelect: (path: string) => void;
  onHover: (path: string | null) => void;
}) {
  return (
    <Panel className="screen-pane" title={t("Layers")} eyebrow={<span className="num">{nodes.length}</span>}
           bodyClass="tight">
      <ul className="screen-layers" onMouseLeave={() => onHover(null)}>
        {nodes.map((node) => {
          const depth = node.path === "." ? 0 : node.path.split("/").length;
          return (
            <li key={node.path}>
              <button
                type="button"
                className={`screen-layer ${node.path === selected ? "is-selected" : ""} ${
                  node.path === hovered ? "is-hover" : ""} ${node.editable ? "" : "is-muted"}`}
                style={{ paddingLeft: `calc(var(--space-2) + ${depth} * var(--space-3))` }}
                aria-label={`${node.name} — ${node.type}`}
                onClick={() => onSelect(node.path)}
                onMouseEnter={() => onHover(node.path)}
              >
                <span className="screen-layer-name">{node.name}</span>
                <span className="mono screen-layer-type">{node.type}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/** The selected element: its name, its type, its file, and a comment to hand to the agent. */
function Inspector({ project, folder, name, node, commenting, onCommenting }: {
  project: string;
  folder: string;
  name: string;
  node: ScreenNode | null;
  commenting: boolean;
  onCommenting: (open: boolean) => void;
}) {
  if (!node) {
    return (
      <Panel className="screen-pane" title={t("Selection")} bodyClass="screen-inspector">
        <Empty title={t("No element selected")} />
      </Panel>
    );
  }
  return (
    <Panel
      className="screen-pane is-selection"
      title={node.name}
      eyebrow={node.type}
      actions={(
        <span className="screen-pane-actions">
          {node.shared && <Badge tone="warn">{t("shared")}</Badge>}
          <CommentButton node={node} open={commenting} onToggle={() => onCommenting(!commenting)} />
        </span>
      )}
      bodyClass="screen-inspector"
    >
      <span className="mono screen-inspector-file">{node.file || t("created by code")}</span>
      {commenting && (
        <CommentComposer project={project} folder={folder} name={name} node={node}
                         onDone={() => onCommenting(false)} />
      )}
    </Panel>
  );
}

/** Opens the comment composer: in the inspector's head and on the element's tag. */
function CommentButton({ node, open, onToggle }: {
  node: ScreenNode;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="btn btn-ghost btn-icon btn-sm screen-comment-open"
      aria-label={t("Comment on {name}", { name: node.name })}
      aria-pressed={open}
      title={t("Comment")}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      <ChatPlusGlyph />
    </button>
  );
}

/** A comment on the selected element: kept for later, or handed to the agent now. */
function CommentComposer({ project, folder, name, node, onDone }: {
  project: string;
  folder: string;
  name: string;
  node: ScreenNode;
  onDone: () => void;
}) {
  const [text, setText] = useState("");
  const gesture = useScreenCommentGesture(project, folder, name);
  const write = (send: boolean) =>
    gesture.mutate({ kind: "add", path: node.path, text, send }, {
      onSuccess: () => {
        setText("");
        onDone();
      },
    });
  const empty = !text.trim();

  return (
    <div className="screen-composer">
      <textarea
        className="input"
        rows={3}
        autoFocus
        value={text}
        placeholder={t("What should change on {name}?", { name: node.name })}
        aria-label={t("Comment on {name}", { name: node.name })}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !empty) write(true);
        }}
      />
      <div className="screen-composer-actions">
        {gesture.isError && <Badge tone="danger">{(gesture.error as Error).message}</Badge>}
        <button type="button" className="btn btn-ghost btn-sm" disabled={empty || gesture.isPending}
                onClick={() => write(false)}>
          {t("Save")}
        </button>
        <button type="button" className="btn btn-primary btn-sm"
                disabled={empty || gesture.isPending} onClick={() => write(true)}>
          {gesture.isPending ? t("Sending…") : t("Process now")}
        </button>
      </div>
    </div>
  );
}

/** Where a comment stands with the screen's agent. */
function CommentState({ comment, place }: { comment: ScreenComment; place: number }) {
  switch (comment.state) {
    case "queued":
      return <State state="pending" label={t("queued · {place}", { place })} />;
    case "sent":
      return <State state="running" label={t("in progress")} />;
    case "done":
      return <State state="done" label={t("processed")} />;
    default:
      return <Badge tone="quiet">{t("saved")}</Badge>;
  }
}

/** The screen's comments: picked, then sent to its agent one at a time, in order. */
function Comments({ project, folder, name, selected, onSelect }: {
  project: string;
  folder: string;
  name: string;
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const { data, error } = useScreenComments(project, folder, name);
  const gesture = useScreenCommentGesture(project, folder, name);
  const [picked, setPicked] = useState<number[]>([]);
  const comments = data?.comments ?? [];
  const free = (comment: ScreenComment) => comment.state === "saved" || comment.state === "done";
  // What is still picked and can be: a comment sent meanwhile drops out.
  const chosen = picked.filter((id) => comments.some((c) => c.id === id && free(c)));
  const queue = comments.filter((c) => c.state === "queued").map((c) => c.id);
  const toggle = (id: number) =>
    setPicked((now) => (now.includes(id) ? now.filter((other) => other !== id) : [...now, id]));
  const run = (kind: "send" | "remove") =>
    gesture.mutate({ kind, ids: chosen }, { onSuccess: () => setPicked([]) });

  if (error && !data) return <Badge tone="danger">{(error as Error).message}</Badge>;
  if (comments.length === 0) return null;

  return (
    <Panel
      className="screen-pane"
      title={t("Comments")}
      eyebrow={<span className="num">{comments.length}</span>}
      actions={data?.session && (
        <button type="button" className="btn btn-ghost btn-sm"
                onClick={() => void focusChat(data.session!.id)}>
          {data.session.title}
        </button>
      )}
      bodyClass="tight"
      foot={(
        <div className="screen-composer-actions">
          {gesture.isError && <Badge tone="danger">{(gesture.error as Error).message}</Badge>}
          <button type="button" className="btn btn-ghost btn-icon btn-sm"
                  aria-label={t("Remove the picked comments")} title={t("Remove")}
                  disabled={chosen.length === 0 || gesture.isPending} onClick={() => run("remove")}>
            <TrashGlyph />
          </button>
          <button type="button" className="btn btn-primary btn-sm"
                  disabled={chosen.length === 0 || gesture.isPending} onClick={() => run("send")}>
            {chosen.length > 0 ? tn(chosen.length, "Send {n} comment", "Send {n} comments")
              : t("Send")}
          </button>
        </div>
      )}
    >
      <ul className="screen-comment-list">
        {comments.map((comment) => (
          <li key={comment.id}
              className={`screen-comment ${comment.path === selected ? "is-selected" : ""}`}>
            <input
              type="checkbox"
              className="screen-comment-pick"
              checked={chosen.includes(comment.id)}
              disabled={!free(comment)}
              aria-label={t("Pick comment {id}", { id: comment.id })}
              onChange={() => toggle(comment.id)}
            />
            <button type="button" className="screen-comment-body"
                    onClick={() => onSelect(comment.path)}>
              <span className="screen-comment-on mono">{comment.name}</span>
              <span className="screen-comment-text">{comment.text}</span>
            </button>
            <CommentState comment={comment} place={queue.indexOf(comment.id) + 1} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** The game's fake server answers: on or off for the renders, written by an agent. */
function FakeData({ project, gesture }: { project: string; gesture: Gesture }) {
  const { data } = usePreviewData(project);
  const enable = usePreviewDataEnable(project);
  const [handing, setHanding] = useState(false);
  const missing = data?.misses.length ?? 0;
  const working = Boolean(data?.agent?.working);
  // The agent handed back: its data is drawn at once.
  const wasWorking = useRef(working);
  useEffect(() => {
    if (wasWorking.current && !working && !gesture.isPending) gesture.mutate({ kind: "render" });
    wasWorking.current = working;
  }, [working, gesture]);

  if (!data || (!data.exists && data.networked.length === 0)) return null;

  return (
    <ToolGroup label={t("Fake data")}>
      {working && (
        <button type="button" className="btn btn-ghost btn-sm"
                onClick={() => void focusChat(data.agent!.id)}>
          <State state="running" label={t("being written")} />
        </button>
      )}
      {data.exists && (
        <Toggle
          checked={data.enabled}
          disabled={enable.isPending || gesture.isPending}
          label={t("Fake data")}
          onChange={(on) =>
            enable.mutate(on, { onSuccess: () => gesture.mutate({ kind: "render" }) })}
        />
      )}
      {data.enabled && missing > 0 && !working && (
        <Badge tone="warn">{tn(missing, "{n} request without data", "{n} requests without data")}</Badge>
      )}
      <button
        type="button"
        className="btn btn-ghost btn-icon btn-sm"
        aria-label={data.exists ? t("Complete the fake data") : t("Prepare fake data")}
        title={data.exists ? t("Complete the fake data") : t("Prepare fake data")}
        disabled={working}
        onClick={() => setHanding(true)}
      >
        <DataGlyph />
      </button>
      {enable.isError && <Badge tone="danger">{(enable.error as Error).message}</Badge>}
      {handing && (
        <HandoffDialog
          title={data.exists ? t("Complete the fake data") : t("Prepare fake data")}
          eyebrow={project}
          load={() => api.previewDataBrief(project)}
          rows={(brief) => [
            [t("Routes"), <span className="num">{brief.routes}</span>],
            [t("Requests without data"), <span className="num">{brief.misses.length}</span>],
          ]}
          submit={(choice) => api.previewDataHandoff(project, choice)}
          sent={(session) => t("Fake data handed to {title}", { title: session.title })}
          onClose={() => setHanding(false)}
        />
      )}
    </ToolGroup>
  );
}

/** An arrow turning back: undo the last change. */
function UndoGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 14.5L4.5 10 9 5.5M4.5 10h10a5 5 0 0 1 0 10H11" />
    </svg>
  );
}

/** A small stack of records: data served in place of the game's server. */
function DataGlyph() {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <ellipse cx="12" cy="6" rx="7" ry="2.5" />
      <path d="M5 6v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6" />
      <path d="M5 12v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-6" />
    </svg>
  );
}
