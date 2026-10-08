/**
 * The editor of a game screen: point at an element on the render, change its properties.
 *
 * It is the "Edit" tab of an Interface or Props card, modelled on design
 * tools: on the left the screen as Godot draws it, where an element is hovered
 * and picked; on the right its layers and properties. "Apply" rewrites the
 * scene on the card's branch (`studio/screen|prop-<card>`, in a separate copy
 * of the game: the user's own checkout does not move), commits it and redraws.
 * The user merges the branch when the screen suits them.
 *
 * The service is `service/screens.py`; the rectangles come from the engine
 * (`render_scene.gd`, survey of the Controls), in preview pixels.
 */

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import {
  documentImageUrl, screenPreviewUrl, type CardRender, type ScreenNode, type ScreenProperty,
  type ScreenState, type ScreenValue,
} from "../api";
import { useScreen, useScreenGesture, useScreenNode } from "../lib/queries";
import { Badge, Callout, Empty, Seg, Select, Toggle, ToolGroup, Toolbar, shortDate } from "./ui";
import { t, tc, tn, tr } from "../lib/i18n";

/** The sections whose cards are edited on the render: a screen, a prop. */
export const SCREEN_FOLDERS = ["design/interface", "design/props"];

type Zoom = "fit" | "half" | "full";

/** Fitted to the space, or in preview pixels: a HUD reads poorly on the whole screen. */
const ZOOMS: { value: Zoom; label: string }[] = [
  { value: "fit", label: t("Fitted") },
  { value: "half", label: "50 %" },
  { value: "full", label: "100 %" },
];

/** A game icon seen from a card: the game root is four levels up. */
const GAME_ROOT_FROM_CARD = "../../../../";

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
  if (!state.opened || !state.preview) {
    return <OpenScreen state={state} render={render} gesture={gesture} />;
  }
  return <Editor project={project} folder={folder} name={name} state={state} gesture={gesture} />;
}

type Gesture = ReturnType<typeof useScreenGesture>;

/** Before the first edit: choose the screen, open its branch. */
function OpenScreen({ state, render, gesture }: {
  state: ScreenState;
  render: CardRender | null;
  gesture: Gesture;
}) {
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
  const [scene, setScene] = useState(
    options.find((entry) => sameScene(entry.value, initial))?.value ?? options[0]?.value ?? "",
  );

  return (
    <div className="screen-open">
      {options.length === 0 ? (
        <Empty title={t("No screen in the game")} />
      ) : (
        <>
          <div className="screen-open-row">
            <Select label={t("Screen")} value={scene} options={options} onChange={setScene} />
            <button
              type="button"
              className="btn btn-primary"
              disabled={!scene || gesture.isPending}
              onClick={() => gesture.mutate({ kind: "open", scene })}
            >
              {gesture.isPending ? t("Opening…") : t("Edit on a branch")}
            </button>
          </div>
          <span className="mono screen-branch">{state.branch}</span>
        </>
      )}
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

function Editor({ project, folder, name, state, gesture }: {
  project: string;
  folder: string;
  name: string;
  state: ScreenState;
  gesture: Gesture;
}) {
  const preview = state.preview!;
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [zoom, setZoom] = useState<Zoom>("fit");
  const stage = useRef<HTMLDivElement>(null);
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

  return (
    <div className="screen-editor">
      <div className="screen-stage-wrap">
        <div className={`screen-stage board is-${zoom}`}>
          <div
            ref={stage}
            className={`screen-canvas ${zoom === "fit" ? "" : "is-sized"}`}
            onMouseMove={(event) => setHovered(point(event)?.path ?? null)}
            onMouseLeave={() => setHovered(null)}
            onClick={(event) => setSelected(point(event)?.path ?? null)}
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
                <span className="screen-tag mono">{current.name}</span>
              </span>
            )}
          </div>
        </div>
        <Toolbar className="card-render-meta">
          <ToolGroup>
            <span className="mono" title={state.checkout}>{state.branch}</span>
            <span className="num">
              {tn(state.commits.length, "{n} change", "{n} changes")}
            </span>
            <span className="num">{shortDate(preview.rendered_at)}</span>
          </ToolGroup>
          <ToolGroup label={t("Zoom")}>
            <Seg<Zoom> value={zoom} options={ZOOMS} onChange={setZoom} />
          </ToolGroup>
          <ToolGroup end>
            {gesture.isPending && <Badge tone="quiet">{t("rendering…")}</Badge>}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!undoable || gesture.isPending}
              title={undoable ? state.commits[0]!.subject : undefined}
              onClick={() => gesture.mutate({ kind: "undo" })}
            >
              {t("Undo")}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={gesture.isPending}
              onClick={() => gesture.mutate({ kind: "render" })}
            >
              {t("Redraw")}
            </button>
          </ToolGroup>
        </Toolbar>
        {gesture.isError && <Badge tone="danger">{(gesture.error as Error).message}</Badge>}
      </div>

      <div className="screen-side">
        <Layers nodes={nodes} selected={selected} hovered={hovered}
                onSelect={setSelected} onHover={setHovered} />
        {current ? (
          <Inspector project={project} folder={folder} name={name} node={current}
                     version={preview.rendered_at} gesture={gesture} />
        ) : (
          <Empty title={t("No element selected")} />
        )}
      </div>
    </div>
  );
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
  );
}

type Draft = Record<string, ScreenValue>;

/** An element's properties, grouped; "Apply" writes everything that changed. */
function Inspector({ project, folder, name, node, version, gesture }: {
  project: string;
  folder: string;
  name: string;
  node: ScreenNode;
  version: string;
  gesture: Gesture;
}) {
  const detail = useScreenNode(project, folder, name, node.path, version);
  const [draft, setDraft] = useState<Draft>({});
  useEffect(() => setDraft({}), [node.path, version]);

  const data = detail.data;
  const groups = useMemo(() => {
    const found = new Map<string, ScreenProperty[]>();
    for (const prop of data?.properties ?? []) {
      found.set(prop.group, [...(found.get(prop.group) ?? []), prop]);
    }
    return [...found.entries()];
  }, [data]);
  const changed = Object.keys(draft).length > 0;

  return (
    <div className="screen-inspector">
      <header className="screen-inspector-head">
        <strong>{node.name}</strong>
        <span className="mono">{node.type}</span>
        {node.shared && <Badge tone="warn">{t("shared")}</Badge>}
        {data?.editable && (
          <span className="screen-apply">
            <button type="button" className="btn btn-ghost btn-sm" disabled={!changed}
                    onClick={() => setDraft({})}>
              {t("Restore")}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={!changed || gesture.isPending}
              onClick={() => gesture.mutate({ kind: "edit", path: node.path, changes: draft })}
            >
              {gesture.isPending ? t("Writing…") : t("Apply")}
            </button>
          </span>
        )}
      </header>
      <span className="mono screen-inspector-file">{node.file || t("created by code")}</span>
      {detail.error && <Badge tone="danger">{(detail.error as Error).message}</Badge>}
      {!data ? (
        !detail.error && <Empty title={t("Reading…")} />
      ) : !data.editable ? (
        <Callout>{t("Created by code: it is changed in its script.")}</Callout>
      ) : (
        <>
          {groups.map(([group, props]) => (
            <section key={group} className="screen-group">
              <h4>{tr(group)}</h4>
              {props.map((prop) => (
                <PropertyRow
                  key={prop.key}
                  project={project}
                  folder={folder}
                  prop={prop}
                  icons={data.icons}
                  value={prop.key in draft ? draft[prop.key]! : (prop.value ?? null) as ScreenValue}
                  touched={prop.key in draft}
                  onChange={(value) => setDraft((current) => ({ ...current, [prop.key]: value }))}
                />
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  );
}

/** `#rrggbbaa` → `#rrggbb` for the picker, which ignores opacity. */
const opaque = (hex: string) => hex.slice(0, 7);

function PropertyRow({ project, folder, prop, icons, value, touched, onChange }: {
  project: string;
  folder: string;
  prop: ScreenProperty;
  icons: { res: string; file: string; size: string }[];
  value: ScreenValue;
  touched: boolean;
  onChange: (value: ScreenValue) => void;
}) {
  const unset = value === null || value === undefined;
  const reset = !unset && (
    <button type="button" className="btn btn-ghost btn-icon screen-reset" title={t("Default value")}
            onClick={() => onChange(null)}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
    </button>
  );
  const number = (raw: string) => (raw.trim() === "" ? null : Number(raw));
  let input;
  switch (prop.kind) {
    case "text":
      input = (
        <textarea rows={String(value ?? "").includes("\n") ? 3 : 1} placeholder={t("default")}
                  value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />
      );
      break;
    case "int":
    case "float":
      input = (
        <input type="number" step={prop.kind === "int" ? 1 : "any"} placeholder={t("default")}
               className="num" value={unset ? "" : String(value)}
               onChange={(event) => onChange(number(event.target.value))} />
      );
      break;
    case "bool":
      input = <Toggle checked={value === true} label={tr(prop.label)} onChange={onChange} />;
      break;
    case "color": {
      const hex = typeof value === "string" && value.startsWith("#") ? value : "";
      input = (
        <span className="screen-color">
          <input type="color" value={hex ? opaque(hex) : "#ffffff"}
                 onChange={(event) => onChange(`${event.target.value}${hex.slice(7) || "ff"}`)} />
          <input type="text" className="mono" placeholder={t("default")} value={hex}
                 onChange={(event) => onChange(event.target.value || null)} />
        </span>
      );
      break;
    }
    case "vector2": {
      const pair = Array.isArray(value) ? value : null;
      const set = (index: 0 | 1, raw: string) => {
        const next: [number, number] = pair ? [...pair] : [0, 0];
        next[index] = Number(raw) || 0;
        onChange(next);
      };
      input = (
        <span className="screen-pair">
          <input type="number" className="num" placeholder="x" value={pair ? pair[0] : ""}
                 onChange={(event) => set(0, event.target.value)} />
          <input type="number" className="num" placeholder="y" value={pair ? pair[1] : ""}
                 onChange={(event) => set(1, event.target.value)} />
        </span>
      );
      break;
    }
    case "enum":
      input = (
        <Select
          label={tr(prop.label)}
          value={unset ? "" : String(value)}
          options={[{ value: "", label: t("default"), muted: true },
                    ...(prop.options ?? []).map(([key, label]) => ({ value: String(key), label: tr(label) }))]}
          onChange={(raw) => onChange(raw === "" ? null : Number(raw))}
        />
      );
      break;
    default: {
      const current = typeof value === "string" ? value : "";
      const known = icons.find((icon) => icon.res === current);
      input = (
        <span className="screen-texture">
          {known && (
            <img src={documentImageUrl(project, folder, `${GAME_ROOT_FROM_CARD}${known.file}`)}
                 alt="" />
          )}
          <Select
            label={tr(prop.label)}
            value={current}
            options={[
              { value: "", label: tc("texture", "none"), muted: true },
              ...(current && !known ? [{ value: current, label: current }] : []),
              ...icons.map((icon) => ({
                value: icon.res, label: icon.res.split("/").pop() ?? icon.res,
                detail: <span className="mono">{icon.size}</span>,
              })),
            ]}
            onChange={(raw) => onChange(raw || null)}
          />
        </span>
      );
    }
  }
  return (
    <div className={`screen-prop ${touched ? "is-touched" : ""}`}>
      <span className="label">{tr(prop.label)}</span>
      <span className="screen-prop-input">{input}{reset}</span>
    </div>
  );
}
