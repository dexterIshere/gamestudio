/**
 * The station: what works on this machine, what is connected, what is followed.
 *
 * Three readings side by side. **Diagnostics**: the tools, the key, the queue.
 * **MCP connections**, by source, with their real state (does the program
 * start here?). **Procedures**: the studio's skills, listed, their index and
 * the mirror that makes them findable by agents that do not read `.claude/`.
 *
 * A new procedure is requested here: the user says what they want, an agent
 * writes it (`write_skill` puts the index and the mirror back in order). The
 * rest is not edited here: these are repository files, never an agent's
 * global configuration.
 */

import { useEffect, useState } from "react";
import {
  api, type DoctorCheck, type DoctorReport, type McpConnections, type McpServer, type Skill,
  type SkillsCheck,
} from "../api";
import HandoffDialog from "./Handoff";
import Markdown from "./Markdown";
import { Badge, Callout, Dialog, Empty, Field, Panel, State, bytes } from "./ui";
import { inTauri, revealPath } from "../lib/host";
import { useSkills, useSyncSkills, useWriteSkillsIndex } from "../lib/queries";
import { useStudio } from "../lib/store";
import { t, tr } from "../lib/i18n";

export default function Station({ diagnostic, connections, check, id }: {
  diagnostic?: DoctorReport;
  connections?: McpConnections;
  check?: SkillsCheck;
  id?: string;
}) {
  const { data: skills, isLoading: loadingSkills } = useSkills();
  const writeIndex = useWriteSkillsIndex();
  const sync = useSyncSkills();
  const { notify } = useStudio();
  const [open, setOpen] = useState<Skill | null>(null);
  const [creating, setCreating] = useState(false);
  const [request, setRequest] = useState<string | null>(null);

  const ours = (skills ?? []).filter((skill) => !skill.vendored);
  const theirs = (skills ?? []).filter((skill) => skill.vendored);

  // The same server is often declared for several CLIs: one line per name,
  // with the sources declaring it, is enough to say whether it starts.
  const servers = new Map<string, { server: McpServer; sources: string[]; available: boolean }>();
  for (const source of connections?.sources ?? []) {
    for (const server of source.servers) {
      const seen = servers.get(server.name);
      if (seen) {
        seen.sources.push(tr(source.label));
        seen.available &&= server.available;
      } else {
        servers.set(server.name, { server, sources: [tr(source.label)], available: server.available });
      }
    }
  }
  const pending = (diagnostic?.checks ?? []).filter((entry) => entry.status !== "ok");

  return (
    <div className="stack-5" id={id}>
      <div className="split-even">
        <Panel
          eyebrow={diagnostic ? `gamestudio ${diagnostic.version}` : t("This machine")}
          title={t("Diagnostics")}
          actions={
            diagnostic &&
            (pending.length ? (
              <State
                state={diagnostic.counts.failures ? "failed" : "needs_review"}
                label={t("{length} to look at", { length: pending.length })}
              />
            ) : (
              <State state="done" label={t("complete")} />
            ))
          }
          bodyClass="tight"
        >
          {/* Until the station has answered, say nothing: showing "missing"
              by default would be a false alarm. */}
          {!diagnostic ? (
            <div className="skeleton" style={{ height: 120, margin: "var(--space-4)" }} />
          ) : (
            <>
              {pending.length > 0 && (
                <div className="station-rows">
                  {pending.map((entry) => <CheckRow key={entry.id} entry={entry} />)}
                </div>
              )}
              <details className="more">
                <summary>
                  {t("All checks")} <span className="mono">{t("{ok}/{length} ok", { ok: diagnostic.counts.ok, length: diagnostic.checks.length })}</span>
                </summary>
                <div className="more-body stack-2">
                  {diagnostic.checks.map((entry) => <CheckRow key={entry.id} entry={entry} />)}
                </div>
              </details>
            </>
          )}
        </Panel>

        <Panel
          eyebrow={connections
            ? t("{project} in the project · {global} global", { project: connections.counts.project, global: connections.counts.global })
            : "MCP"}
          title={t("Connections")}
          actions={
            connections &&
            (connections.counts.unavailable ? (
              <State state="failed" label={t("{n} unreachable", { n: connections.counts.unavailable })} />
            ) : (
              <State state="done" label={t("reachable")} />
            ))
          }
          bodyClass="tight"
        >
          {servers.size === 0 ? (
            <Empty title={t("No server declared")} />
          ) : (
            <ul className="wsteps station-rows">
              {[...servers.values()].map(({ server, sources, available }) => (
                <li key={server.name}>
                  <span className="srv">
                    <span className="grow">
                      <b className="mono">{server.name}</b>
                      <span className="hint">{sources.join(" · ")}</span>
                    </span>
                    {server.studio && <Badge>{t("studio")}</Badge>}
                    {server.companion && <Badge>{t("companion")}</Badge>}
                    {!available
                      ? <Badge tone="danger">{server.reason ? tr(server.reason) : t("not found")}</Badge>
                      : server.companion && !server.companion.ready
                        ? <Badge tone="warn">{t("pending")}</Badge>
                        : <Badge tone="quiet">{t("starts")}</Badge>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {(connections?.sources.length ?? 0) > 0 && (
            <details className="more">
              <summary>
                {t("Sources")} <span className="mono">{connections?.sources.length}</span>
              </summary>
              <div className="more-body stack-4">
                {connections?.sources.map((source) => (
                  <div key={source.id} className="stack-2">
                    <div className="row gap-3">
                      <b className="grow">{tr(source.label)}</b>
                      {source.exists
                        ? <Badge tone="quiet">{t("declared")}</Badge>
                        : <Badge tone="warn">{t("missing")}</Badge>}
                    </div>
                    <p className="path-box">
                      <span className="mono truncate">{source.path}</span>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={!inTauri()}
                        onClick={() => void revealPath(source.path).then((refusal) => refusal
                          && notify({ kind: "error", title: t("The folder does not open"), body: refusal }))}
                      >
                        {t("Show")}
                      </button>
                    </p>
                    {source.error && <Badge tone="danger">{tr(source.error)}</Badge>}
                    {source.warning && <Badge tone="warn">{tr(source.warning)}</Badge>}
                    {source.servers.map((server) => (
                      <p className="hint mono" key={server.name}>
                        {server.name} — {server.url || `${server.command} ${server.args.join(" ")}`}
                        {server.companion && ` · ${tr(server.companion.detail)}`}
                      </p>
                    ))}
                  </div>
                ))}
                {connections?.notes.map((note) => (
                  <p className="hint" key={note}>· {tr(note)}</p>
                ))}
              </div>
            </details>
          )}
        </Panel>
      </div>

      <Panel
        eyebrow={check
          ? t("{skills} procedures · {files} files · {bytes}", { skills: check.skills, files: check.files, bytes: bytes(check.bytes) })
          : t("Skills")}
        title={t("Procedures")}
        actions={
          <div className="row row-wrap gap-2">
            {check && (check.ok
              ? <State state="done" label={t("index up to date")} />
              : <State state="failed" label={t("{v} problem(s)", { v: check.problems.length + check.missing.length })} />)}
            <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
              {t("New procedure")}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={writeIndex.isPending}
              onClick={() => writeIndex.mutate()}
            >
              {writeIndex.isPending ? t("Regenerating…") : t("Regenerate the index")}
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={sync.isPending}
              onClick={() => sync.mutate()}
            >
              {sync.isPending ? t("Repairing…") : t("Repair the mirror")}
            </button>
          </div>
        }
        bodyClass="tight"
      >
        {check && !check.ok && (
          <div className="station-rows">
            <Callout action={t("Regenerate the index")} onAction={() => writeIndex.mutate()}>
              {check.problems.map((entry) => `${entry.skill}: ${tr(entry.problem)}`).join(" · ")}
              {check.missing.join(" · ")}
            </Callout>
          </div>
        )}
        {loadingSkills ? (
          <div className="skeleton" style={{ height: 80, margin: "var(--space-4)" }} />
        ) : (skills?.length ?? 0) === 0 ? (
          <Empty
            title={t("No procedure")}
            action={
              <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
                {t("New procedure")}
              </button>
            }
          />
        ) : (
          <>
            <div className="station-rows">
              <div className="skill-grid">
                {ours.map((skill) => (
                  <SkillRow key={skill.name} skill={skill} onOpen={setOpen} />
                ))}
              </div>
            </div>
            {theirs.length > 0 && (
              <details className="more">
                <summary>
                  {t("Third-party material")} <span className="mono">{theirs.length}</span>
                </summary>
                <div className="more-body">
                  <div className="skill-grid">
                    {theirs.map((skill) => (
                      <SkillRow key={skill.name} skill={skill} onOpen={setOpen} />
                    ))}
                  </div>
                </div>
              </details>
            )}
          </>
        )}
      </Panel>

      {creating && (
        <NewSkillDialog
          initial={request ?? ""}
          onClose={() => {
            setCreating(false);
            setRequest(null);
          }}
          onNext={(wanted) => {
            setCreating(false);
            setRequest(wanted);
          }}
        />
      )}

      {request !== null && !creating && (
        <HandoffDialog
          title={t("New procedure")}
          eyebrow=".claude/skills/<name>/SKILL.md"
          load={() => api.skillBrief(request)}
          rows={(brief) => [
            [t("Request"), brief.request],
            [t("Folder"), <span className="mono">{brief.root}</span>],
          ]}
          submit={(choice) => api.skillHandoff(request, choice)}
          sent={(session) => t("Procedure handed to {title}", { title: session.title })}
          onClose={() => setRequest(null)}
        />
      )}

      {open && <SkillDialog skill={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function CheckRow({ entry }: { entry: DoctorCheck }) {
  return (
    <div className="check-row">
      <span className={`led ${entry.status === "ok" ? "is-ok"
        : entry.status === "failed" ? "is-fail" : "is-warn"}`} aria-hidden="true" />
      <span className="grow">
        <b>{tr(entry.label)}</b>
        <span className="mono detail">{tr(entry.detail)}</span>
        {entry.fix && <span className="hint">→ {tr(entry.fix)}</span>}
      </span>
      <span className="meta">{entry.status === "ok" ? "" : tr(entry.status)}</span>
    </div>
  );
}

function SkillRow({ skill, onOpen }: { skill: Skill; onOpen: (skill: Skill) => void }) {
  return (
    <button className="doc-row" onClick={() => onOpen(skill)}>
      <span className="grow">
        <b className="mono">{skill.name}</b>
        <span>{skill.description}</span>
      </span>
      {skill.problems.length > 0 && <Badge tone="danger">{skill.problems.length}</Badge>}
      <span className="meta num">{t("{files} file(s)", { files: skill.files })}</span>
    </button>
  );
}

/**
 * Below this the service refuses: a request of a few words says neither what
 * the procedure does nor when to follow it.
 */
const MIN_REQUEST = 20;

/**
 * Requesting a procedure: the user says what they want, in plain words.
 *
 * They do not write it: the request goes into a brief, and an agent picks the
 * name, the description and the text, in the shape of the studio's
 * procedures. Choosing the agent comes next (`HandoffDialog`).
 */
function NewSkillDialog({ initial, onClose, onNext }: {
  initial: string;
  onClose: () => void;
  onNext: (request: string) => void;
}) {
  const [wanted, setWanted] = useState(initial);
  const ready = wanted.trim().length >= MIN_REQUEST;

  return (
    <Dialog
      wide
      title={t("New procedure")}
      eyebrow=".claude/skills/<name>/SKILL.md"
      onClose={onClose}
      foot={
        <>
          <span className="grow" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!ready} onClick={() => onNext(wanted.trim())}>
            {t("Choose the agent")}
          </button>
        </>
      }
    >
      <Field label={t("What the procedure must do")} hint={t("At least {MIN_REQUEST} characters", { MIN_REQUEST })}>
        <textarea
          value={wanted}
          autoFocus
          rows={10}
          placeholder={t("Fix a badly split sheet, element by element, until every icon is whole.")}
          onChange={(event) => setWanted(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && ready) {
              onNext(wanted.trim());
            }
          }}
        />
      </Field>
    </Dialog>
  );
}

/** A procedure's text, read in the window: an agent follows it, the user sees it. */
function SkillDialog({ skill, onClose }: { skill: Skill; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.skill(skill.name)
      .then((entry) => alive && setText(entry.text))
      .catch((error: unknown) => alive && setFailed(String(error)));
    return () => {
      alive = false;
    };
  }, [skill.name]);

  return (
    <Dialog
      wide
      title={skill.name}
      eyebrow={skill.vendored ? t("third-party material") : t("studio procedure")}
      hint={skill.skill_file}
      onClose={onClose}
      foot={
        <span className="hint grow">
          {bytes(skill.bytes)} · {t("{files} file(s)", { files: skill.files })}
          {skill.vendored && t(" · Apache 2.0 — see THIRD_PARTY_NOTICES.md")}
        </span>
      }
    >
      {failed ? <Callout>{failed}</Callout>
        : text === null ? <Empty title={t("Reading…")} />
          : <Markdown text={text} />}
    </Dialog>
  );
}
