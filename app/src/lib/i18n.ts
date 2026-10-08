/**
 * The interface language: English as written in the code, or its French translation.
 *
 * Every displayed text is written in English and goes through `t("Cancel")`:
 * English shows it as is, French looks it up in `locales/fr.ts`. The English
 * text is therefore both the text and its key; a text without a translation
 * stays in English rather than disappearing, and `make check` refuses a
 * missing one.
 *
 * Text that comes from the server (errors, labels, diagnostics) goes through
 * `tr()`: it is only known at runtime, and a composed message ("project not
 * found: 'x'") is matched by its template (`locales/fr-server.json`, where
 * `{0}`, `{1}`… stand for the values).
 *
 * The language is read once, when the module loads: changing it reloads the
 * windows, which also re-translates module-level constants.
 */

import { FR } from "../locales/fr";
import FR_SERVER from "../locales/fr-server.json";

export type Lang = "fr" | "en";

/** The offered languages, each named -- and saying "language" -- in itself. */
export const LANGS: { id: Lang; label: string; word: string }[] = [
  { id: "fr", label: "Français", word: "Langue" },
  { id: "en", label: "English", word: "Language" },
];

const KEY = "gamestudio.lang";

function stored(): Lang | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "fr" || value === "en" ? value : null;
  } catch {
    return null;
  }
}

/** The system language, narrowed to the studio's: French, otherwise English. */
export function systemLang(): Lang {
  return (navigator.language || "").toLowerCase().startsWith("fr") ? "fr" : "en";
}

/** This window's language, until it next loads. */
export const lang: Lang = stored() ?? systemLang();

/** True once a language has been chosen: until then, the studio opens on that choice. */
export const langChosen = (): boolean => stored() !== null;

/** For `toLocaleString` and the like: numbers and dates follow the language. */
export const locale = lang === "fr" ? "fr-FR" : "en-US";

/** A number in the language's style (space or comma for thousands). */
export const num = (value: number): string => value.toLocaleString(locale);

/** A decimal with `digits` digits, using the language's decimal separator. */
export const decimal = (value: number, digits = 2): string =>
  value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Changes the language: stores it, then reloads this window (the other one follows). */
export function setLang(next: Lang): void {
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* Without storage the choice does not survive the reload: nothing to do. */
  }
  window.location.reload();
}

type Vars = Record<string, string | number>;

function fill(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

/** The French text of a catalogue key, or undefined (English, or no translation). */
function french(key: string): string | undefined {
  if (lang !== "fr") return undefined;
  const value = FR[key];
  return typeof value === "string" ? value : undefined;
}

/**
 * Translates an interface text, written in English in the code.
 *
 * `{name}` stands for a value: `t("{n} cards", { n })`. The key is always a
 * literal -- that is what lets `make check` verify each one has a translation.
 */
export function t(text: string, vars?: Vars): string {
  return fill(french(text) ?? text, vars);
}

/**
 * Like `t()`, for an English text that French translates in more than one way
 * ("All" is "Tout", "Toutes" or "Tous"). `context` names what the text refers
 * to; the French entry is keyed `context|text`. English ignores the context.
 */
export function tc(context: string, text: string, vars?: Vars): string {
  return fill(french(`${context}|${text}`) ?? text, vars);
}

/**
 * The singular or the plural depending on `n`, which fills `{n}`.
 *
 * The two languages do not split at the same place: English says "0 files",
 * French "0 fichier". The French entry is keyed by the English plural and
 * holds `[singular, plural]`.
 */
export function tn(n: number, one: string, many: string, vars?: Vars): string {
  const forms = lang === "fr" ? FR[many] : undefined;
  if (Array.isArray(forms)) return fill(Math.abs(n) < 2 ? forms[0] : forms[1], { n, ...vars });
  return fill(n === 1 ? one : many, { n, ...vars });
}

const SERVER = FR_SERVER as Record<string, string>;
let templates: [RegExp, string][] | null = null;

function compile(): [RegExp, string][] {
  const escape = (part: string) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const compiled: [RegExp, string][] = [];
  for (const [english, translation] of Object.entries(SERVER)) {
    const parts = english.split(/\{\d+\}/);
    // A template made only of values would match any message.
    if (parts.length < 2 || parts.join("").trim() === "") continue;
    compiled.push([new RegExp(`^${parts.map(escape).join("(.+?)")}$`, "s"), translation]);
  }
  return compiled;
}

/**
 * Translates a text that comes from the server, only known at runtime.
 *
 * First as is, then by template: "project not found: 'x'" matches "project
 * not found: {0}", and the value carries over into the translation. An
 * unknown text -- a name, a card, a message from a third-party tool -- comes
 * back untouched.
 */
export function tr(text: string | null | undefined): string {
  if (!text) return text ?? "";
  if (lang !== "fr") return text;
  const exact = SERVER[text] ?? french(text);
  if (exact !== undefined) return exact;
  templates ??= compile();
  for (const [pattern, translation] of templates) {
    const match = pattern.exec(text);
    if (match) return translation.replace(/\{(\d+)\}/g, (_, index: string) => match[Number(index) + 1] ?? "");
  }
  return text;
}

// The page carries its language (screen readers, hyphenation, spell checker),
// and the other window follows a change made in this one.
document.documentElement.lang = lang;
window.addEventListener("storage", (event) => {
  if (event.key === KEY && event.newValue !== null && event.newValue !== lang) {
    window.location.reload();
  }
});
