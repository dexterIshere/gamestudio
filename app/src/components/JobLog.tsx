/**
 * A project's job log, and the report of each job.
 *
 * The table computes nothing: it reads the queue, which the SSE stream only
 * refreshes when it really moved. A row opens on its report (cost, files
 * produced, refusals) with the raw JSON folded below.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type Job } from "../api";
import { writeClipboard } from "../lib/host";
import { useEntities } from "../lib/queries";
import { useStudio } from "../lib/store";
import { cardHref } from "../pages/World";
import {
  Callout, Dialog, Empty, Flags, Menu, Metric, MetricGrid, Panel, Section, State, ToolGroup, Toolbar, cost,
  jobTitle, shortDate,
} from "./ui";
import { t, tc, tn, tr } from "../lib/i18n";

export default function JobLog({ jobs, running, id }: {
  jobs: Job[];
  running: boolean;
  id?: string;
}) {
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [pings, setPings] = useState(false);
  const [report, setReport] = useState<Job | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; job: Job } | null>(null);
  const { project, notify } = useStudio();
  const { data: entities } = useEntities(project);
  // A world card's job carries its entity in its step (`concepts:<entity>`,
  // `entity:<entity>`): that is where it can be resumed.
  const cardOf = (job: Job) => {
    const [kind, entity] = job.step.split(":");
    if (!entity || (kind !== "concepts" && kind !== "entity")) return null;
    return cardHref(entities, entity, kind === "concepts" ? "concepts" : "3d");
  };

  // Connection tests say nothing about the project: hidden by default, so the
  // log speaks first of what was produced.
  const listed = jobs.filter((job) => pings || job.kind !== "ping");
  const failures = listed.filter((job) => job.state === "failed");
  const shown = onlyFailed ? failures : listed;

  return (
    <div id={id}>
      <Panel
        eyebrow={tn(listed.length, "Queue · {n} task", "Queue · {n} tasks")}
        title={t("Task log")}
        actions={
          <State state={running ? "running" : "done"} label={running ? t("queue running") : t("queue idle")} />
        }
        bodyClass="tight"
      >
        <Toolbar className="filter-bar">
          <ToolGroup>
            <div className="chips" role="group" aria-label={t("Filter the log")}>
              <button className="chip" aria-pressed={!onlyFailed} onClick={() => setOnlyFailed(false)}>
                {tc("tasks", "All")} <span className="n">{listed.length}</span>
              </button>
              <button className="chip" aria-pressed={onlyFailed} onClick={() => setOnlyFailed(true)}>
                {t("Failures")} <span className="n">{failures.length}</span>
              </button>
            </div>
          </ToolGroup>
          <ToolGroup end>
            <Flags options={[
              { label: t("Connection tests"), checked: pings, onChange: setPings },
            ]} />
          </ToolGroup>
        </Toolbar>
        {shown.length ? (
          <div className="log-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("Task")}</th>
                  <th style={{ width: 110 }}>{t("Status")}</th>
                  <th className="num" style={{ width: 90 }}>{t("Cost")}</th>
                  <th className="num" style={{ width: 120 }}>{t("When")}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((job) => (
                  <tr
                    key={job.id}
                    tabIndex={0}
                    className={`is-clickable ${job.state === "failed" ? "is-failed" : ""}`}
                    onClick={() => setReport(job)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") setReport(job);
                    }}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      setMenu({ x: event.clientX, y: event.clientY, job });
                    }}
                  >
                    <td className="task-name">
                      <b>{jobTitle(job)}</b>
                      <span>{job.error ? tr(job.error) : job.kind}</span>
                    </td>
                    <td><State state={job.state} /></td>
                    <td className="num">{cost(job.cost_usd)}</td>
                    <td className="num when">{shortDate(job.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title={onlyFailed ? t("No failure") : t("Empty queue")} />
        )}
      </Panel>

      {menu && (
        <Menu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          <button onClick={() => { setReport(menu.job); setMenu(null); }}>{t("See the full report")}</button>
          <button
            onClick={() => {
              writeClipboard(menu.job.id).catch((error: unknown) =>
                notify({ kind: "error", title: t("Copy failed"), body: String(error) }));
              setMenu(null);
            }}
          >
            {t("Copy the identifier")}
          </button>
        </Menu>
      )}

      {report && (
        <Dialog
          eyebrow={report.id.slice(0, 8)}
          title={jobTitle(report)}
          onClose={() => setReport(null)}
          wide
          foot={
            report.state === "failed" && cardOf(report) ? (
              <>
                <span className="spacer grow" />
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => { window.location.hash = cardOf(report)!.slice(1); }}
                >
                  {t("Continue in the card")}
                </button>
              </>
            ) : undefined
          }
        >
          <JobDetail id={report.id} />
        </Dialog>
      )}
    </div>
  );
}

/**
 * What became of a job.
 *
 * The server's report reads first (cost, files produced, refusals) and the raw
 * JSON stays below, folded: not everything is modelled, but nothing is hidden.
 * Files are given by their library path, which can be copied: that is what
 * goes into a Godot project or a prompt.
 */
function JobDetail({ id }: { id: string }) {
  const { notify } = useStudio();
  const { data, error } = useQuery({ queryKey: ["job", id], queryFn: () => api.job(id) });
  const [copied, setCopied] = useState<string | null>(null);

  if (error) return <div className="hint" style={{ color: "var(--danger)" }}>{(error as ApiError).message}</div>;
  if (!data) return <div className="skeleton" style={{ height: 200 }} />;

  const report = data.report;
  const copy = (path: string) => {
    writeClipboard(path).then(
      () => {
        setCopied(path);
        window.setTimeout(() => setCopied((seen) => (seen === path ? null : seen)), 1500);
      },
      (failure: unknown) => notify({ kind: "error", title: t("Copy failed"), body: String(failure) }),
    );
  };

  return (
    <>
      <MetricGrid>
        <Metric label={t("cost")} value={<span className="mono">{report.cost_usd ? cost(report.cost_usd) : t("free")}</span>} />
        <Metric label={t("files produced")} value={report.files.length} />
        <Metric label={t("outside the library")} value={report.absent.length} />
        <Metric label={t("status")} value={<State state={report.state} />} />
      </MetricGrid>

      {report.error && (
        <Callout>
          <b style={{ color: "var(--danger)" }}>{t("Failed —")}{" "}</b>
          {tr(report.error)}
        </Callout>
      )}
      {report.errors.map((message, index) => (
        <Callout key={index}>
          <span style={{ color: "var(--danger)" }}>{tr(message)}</span>
        </Callout>
      ))}
      {report.absent.length > 0 && (
        <Callout action={t("copy the hashes")} onAction={() => copy(report.absent.map((a) => a.asset_id).join("\n"))}>
          {t("{length} produced file(s) that no project folder claims: they exist in the store but appear nowhere in the library.", { length: report.absent.length })}
        </Callout>
      )}
      {report.note && <Callout>{tr(report.note)}</Callout>}

      {report.files.length > 0 && (
        <Section title={t("Files produced · {length}", { length: report.files.length })}>
          <div className="summary">
            {report.files.map((file) => (
              <div className="line" key={file.asset_id}>
                <b className="grow truncate" title={file.path}>{file.name}</b>
                <span className="hint mono">{file.folder || t("root")}</span>
                <button className="btn btn-ghost btn-sm" onClick={() => copy(file.path)}>
                  {copied === file.path ? t("copied") : t("copy")}
                </button>
              </div>
            ))}
          </div>
        </Section>
      )}

      {report.refused.length > 0 && (
        <Section title={t("Refused")}>
          <div className="summary">
            {report.refused.map((entry) => (
              <div className="line" key={entry.what}>
                <b className="grow">{entry.what}</b>
                <span className="hint">{tr(entry.why)}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {report.facts.length > 0 && (
        <Section title={t("Details")}>
          <div className="summary">
            {report.facts.map((fact) => (
              <div className="line" key={fact.key}>
                <b className="grow">{fact.key}</b>
                <span className="mono">{fact.value}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <details>
        <summary className="hint">{t("Raw payload and result")}</summary>
        <pre className="recipe" style={{ maxHeight: "40vh", overflow: "auto", whiteSpace: "pre-wrap" }}>
          {JSON.stringify({ payload: data.payload, result: data.result }, null, 2)}
        </pre>
      </details>
    </>
  );
}
