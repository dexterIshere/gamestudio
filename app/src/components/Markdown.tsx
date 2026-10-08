/**
 * A minimal Markdown renderer, for the texts the studio produces and reads.
 *
 * It covers exactly what these files contain: headings, lists (an item may
 * carry others, indented below), code blocks, quotes, rules, images
 * (`![caption](path)`, alone on its line for a figure, its italic caption just
 * below), and inline markup (`code`, **bold**, *italic*, [link](url)). Nothing
 * more: a full engine would be a dependency to maintain for texts written
 * here. An image path is resolved by the page showing the text: only it knows
 * where the text lives.
 *
 * The layout serves cards first, which are skimmed before being read: each
 * `##` opens a clearly separated section, the card's opening sentence reads as
 * a lead, the bold label opening an item stands apart from its definition, and
 * the game survey (written by an agent, for agents) stays folded.
 *
 * Text is rendered as text: never interpreted HTML. These files are editable
 * in the interface, so a `dangerouslySetInnerHTML` would be an open door;
 * building React elements closes it by construction. A link never navigates
 * the window: to a sibling document, the page opens it itself; to the web, it
 * goes to `_blank`, which the shell hands to the system browser
 * (`tauri-plugin-opener`); everything else is inert.
 */

import { Fragment, type ReactNode } from "react";

/** A list item: its text, and the list it carries if any. */
export interface ListItem {
  text: string;
  sub: ListBlock | null;
}

export interface ListBlock {
  kind: "list";
  ordered: boolean;
  items: ListItem[];
}

export type Block =
  | { kind: "heading"; level: number; text: string }
  | ListBlock
  | { kind: "code"; lines: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "hr" }
  | { kind: "image"; alt: string; src: string; caption: string | null }
  | { kind: "para"; lines: string[] };

const HEADING = /^(#{1,4})\s+(.*)$/;
// A bullet (`-`, `*`, `+`) or a number, after its indentation: the indentation
// says which list level the item belongs to.
const ITEM = /^(\s*)([-*+]|\d+[.)])(?:\s+(.*))?$/;
const IMAGE = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/;
const RULE = /^(-{3,}|\*{3,})$/;
// A figure's caption: a paragraph entirely in italics, below it.
const CAPTIONS = [/^\*(?![*\s])([^*]*[^*\s])\*$/, /^_(?![_\s])([^_]*[^_\s])_$/];
// The game survey ("Relevé du jeu" in French cards): the section an agent
// keeps up to date for the next ones.
const FOLDED = /^(game\s+survey|relev[ée]\s+du\s+jeu)\b/i;
// "**Header** — what it shows", "**Tap** → open it", or "**Header:** …": the
// label opening an item, its separator, its definition.
const TERM = /^\*\*([^*]+)\*\*\s*(—|–|→|:|-(?=\s)|$)\s*([\s\S]*)$/;
const TERM_COLON = /^\*\*([^*]+?)\s*:\*\*\s*([\s\S]*)$/;
// Inline markup, in recognition order. A single capturing group: odd-ranked
// pieces are tokens, the others are text.
const INLINE = new RegExp(
  [
    // Bold may carry italics: `**bold *nested* end**`.
    "(\\*\\*(?:[^*]|\\*(?!\\*))+?\\*\\*",
    "`[^`]+`",
    "!\\[[^\\]]*\\]\\([^)]+\\)",
    "\\[[^\\]]+\\]\\([^)]+\\)",
    "\\*(?![\\s*])[^*]*[^\\s*]\\*",
    // `_italic_`, never inside a word: `walk_loop` stays as is.
    "(?<![\\p{L}\\p{N}_])_(?![\\s_])[^_]*[^\\s_]_(?![\\p{L}\\p{N}_]))",
  ].join("|"),
  "u",
);
// A link with a scheme: the web opens outside, the rest does not open.
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
const WEB = /^(https?:|mailto:)/i;

/** Turns an image path into a URL, or says it cannot. */
export type ImageResolver = (src: string) => string | null;

/**
 * What a relative link to a document (`top-bar.md`) becomes: a way to open it
 * in the page, or `null` when the page does not know it.
 */
export type DocumentLinker = (href: string) => (() => void) | null;

/** What inline rendering needs to know about the page showing the text. */
interface Context {
  resolveImage?: ImageResolver;
  hideImage?: (src: string) => boolean;
  linkDocument?: DocumentLinker;
}

/**
 * Strips `<!-- … -->` comments, outside code blocks.
 *
 * Document templates put their instructions there: they are read in the raw
 * text, and have no place in the rendering.
 */
function stripComments(text: string): string {
  return text
    .split(/(^```[\s\S]*?^```)/m)
    .map((part, index) => (index % 2 ? part : part.replace(/<!--[\s\S]*?-->/g, "")))
    .join("");
}

const indentOf = (line: string): number => line.length - line.trimStart().length;
const isOrdered = (marker: RegExpMatchArray): boolean => /\d/.test(marker[2] ?? "");

/** The deepest item under an item: an indented line continues that one. */
function deepest(item: ListItem): ListItem {
  let cursor = item;
  while (cursor.sub?.items.length) cursor = cursor.sub.items[cursor.sub.items.length - 1]!;
  return cursor;
}

/** Splits a text into blocks. Exported so it can be tested without a DOM. */
export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  // A tab counts as four spaces: indentation is what nests the items.
  const lines = stripComments(text.replace(/\r\n/g, "\n")).replace(/\t/g, "    ").split("\n");
  // The project compiles with `noUncheckedIndexedAccess`: lines are read
  // through this helper, never by a bare index, so that a missing line is an
  // empty string, not an `undefined` that travels up to the rendering.
  const at = (position: number): string => lines[position] ?? "";
  const starts = (position: number, prefix: string): boolean =>
    at(position).startsWith(prefix);
  const blank = (position: number): boolean => !at(position).trim();
  const filled = (position: number): number => {
    let cursor = position;
    while (cursor < lines.length && blank(cursor)) cursor += 1;
    return cursor;
  };

  /** A paragraph's lines: up to a blank line, or another block. */
  const paragraph = (start: number): [string[], number] => {
    const body: string[] = [];
    let index = start;
    while (index < lines.length && !blank(index) && !HEADING.test(at(index))
      && !ITEM.test(at(index)) && !starts(index, ">") && !starts(index, "```")
      && !IMAGE.test(at(index).trim()) && !RULE.test(at(index).trim())) {
      body.push(at(index).trim());
      index += 1;
    }
    return [body, index];
  };

  /**
   * A list, from its first bullet: its items, and those they carry. `depth`
   * is the indentation of its bullets; a bullet indented by at least two more
   * spaces opens the previous item's list.
   */
  const readList = (start: number, depth: number, ordered: boolean): [ListBlock, number] => {
    const items: ListItem[] = [];
    let index = start;
    while (index < lines.length) {
      if (blank(index)) {
        // A blank line only ends the list if what follows is no longer part of it.
        const next = filled(index);
        const marker = at(next).match(ITEM);
        const indent = indentOf(at(next));
        const goesOn = next < lines.length
          && (marker ? indent >= depth : items.length > 0 && indent >= depth + 2);
        if (!goesOn) break;
        index = next;
        continue;
      }
      const line = at(index);
      const indent = indentOf(line);
      const marker = line.match(ITEM);
      if (marker) {
        if (indent < depth) break; // the parent list resumes
        const last = items[items.length - 1];
        if (last && indent >= depth + 2) {
          const [sub, next] = readList(index, indent, isOrdered(marker));
          last.sub = last.sub ? { ...last.sub, items: [...last.sub.items, ...sub.items] } : sub;
          index = next;
          continue;
        }
        if (isOrdered(marker) !== ordered) break; // another list starts
        items.push({ text: (marker[3] ?? "").trim(), sub: null });
        index += 1;
        continue;
      }
      // An indented line continues the previous item: that is how a long
      // sentence is written in a list.
      const last = items[items.length - 1];
      if (last && indent >= depth + 2) {
        const target = deepest(last);
        target.text = target.text ? `${target.text} ${line.trim()}` : line.trim();
        index += 1;
        continue;
      }
      break;
    }
    return [{ kind: "list", ordered, items }, index];
  };

  let index = 0;
  while (index < lines.length) {
    const line = at(index);

    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !starts(index, "```")) {
        body.push(at(index));
        index += 1;
      }
      index += 1; // the closing fence, or the end of the text
      blocks.push({ kind: "code", lines: body });
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "#").length,
        text: (heading[2] ?? "").trim(),
      });
      index += 1;
      continue;
    }

    const image = line.trim().match(IMAGE);
    if (image) {
      index += 1;
      // The caption: the italic paragraph after the image, if any.
      const [body, end] = paragraph(filled(index));
      const joined = body.join(" ");
      const italic = CAPTIONS.map((pattern) => joined.match(pattern)).find(Boolean);
      let caption: string | null = null;
      if (body.length && italic) {
        caption = italic[1] ?? null;
        index = end;
      }
      blocks.push({ kind: "image", alt: image[1] ?? "", src: image[2] ?? "", caption });
      continue;
    }

    if (RULE.test(line.trim())) {
      blocks.push({ kind: "hr" });
      index += 1;
      continue;
    }

    if (line.startsWith(">")) {
      const body: string[] = [];
      while (index < lines.length && starts(index, ">")) {
        body.push(at(index).replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", lines: body });
      continue;
    }

    const marker = line.match(ITEM);
    if (marker) {
      const [block, next] = readList(index, indentOf(line), isOrdered(marker));
      blocks.push(block);
      index = next;
      continue;
    }

    const [body, end] = paragraph(index);
    // A line nothing recognizes (an indented code fence, for example) stays
    // text: always move forward by at least one line.
    blocks.push({ kind: "para", lines: body.length ? body : [line.trim()] });
    index = body.length ? end : index + 1;
  }
  return blocks;
}

/** The file name of a path, without extension, query or anchor. */
function fileStem(src: string): string {
  const name = (src.split(/[?#]/)[0] ?? "").split("/").pop() ?? "";
  return name.replace(/\.[^.]+$/, "");
}

/** An image: its URL if the page can resolve it, its caption otherwise. */
function picture(alt: string, src: string, resolve: ImageResolver | undefined,
  className: string, key: string): ReactNode {
  const url = resolve?.(src) ?? null;
  if (!url) return <em key={key}>{alt || src}</em>;
  return <img key={key} className={className} src={url} alt={alt} loading="lazy" />;
}

/**
 * A link. The web goes to `_blank`: in the shell, `tauri-plugin-opener`
 * intercepts the click and opens it in the system browser, never navigating
 * the window. A sibling document is opened by the page itself. The rest (an
 * anchor, a disk path, an unknown scheme) is inert: an anchor would change the
 * URL fragment, which is the page's address.
 */
function anchor(label: ReactNode, href: string, key: string, context: Context): ReactNode {
  const target = href.trim();
  if (WEB.test(target)) {
    return (
      <a key={key} href={target} target="_blank" rel="noreferrer noopener" title={target}>
        {label}
      </a>
    );
  }
  const open = !SCHEME.test(target) && !target.startsWith("#")
    ? context.linkDocument?.(target) ?? null
    : null;
  if (open) {
    return (
      <a
        key={key}
        href={target}
        onClick={(event) => {
          event.preventDefault();
          open();
        }}
        onAuxClick={(event) => event.preventDefault()}
      >
        {label}
      </a>
    );
  }
  return <span key={key} className="md-inert" title={target}>{label}</span>;
}

/** Inline markup: `code`, **bold**, *italic*, ![image](path), [link](url). The rest is text. */
function inline(text: string, key: string, context: Context): ReactNode {
  return text.split(INLINE).map((part, position) => {
    const id = `${key}-${position}`;
    if (position % 2 === 0) return part ? <Fragment key={id}>{part}</Fragment> : null;
    const image = part.match(/^!\[([^\]]*)\]\(([^)\s]+)[^)]*\)$/);
    if (image) {
      if (context.hideImage?.(image[2] ?? "")) return null;
      return picture(image[1] ?? "", image[2] ?? "", context.resolveImage, "md-inline-image", id);
    }
    if (part.startsWith("**")) return <strong key={id}>{inline(part.slice(2, -2), id, context)}</strong>;
    if (part.startsWith("`")) return <code key={id} className="mono">{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) return anchor(inline(link[1] ?? "", id, context), link[2] ?? "", id, context);
    return <em key={id}>{inline(part.slice(1, -1), id, context)}</em>;
  });
}

/** A list item: its label set apart from its definition when it opens with one, then its list. */
function item(entry: ListItem, key: string, context: Context): ReactNode {
  const term = entry.text.match(TERM);
  const colon = term ? null : entry.text.match(TERM_COLON);
  const label = term?.[1] ?? colon?.[1] ?? null;
  const separator = term ? term[2] || "" : ":";
  const rest = (term?.[3] ?? colon?.[2] ?? "").trim();
  return (
    <li key={key} className={label !== null ? "has-term" : undefined}>
      {label !== null ? (
        <>
          <span className="md-term">{inline(label.trim(), `${key}-t`, context)}</span>
          {rest && (
            <>
              {" "}
              {separator && <span className="md-sep">{separator}</span>}
              {separator && " "}
              {inline(rest, `${key}-d`, context)}
            </>
          )}
        </>
      ) : (
        inline(entry.text, key, context)
      )}
      {entry.sub && listElement(entry.sub, `${key}-sub`, context)}
    </li>
  );
}

function listElement(block: ListBlock, key: string, context: Context): ReactNode {
  const items = block.items.map((entry, rank) => item(entry, `${key}-${rank}`, context));
  return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>;
}

function render(block: Block, key: string, context: Context, lead: boolean): ReactNode {
  const rich = (value: string) => inline(value, key, context);
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 1, 5)}` as "h2";
      return <Tag key={key}>{rich(block.text)}</Tag>;
    }
    case "list":
      return listElement(block, key, context);
    case "code":
      return (
        <pre key={key} className="mono">
          {block.lines.join("\n")}
        </pre>
      );
    case "quote":
      return <blockquote key={key}>{rich(block.lines.join(" "))}</blockquote>;
    case "hr":
      return <hr key={key} />;
    case "image": {
      // The caption written under the image, otherwise its alt text, unless
      // it only repeats the file name.
      const caption = block.caption
        ?? (block.alt && block.alt !== fileStem(block.src) ? block.alt : null);
      return (
        <figure key={key} className="md-figure">
          {picture(block.alt, block.src, context.resolveImage, "", `${key}-img`)}
          {caption && <figcaption>{rich(caption)}</figcaption>}
        </figure>
      );
    }
    default:
      return (
        <p key={key} className={lead ? "md-lead" : undefined}>
          {rich(block.lines.join(" "))}
        </p>
      );
  }
}

/** A part of the text: what precedes the first heading, then one section per `##`. */
interface Part {
  heading: { level: number; text: string } | null;
  blocks: Block[];
}

function parts(blocks: Block[]): Part[] {
  const found: Part[] = [{ heading: null, blocks: [] }];
  for (const block of blocks) {
    if (block.kind === "heading" && block.level <= 2) {
      found.push({ heading: block, blocks: [] });
    } else {
      found[found.length - 1]!.blocks.push(block);
    }
  }
  return found.filter((part) => part.heading || part.blocks.length);
}

export default function Markdown({
  text, className = "", resolveImage, titled = false, lead = false, hideImage, linkDocument,
}: {
  text: string;
  className?: string;
  /** An image path, resolved by the page that knows where the text lives. */
  resolveImage?: ImageResolver;
  /** The panel already shows the title: the leading `#` is not repeated. */
  titled?: boolean;
  /** The first paragraph as a lead: the sentence that sums up a card. */
  lead?: boolean;
  /** An image the page shows elsewhere: it leaves the text with its caption. */
  hideImage?: (src: string) => boolean;
  /** A link to a sibling document: the page opens it instead of navigating. */
  linkDocument?: DocumentLinker;
}) {
  const context: Context = { resolveImage, hideImage, linkDocument };
  let blocks = parseMarkdown(text);
  const first = blocks[0];
  if (titled && first?.kind === "heading" && first.level === 1) blocks = blocks.slice(1);
  if (hideImage) blocks = blocks.filter((block) => block.kind !== "image" || !hideImage(block.src));

  return (
    <div className={`md ${className}`.trim()}>
      {parts(blocks).map((part, rank) => {
        const key = `part-${rank}`;
        const body = part.blocks.map((block, position) =>
          render(block, `${key}-${position}`, context,
            lead && rank === 0 && position === 0 && !part.heading));
        if (!part.heading) return <Fragment key={key}>{body}</Fragment>;

        if (FOLDED.test(part.heading.text)) {
          // "Game survey — 2026-10-05": the label, then the date in figures.
          const [, label = part.heading.text, when = ""] =
            part.heading.text.match(/^(.*?)\s+[—–-]\s+(.+)$/) ?? [];
          return (
            <details key={key} className="more md-fold">
              <summary>
                <span>{inline(label, `${key}-h`, context)}</span>
                {when && <span className="num">{inline(when, `${key}-w`, context)}</span>}
              </summary>
              <div className="more-body md-fold-body">{body}</div>
            </details>
          );
        }

        const Tag = `h${Math.min(part.heading.level + 1, 5)}` as "h2";
        return (
          <section key={key} className="md-section">
            <Tag>{inline(part.heading.text, `${key}-h`, context)}</Tag>
            {body}
          </section>
        );
      })}
    </div>
  );
}
