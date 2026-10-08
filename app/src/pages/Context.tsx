/**
 * Context: what every agent reads before working in the studio.
 *
 * The page writes nothing itself. It shows the briefing -- computed from the
 * database and the library -- and lets the user edit the three handwritten
 * notes. It is the only page whose content changes how later conversations
 * behave: editing it here changes what an agent knows on arrival.
 *
 * The page follows the active project: one context per project. The notes
 * (`identity.md`, `goals.md`, `preferences.md`) are global to the studio.
 */

import { useEffect, useState } from "react";
import { api, type ContextNote } from "../api";
import Markdown from "../components/Markdown";
import {
  Badge, Callout, Dialog, Empty, Facts, Metric, MetricGrid, PageHeader, Panel, Seg,
  cost, shortDate,
} from "../components/ui";
import { inTauri } from "../lib/host";
import { useReveal } from "../lib/paths";
import { useContext, useContextNotes, useRefreshContext, useSaveContextNote } from "../lib/queries";
import { useStudio } from "../lib/store";
import { t } from "../lib/i18n";

type View = "rendered" | "raw";

/** The project entry as the briefing's digest carries it. */
type FocusEntry = {
  characters_total?: number;
  jobs_active?: number;
  cost_usd?: number;
  files?: number;
  library?: string;
};

export default function Context() {
  const { project, notify } = useStudio();
  const reveal = useReveal();
  const { data: briefing, isLoading, error } = useContext(project);
  const { data: notes } = useContextNotes();
  const refresh = useRefreshContext();
  const [view, setView] = useState<View>("rendered");
  const [editing, setEditing] = useState<{ name: string; text: string } | null>(null);

  const digest = briefing?.digest as Record<string, unknown> | undefined;
  const focusEntry = (digest?.focus_entry ?? null) as FocusEntry | null;
  const handwritten = (notes ?? []).filter((note) => !note.generated);

  const open = async (note: ContextNote) => {
    try {
      const file = await api.contextNote(note.name);
      setEditing({ name: note.name, text: file.text });
    } catch (failure) {
      notify({ kind: "error", title: t("Note cannot be read"), body: (failure as Error).message });
    }
  };

  return (
    <div className="stack-4">
      <PageHeader
        title={t("Context")}
        project={project}
        actions={
          <>
            <button
              className="btn btn-primary"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate(project)}
            >
              {refresh.isPending ? t("Regenerating…") : t("Regenerate the briefing")}
            </button>
            <button
              className="btn btn-ghost"
              disabled={!inTauri() || !briefing?.path}
              onClick={() => briefing && void reveal(briefing.path)}
            >
              {t("Show in the folder")}
            </button>
          </>
        }
      />

      {project ? (
        <MetricGrid>
          <Metric label={t("characters")} value={focusEntry?.characters_total ?? "—"} />
          <Metric label={t("active tasks")} value={focusEntry?.jobs_active ?? "—"} />
          <Metric label={t("spent")} value={focusEntry?.cost_usd != null ? cost(focusEntry.cost_usd) : "—"} />
          <Metric label={t("files")} value={focusEntry?.files ?? "—"} />
          <Metric label={t("handwritten notes")} value={handwritten.length} />
        </MetricGrid>
      ) : (
        <MetricGrid>
          <Metric label={t("handwritten notes")} value={handwritten.length} />
        </MetricGrid>
      )}

      {refresh.isError && (
        <Callout action={t("Retry")} onAction={() => refresh.mutate(project)}>
          {t("The briefing could not be rewritten: {error}", { error: String(refresh.error) })}
        </Callout>
      )}

      <div className="split-wide">
        <Panel
          title={t("Briefing")}
          actions={
            <Seg<View>
              value={view}
              onChange={setView}
              options={[
                { value: "rendered", label: t("Render") },
                { value: "raw", label: t("Raw") },
              ]}
            />
          }
          foot={
            briefing && (
              <span className="hint mono">
                {t("{path} · written {shortDate}", { path: briefing.path, shortDate: shortDate(briefing.generated_at) })}
              </span>
            )
          }
        >
          {error ? (
            <Callout>{t("The briefing cannot be read: {error}", { error: String(error) })}</Callout>
          ) : isLoading || !briefing ? (
            <Empty title={t("Reading the context…")} />
          ) : view === "rendered" ? (
            <Markdown text={briefing.markdown} />
          ) : (
            <pre className="mono md-raw">{briefing.markdown}</pre>
          )}
        </Panel>

        <Panel
          title={t("Handwritten notes")}
          bodyClass="stack-4"
        >
          {handwritten.length === 0 ? (
            <Empty title={t("No note")} />
          ) : (
            <div className="stack-3">
              {handwritten.map((note) => (
                <div className="note-row" key={note.name}>
                  <div className="grow">
                    <b className="mono">{note.name}</b>
                    <p className="hint">
                      {note.exists ? (
                        <>{t("modified {shortDate}", { shortDate: shortDate(note.modified_at ?? "") })}</>
                      ) : (
                        <Badge tone="warn">{t("missing")}</Badge>
                      )}
                    </p>
                  </div>
                  <button className="btn btn-ghost btn-sm" onClick={() => void open(note)}>
                    {note.exists ? t("Edit") : t("Create")}
                  </button>
                </div>
              ))}
            </div>
          )}
          {briefing && (
            <Facts
              rows={[
                [t("Studio root"), String(digest?.root ?? "—")],
                [t("Data folder"), String(digest?.data_dir ?? "—")],
                [t("Library"), String(digest?.library_dir ?? "—")],
              ]}
            />
          )}
        </Panel>
      </div>

      {editing && (
        <NoteDialog
          name={editing.name}
          initial={editing.text}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/** A note's editor: a text, and nothing else around it. */
function NoteDialog({ name, initial, onClose }: {
  name: string;
  initial: string;
  onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  const save = useSaveContextNote();
  const dirty = text !== initial;

  // Follows a new `initial` (another note opened without unmounting).
  useEffect(() => setText(initial), [initial]);

  const close = () => {
    if (!dirty || window.confirm(t("Close without saving?"))) onClose();
  };

  return (
    <Dialog
      title={name}
      eyebrow={t("agent context")}
      wide
      onClose={close}
      foot={
        <>
          <span className="hint grow">
            {save.isError ? <Badge tone="danger">{String(save.error)}</Badge>
              : dirty ? t("Unsaved changes") : t("Up to date")}
          </span>
          <button className="btn btn-ghost" onClick={close}>{t("Close")}</button>
          <button
            className="btn btn-primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ name, text }, { onSuccess: onClose })}
          >
            {save.isPending ? t("Saving…") : t("Save")}
          </button>
        </>
      }
    >
      <textarea
        className="mono note-editor"
        value={text}
        spellCheck={false}
        onChange={(event) => setText(event.target.value)}
      />
    </Dialog>
  );
}
