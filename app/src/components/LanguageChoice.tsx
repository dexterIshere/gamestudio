/**
 * The language choice, at first launch: until it is made, the studio does not open.
 *
 * Two buttons, each language named in itself; the system language is
 * highlighted and takes the focus, so Enter keeps it. The choice can be changed
 * later in the Control room, "This machine" zone.
 */

import { LANGS, setLang, systemLang } from "../lib/i18n";

export default function LanguageChoice() {
  const preferred = systemLang();
  return (
    <div className="lang-window">
      <div className="lang-card col" role="group" aria-label={LANGS.map((entry) => entry.word).join(" · ")}>
        <header>
          <p className="eyebrow">gamestudio</p>
          <h1>{LANGS.map((entry) => entry.word).join(" · ")}</h1>
        </header>
        <div className="lang-options">
          {LANGS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              lang={entry.id}
              className={`btn ${entry.id === preferred ? "btn-primary" : "btn-secondary"}`}
              autoFocus={entry.id === preferred}
              onClick={() => setLang(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
