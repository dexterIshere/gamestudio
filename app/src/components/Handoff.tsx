/**
 * Handing a brief to an agent: a new discussion, or a tab already open.
 *
 * The studio realizes neither a VFX nor an entity's rig: it writes a brief and
 * hands it off. This dialog is the same gesture for every brief: the brief is
 * written on opening (it says what will be sent, and whether anything blocks
 * it), then the user picks the agent and its effort, or the discussion to type
 * it into.
 */

import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Brief, type HandoffChoice, type TerminalSession } from "../api";
import { HARNESS_LOGOS } from "../chat/logos";
import { focusChat } from "../lib/host";
import { folderName } from "../lib/paths";
import { useStudio } from "../lib/store";
import { Badge, Choice, Dialog, Empty, Facts, Section, Seg } from "./ui";
import { t, tr } from "../lib/i18n";

const NEW = "";

export default function HandoffDialog<B extends Brief>({
  title, eyebrow, load, rows, refusal, submit, sent, onClose,
}: {
  title: string;
  eyebrow: string;
  /** Writes the brief and returns it. */
  load: () => Promise<B>;
  /** What the brief says, as name → value lines. */
  rows: (brief: B) => [ReactNode, ReactNode][];
  /** What prevents sending this brief, or `null`. */
  refusal?: (brief: B) => string | null;
  /** Hands the brief off: the server opens the tab or types the request into it. */
  submit: (choice: HandoffChoice) => Promise<{ session: TerminalSession }>;
  /** The notification title, once handed off. */
  sent: (session: TerminalSession) => string;
  onClose: () => void;
}) {
  const { notify } = useStudio();
  const [brief, setBrief] = useState<B | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [session, setSession] = useState(NEW);
  const [harness, setHarness] = useState("claude");
  const [effort, setEffort] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: harnesses } = useQuery({
    queryKey: ["terminal", "harnesses"],
    queryFn: api.terminalHarnesses,
    staleTime: 30_000,
  });
  const { data: sessions } = useQuery({
    queryKey: ["terminal", "sessions"],
    queryFn: api.terminalSessions,
  });

  // `load` changes with every render of the caller: the brief is written only
  // once, on opening.
  useEffect(() => {
    let alive = true;
    load().then(
      (written) => alive && setBrief(written),
      (error: Error) => alive && setFailure(error.message),
    );
    return () => {
      alive = false;
    };
  }, []);

  const blocked = brief && refusal ? refusal(brief) : null;
  const live = (sessions ?? [])
    .filter((entry) => entry.state === "running")
    .sort((a, b) => Number(b.cwd === brief?.godot) - Number(a.cwd === brief?.godot));
  const agent = harnesses?.find((entry) => entry.id === harness);
  const ready = Boolean(brief) && !blocked && !busy
    && (session !== NEW || Boolean(agent?.available));

  const send = async () => {
    setBusy(true);
    setFailure(null);
    try {
      const done = await submit({ harness, effort, session });
      notify({ kind: "success", title: sent(done.session), body: brief?.path });
      onClose();
      await focusChat(done.session.id);
    } catch (error) {
      setFailure((error as Error).message);
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={title}
      eyebrow={eyebrow}
      wide
      onClose={onClose}
      foot={
        <>
          <span className="hint grow">{failure && <Badge tone="danger">{failure}</Badge>}</span>
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!ready} onClick={() => void send()}>
            {busy ? t("Sending…") : t("Send")}
          </button>
        </>
      }
    >
      <div className="stack-4">
        {brief ? (
          <Facts rows={[...rows(brief), [t("Brief"), <span className="mono">{brief.path}</span>]]} />
        ) : (
          !failure && <Empty title={t("Writing the brief…")} />
        )}
        {blocked && <Badge tone="danger">{blocked}</Badge>}

        <Section title={t("Discussion")}>
          <div className="harness-list" role="radiogroup" aria-label={t("Discussion")}>
            <Choice checked={session === NEW} label={t("New discussion")}
                    onSelect={() => setSession(NEW)} />
            {live.map((entry) => (
              <Choice
                key={entry.id}
                checked={session === entry.id}
                label={entry.title}
                sub={<span className="mono">{folderName(entry.cwd)}</span>}
                onSelect={() => setSession(entry.id)}
              />
            ))}
          </div>
        </Section>

        {session === NEW && (
          <Section title={t("Agent")}>
            <div className="harness-list is-grid" role="radiogroup" aria-label={t("Agent")}>
              {(harnesses ?? []).map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className="choice"
                  role="radio"
                  aria-checked={harness === entry.id}
                  disabled={!entry.available}
                  title={entry.available ? entry.detail : entry.reason}
                  onClick={() => {
                    setHarness(entry.id);
                    setEffort("");
                  }}
                >
                  <span className="tick" />
                  <span className="harness-logo" aria-hidden="true">
                    {HARNESS_LOGOS[entry.id] ?? <span className="mono">{entry.label.slice(0, 1)}</span>}
                  </span>
                  <span className="who">
                    <b>{entry.label}</b>
                    {!entry.available && <span>{tr(entry.reason)}</span>}
                  </span>
                </button>
              ))}
            </div>
            {agent && agent.effort_levels.length > 0 && (
              <Seg<string>
                value={effort}
                onChange={setEffort}
                options={[
                  { value: "", label: t("Default") },
                  ...agent.effort_levels.map((level) => ({ value: level, label: level })),
                ]}
              />
            )}
          </Section>
        )}
      </div>
    </Dialog>
  );
}
