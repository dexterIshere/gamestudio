/**
 * The translations check: no hard-coded text, no text without its translation.
 *
 * The interface is written in English in the code, and every displayed text
 * goes through `t("…")`, `tn(n, "…", "…")` or `tc("context", "…")` (see
 * `src/lib/i18n.ts`). This check, run by `make check`, refuses three faults:
 *
 * - a displayed text outside those calls: JSX text, or a hard-coded textual
 *   attribute (`title`, `label`, `placeholder`…);
 * - a key that is not a literal, or has no entry in `src/locales/fr.ts`: a
 *   string for `t()`, a `[singular, plural]` tuple keyed by the plural for
 *   `tn()`, a string keyed `context|text` for `tc()`;
 * - an entry that no call uses any more.
 *
 * Server texts (`tr()`) are not checked here: their catalogue,
 * `src/locales/fr-server.json`, is checked by the Python tests, where the
 * messages are written.
 *
 *     node scripts/translations.mjs                   check (exit code 1 on a fault)
 *     node scripts/translations.mjs --candidates …    review: remaining literals
 */

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ts = createRequire(import.meta.url)(path.join(app, "node_modules/typescript"));

// Text that reads the same in both languages: proper names and acronyms.
const INVARIANT = new Set([
  "gamestudio", "Godot", "Blender", "Runware", "Tripo", "Excalidraw", "Claude", "Claude Code",
  "Codex", "Gemini", "Kimi", "DeepSeek", "Opus", "img2threejs", "Chats",
]);
// Attributes that never carry readable text.
const NOT_TEXT = new Set([
  "className", "style", "key", "id", "href", "src", "type", "name", "value", "defaultValue", "to",
  "kind", "tone", "variant", "icon", "role", "htmlFor", "accept", "target", "rel", "method",
  "autoComplete", "inputMode", "pattern", "step", "min", "max", "width", "height", "viewBox", "d",
  "fill", "stroke", "folder", "mode", "state", "status", "path", "langCode", "lang", "theme",
  "data-tauri-drag-region", "size", "align", "direction", "format", "encoding", "dir", "bodyClass",
]);
const CODE = /^(code|kbd|pre|samp)$/;

/** Readable text: a word that is neither a proper name nor an acronym. */
function readable(text) {
  const v = text.trim();
  return /[A-Za-zÀ-ÿ]{2,}/.test(v) && !INVARIANT.has(v) && !/^[A-Z0-9_.\-/ +×·]+$/.test(v);
}

/** An attribute value that looks like a sentence rather than an identifier. */
function sentence(text) {
  const v = text.trim();
  if (!readable(v)) return false;
  if (/^(https?:|\/|\.\/|#|--|var\(|rgba?\(|calc\(|url\()/.test(v)) return false;
  return /[À-ÿ…«»’]/.test(v) || (/\s/.test(v) && /[a-z]{2,}/.test(v)) || /^[A-ZÀ-Ý][a-zà-ÿ]+$/.test(v);
}

/** The whitespace of a JSX text, cleaned up the way the compiler does it. */
function jsx(raw) {
  return raw.split(/\r\n|\n|\r/).map((line, i, all) => {
    let l = line;
    if (i > 0) l = l.replace(/^\s+/, "");
    if (i < all.length - 1) l = l.replace(/\s+$/, "");
    return l;
  }).filter(Boolean).join(" ");
}

function sources(dir) {
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    const file = path.join(dir, name);
    if (fs.statSync(file).isDirectory()) out.push(...sources(file));
    else if (/\.tsx?$/.test(name)) out.push(file);
  }
  return out;
}

function parse(file) {
  const kind = file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, kind);
}

/** The text of a literal, or of a sum of literals (a long text split over several lines). */
function literal(node) {
  if (!node) return null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isParenthesizedExpression(node)) return literal(node.expression);
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = literal(node.left), right = literal(node.right);
    return left === null || right === null ? null : left + right;
  }
  return null;
}

function callee(node) {
  const e = node.expression;
  return ts.isIdentifier(e) ? e.text : ts.isPropertyAccessExpression(e) ? e.name.text : "";
}

function inside(node, test) {
  for (let p = node.parent; p; p = p.parent) if (test(p)) return p;
  return null;
}

// The catalogue: each key of `locales/fr.ts`, and whether its value is a string or a tuple.
const catalogue = path.join(app, "src/locales/fr.ts");
const entries = new Map();
(function read(node) {
  if (ts.isPropertyAssignment(node) && (ts.isStringLiteral(node.name) || ts.isIdentifier(node.name))) {
    const value = node.initializer;
    const tuple = ts.isArrayLiteralExpression(value) && value.elements.length === 2
      && value.elements.every((e) => literal(e) !== null);
    entries.set(node.name.text, literal(value) !== null ? "string" : tuple ? "tuple" : "other");
  }
  ts.forEachChild(node, read);
})(parse(catalogue));

const candidates = process.argv.includes("--candidates");
const filters = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const faults = [];
const used = new Set();

for (const file of sources(path.join(app, "src"))) {
  const rel = path.relative(app, file);
  if (rel.startsWith("src/locales/") || rel === "src/lib/i18n.ts") continue;
  const sf = parse(file);
  const line = (n) => `${rel}:${sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1}`;
  const shown = !filters.length || filters.some((f) => rel.includes(f));

  (function visit(node) {
    // t(text), tc(context, text), tn(n, singular, plural): every text is a translated literal.
    const fn = ts.isCallExpression(node) && ts.isIdentifier(node.expression) ? callee(node) : "";
    if (fn === "t" || fn === "tc" || fn === "tn") {
      const args = fn === "t" ? [node.arguments[0]] : node.arguments.slice(fn === "tc" ? 0 : 1, fn === "tc" ? 2 : 3);
      const texts = args.map(literal);
      if (args.length !== (fn === "t" ? 1 : 2) || texts.includes(null)) {
        faults.push(`${line(node)}: ${fn}() needs literal text`);
      } else {
        const key = fn === "t" ? texts[0] : fn === "tc" ? `${texts[0]}|${texts[1]}` : texts[1];
        const kind = fn === "tn" ? "tuple" : "string";
        used.add(key);
        if (!entries.has(key)) faults.push(`${line(node)}: no translation: "${key}"`);
        else if (entries.get(key) !== kind) {
          faults.push(`${line(node)}: "${key}" must be a ${kind === "tuple" ? "[singular, plural] tuple" : "string"} in fr.ts`);
        }
      }
    }
    if (ts.isJsxText(node) && readable(jsx(node.text))) {
      const element = inside(node, (p) => ts.isJsxElement(p));
      if (!element || !CODE.test(element.openingElement.tagName.getText(sf))) {
        faults.push(`${line(node)}: hard-coded text: "${jsx(node.text).trim().slice(0, 60)}"`);
      }
    }
    if (ts.isJsxAttribute(node) && !NOT_TEXT.has(node.name.getText(sf)) && node.initializer
        && ts.isStringLiteral(node.initializer) && sentence(node.initializer.text)) {
      faults.push(`${line(node)}: hard-coded attribute: ${node.name.getText(sf)}="${node.initializer.text.slice(0, 50)}"`);
    }
    if (candidates && shown && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
        || ts.isTemplateExpression(node))) {
      const text = ts.isTemplateExpression(node) ? node.getText(sf) : node.text;
      const p = node.parent;
      const ignored = ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isLiteralTypeNode(p)
        || (ts.isPropertyAssignment(p) && p.name === node)
        || inside(node, (a) => ts.isCallExpression(a) && /^(t|tc|tn|tr|cls)$/.test(callee(a)))
        || inside(node, (a) => ts.isJsxAttribute(a) && NOT_TEXT.has(a.name.getText(sf)))
        || inside(node, (a) => ts.isPropertyAssignment(a) && /^(queryKey|className)$/.test(a.name.getText(sf)))
        || inside(node, (a) => ts.isTemplateSpan(a) || ts.isTemplateExpression(a)) && !ts.isTemplateExpression(node);
      if (!ignored && /[a-zà-ÿ]{3,}/i.test(text)) console.log(`${line(node)}\t${text.slice(0, 100)}`);
    }
    ts.forEachChild(node, visit);
  })(sf);
}

if (!candidates) {
  for (const key of entries.keys()) {
    if (!used.has(key)) faults.push(`${path.relative(app, catalogue)}: unused translation: "${key}"`);
  }
  if (faults.length) {
    console.error(faults.join("\n"));
    console.error(`translations: ${faults.length} fault(s)`);
    process.exit(1);
  }
  console.log(`translations: ok — ${used.size} texts`);
}
