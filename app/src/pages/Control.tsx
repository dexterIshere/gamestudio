/**
 * Control room: decide, pick the project, produce it, equip the station -- one page.
 *
 * In the order of use:
 *
 * 1. **To do now** -- computed, never written: a failure in the queue, a
 *    character declared but not built, an output to review (here or in another
 *    project), a missing tool, an unreachable connection.
 * 2. **The active project** -- its entities and their chain, its task log, its
 *    recipe and what it cost. The project is picked in the workspace selector,
 *    at the top of the rail: one place for that choice.
 * 3. **This station** -- diagnostics, MCP connections, procedures.
 *
 * The `#studio:<zone>` fragment leads straight to a zone (`project`, `log`,
 * `station`).
 */

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, assetPreviewUrl, type Character, type WorldEntity } from "../api";
import JobLog from "../components/JobLog";
import Station from "../components/Station";
import {
  Badge, Dialog, Empty, Facts, Field, Metric, MetricGrid, PageHeader, Panel, Seg, Select, Setting,
  Slider, State, Toggle, cost, jobTitle, shortDate,
} from "../components/ui";
import { IMAGE_MODELS, MESH_MODELS } from "../lib/catalog";
import {
  useCharacters, useConnections, useDoctor, useEntities, useJobs, useQueueStatus, useRecipes,
  useRefreshProject, useSkillsCheck, useWorkspace, useWorld,
} from "../lib/queries";
import { useStudio } from "../lib/store";
import { cardHref } from "./World";
import { LANGS, lang, setLang, t, tn, tr, type Lang } from "../lib/i18n";

/** Goes to another page: the shell follows the URL fragment. */
const go = (page: string) => () => {
  window.location.hash = page;
};

/** Brings a zone of the page into view -- a jump on arrival, a smooth scroll afterwards. */
const reach = (zone: string, behavior: ScrollBehavior = "smooth") => () =>
  document.getElementById(`zone-${zone}`)?.scrollIntoView({ behavior, block: "start" });

interface Todo {
  tone: "danger" | "next" | "warn";
  title: ReactNode;
  detail: ReactNode;
  actions: ReactNode;
}

const TODO_ICONS = {
  danger: <path d="M12 7v6M12 17h.01" />,
  next: <path d="M5 12h14M13 6l6 6-6 6" />,
  warn: (
    <>
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v4M12 17h.01" />
    </>
  ),
};

/**
 * The models a recipe build uses, with their price, taken from the interface's
 * catalog so a model has one price on every page. The posed reference comes
 * from FLUX.1 dev (a style's base model), the mesh from Tripo v3.1 on Runware
 * (`runware/catalog.py`); style exploration uses FLUX.1 schnell.
 */
const BUILD_IMAGE = IMAGE_MODELS.find((model) => model.air === "runware:101@1");
const BUILD_MESH = MESH_MODELS.find((model) => model.air === "tripo:v3.1@0");
const EXPLORE_MODEL = IMAGE_MODELS.find((model) => model.air === "runware:100@1");

/** A catalog price, or "—" if the model left the catalog: nothing is made up. */
const price = (model: { usd: number } | undefined) => (model ? cost(model.usd) : "—");

// The queue has its own "To do" item (the project's last failure): the
// diagnostic's warning would say the same, less precisely.
const DOCTOR_SKIP = new Set(["queue"]);

export default function Control() {
  const { project, setProject, notify } = useStudio();
  const { data: workspace } = useWorkspace();
  const { data: recipes } = useRecipes();
  const { data: characters } = useCharacters(project);
  const { data: entities } = useEntities(project);
  const { data: sections } = useWorld(project);
  const { data: queue } = useQueueStatus(project || undefined);
  const { data: jobs } = useJobs(project || undefined);
  const { data: diagnostic } = useDoctor();
  const { data: connections } = useConnections();
  const { data: check } = useSkillsCheck();
  const refresh = useRefreshProject();
  const [confirming, setConfirming] = useState<null | { characters: string[]; force: boolean }>(null);
  const [exploring, setExploring] = useState(false);
  const { data: source } = useQuery({
    queryKey: ["recipe-source", project],
    queryFn: () => api.recipeSource(project),
    enabled: Boolean(project),
    retry: false,
  });

  // `#studio:log`: scroll to the requested zone once the page is filled --
  // before that, panels arriving above would push it out of view, and a scroll
  // started while a panel grows above it stops short.
  const settled = Boolean(workspace && jobs && characters && diagnostic && connections && check);
  const reached = useRef(false);
  useEffect(() => {
    const zone = window.location.hash.split(":")[1];
    if (!settled || reached.current || !zone) return;
    reached.current = true;
    window.requestAnimationFrame(reach(zone, "auto"));
  }, [settled]);

  const cards = workspace?.projects ?? [];
  const card = cards.find((entry) => entry.project === project);
  const recipe = recipes?.find((entry) => entry.project === project);
  const declared = recipe?.characters ?? [];
  const built = (characters ?? []).filter((character) => character.state === "done");
  const review = (characters ?? []).filter((character) => character.state === "needs_review");
  const states = queue?.states ?? {};
  const missing = declared.filter(
    (id) => !(characters ?? []).some((character) => character.id === id && character.state === "done"),
  );
  const paid = (jobs ?? []).filter((job) => job.cost_usd > 0);
  const failures = (jobs ?? []).filter((job) => job.state === "failed" && job.kind !== "ping");
  const canBuild = Boolean(recipe && !recipe.error);

  // Most urgent first: what failed, what the station lacks, what waits to be
  // built or reviewed -- here, then in the other projects.
  const todos: Todo[] = [];
  const lastFailure = failures[0];
  if (lastFailure) {
    todos.push({
      tone: "danger",
      title: t("{jobTitle} failed", { jobTitle: jobTitle(lastFailure) }),
      detail: (
        <>
          {shortDate(lastFailure.updated_at)}
          {lastFailure.cost_usd ? t(" · {cost_usd} spent", { cost_usd: cost(lastFailure.cost_usd) }) : t(" · nothing was billed")}
          {lastFailure.error ? ` · ${tr(lastFailure.error)}` : ""}
        </>
      ),
      actions: <button className="btn btn-ghost btn-sm" onClick={reach("log")}>{t("See the log")}</button>,
    });
  }
  for (const entry of diagnostic?.checks ?? []) {
    if (entry.status === "ok" || DOCTOR_SKIP.has(entry.id)) continue;
    todos.push({
      tone: entry.status === "failed" ? "danger" : "warn",
      title: `${tr(entry.label)} — ${tr(entry.status)}`,
      detail: tr(entry.fix || entry.detail),
      actions: <button className="btn btn-ghost btn-sm" onClick={reach("station")}>{t("Diagnostics")}</button>,
    });
  }
  if (connections?.counts.unavailable) {
    const n = connections.counts.unavailable;
    todos.push({
      tone: "warn",
      title: tn(n, "{n} MCP server unreachable", "{n} MCP servers unreachable"),
      detail: (
        <span className="mono">
          {connections.sources
            .flatMap((entry) => entry.servers)
            .filter((server) => !server.available)
            .map((server) => server.name)
            .join(", ")}
        </span>
      ),
      actions: <button className="btn btn-ghost btn-sm" onClick={reach("station")}>{t("Connections")}</button>,
    });
  }
  if (missing.length) {
    todos.push({
      tone: "next",
      title: tn(missing.length, "{n} character of the recipe is not built yet",
        "{n} characters of the recipe are not built yet"),
      detail: (
        <>
          <span className="mono">{missing.join(", ")}</span>{" "}
          {missing.length > 1 ? t("are declared in") : t("is declared in")}{" "}
          <span className="mono">{recipe?.path.split("/").slice(-2).join("/")}</span>.
        </>
      ),
      actions: (
        <button
          className="btn btn-secondary btn-sm"
          disabled={!canBuild}
          onClick={() => setConfirming({ characters: missing, force: false })}
        >
          {t("Build")}
        </button>
      ),
    });
  }
  if (review.length) {
    todos.push({
      tone: "warn",
      title: tn(review.length, "{n} output to review", "{n} outputs to review"),
      detail: <span className="mono">{review.map((character) => character.name).join(", ")}</span>,
      actions: (
        <button
          className="btn btn-secondary btn-sm"
          onClick={go(
            (cardHref(entities, review[0]!.id, "3d") ?? "#library").slice(1),
          )}
        >
          {t("Review")}
        </button>
      ),
    });
  }
  for (const other of cards) {
    if (other.project === project || !other.counters.to_review) continue;
    todos.push({
      tone: "warn",
      title: tn(other.counters.to_review, "{n} output to review in {title}", "{n} outputs to review in {title}",
        { title: other.title }),
      detail: <span className="mono">{other.project}</span>,
      actions: (
        <button className="btn btn-ghost btn-sm" onClick={() => setProject(other.project)}>
          {t("Switch to {project}", { project: other.project })}
        </button>
      ),
    });
  }

  async function launch(body: { characters: string[]; force: boolean }) {
    try {
      const result = await api.build({
        recipe: project,
        characters: body.characters,
        force: body.force,
        confirm: true,
      });
      notify({
        kind: "success",
        title: t("{length} task(s) queued", { length: result.queued.length }),
        body: body.characters.length ? body.characters.join(", ") : t("the whole roster"),
      });
      refresh(project);
    } catch (error) {
      notify({ kind: "error", title: t("Build refused"), body: (error as ApiError).message });
    } finally {
      setConfirming(null);
    }
  }

  const counters = workspace?.counters;

  return (
    <>
      <PageHeader
        title={t("Control room")}
        project={project}
        actions={
          sections?.length ? (
            <button className="btn btn-primary" onClick={go(`world:${sections[0]!.id}`)}>
              {t("The world")}
            </button>
          ) : undefined
        }
      />

      <MetricGrid>
        <Metric label={t("projects")} value={counters?.projects ?? "—"} />
        <Metric label={tn(counters?.jobs_active ?? 0, "task running", "tasks running")} value={counters?.jobs_active ?? "—"} />
        <Metric label={t("outputs to review")} value={counters?.to_review ?? "—"} />
        <Metric label={t("spent, all projects")} value={counters ? cost(counters.cost_usd) : "—"} />
      </MetricGrid>

      {todos.length > 0 && (
        <Panel
          eyebrow={tn(todos.length, "{n} open item", "{n} open items")}
          title={t("To do now")}
          bodyClass="tight"
          style={{ marginBottom: "var(--space-5)" }}
        >
          <div className="todo">
            {todos.map((todo, index) => (
              <div className={`todo-item is-${todo.tone}`} key={index}>
                <span className="ind">
                  <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">{TODO_ICONS[todo.tone]}</svg>
                </span>
                <div>
                  <b>{todo.title}</b>
                  <p>{todo.detail}</p>
                </div>
                <div className="acts">{todo.actions}</div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <section className="zone" id="zone-project">
        <div className="zone-head">
          <span className="step-num">1</span>
          <div className="grow">
            <p className="eyebrow">{t("Active project")}</p>
            <h2>{card?.title || project || t("no project")}</h2>
          </div>
        </div>

        <div className="split">
          <div className="col" style={{ gap: "var(--space-5)" }}>
            <Panel
              eyebrow={characters?.length
                ? tn(built.length, "Roster · {n}/{total} built", "Roster · {n}/{total} built", { total: characters.length })
                : t("Roster")}
              title={t("Entities and progress")}
              actions={
                <div className="legend">
                  <State state="done" label={t("done")} />
                  <State state="pending" label={t("to do")} />
                </div>
              }
              bodyClass="tight"
            >
              {characters?.length ? (
                <div className="entities">
                  {characters.map((character) => (
                    <Entity key={character.id} character={character} entities={entities} />
                  ))}
                </div>
              ) : (
                <Empty title={t("No entity")} />
              )}
            </Panel>

            <JobLog
              id="zone-log"
              jobs={jobs ?? []}
              running={Boolean(states.running)}
            />
          </div>

          <div className="col" style={{ gap: "var(--space-5)" }}>
            <Panel
              eyebrow={recipe?.path.split("/").slice(-2).join("/") ?? "recipe"}
              title={t("Recipe")}
              actions={recipe?.error ? <Badge tone="danger">{t("invalid")}</Badge> : undefined}
              foot={
                <div className="row row-wrap gap-2">
                  <button
                    className="btn btn-secondary btn-sm"
                    disabled={!canBuild}
                    onClick={() => setConfirming({ characters: [], force: false })}
                  >
                    {t("Build the recipe")}
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    disabled={!canBuild}
                    onClick={() => setExploring(true)}
                  >
                    {t("Explore the style…")}
                  </button>
                </div>
              }
            >
              {recipe ? (
                <>
                  <div className="recipe-sum">
                    <div>
                      <span className="eyebrow">{t("Style")}</span>
                      <b>{recipe.style ?? "—"}</b>
                      <span className="hint">{recipe.lora ? `LoRA ${recipe.lora}` : t("LoRA not trained yet")}</span>
                    </div>
                    <div>
                      <span className="eyebrow">{t("Characters")}</span>
                      <b>{declared.length ? declared.join(", ") : "—"}</b>
                      <span className="hint">{tn(declared.length, "{n} declared", "{n} declared")}</span>
                    </div>
                    <div>
                      <span className="eyebrow">{t("File")}</span>
                      <b className="mono">{recipe.path.split("/").pop()}</b>
                    </div>
                  </div>
                  {recipe.error && (
                    <p className="hint" style={{ color: "var(--danger)", marginTop: "var(--space-4)" }}>{tr(recipe.error)}</p>
                  )}
                  {source && (
                    <details className="more" style={{ marginTop: "var(--space-5)", marginBottom: "calc(-1 * var(--space-5))" }}>
                      <summary>{t("See the YAML file")}</summary>
                      <div className="more-body">
                        <pre className="recipe" style={{ maxHeight: 420, overflow: "auto" }}>{source.text}</pre>
                      </div>
                    </details>
                  )}
                </>
              ) : (
                <Empty title={t("No recipe")} />
              )}
            </Panel>

            <Panel eyebrow={t("Spending · Runware")} title={t("Cost")}>
              <Facts
                rows={[
                  [t("Project total"), <span style={{ fontSize: "var(--text-lg)" }}>{cost(card?.counters.cost_usd ?? queue?.cost_usd ?? 0)}</span>],
                  [t("Billed tasks"), paid.length],
                  [t("Last spending"), paid[0] ? `${cost(paid[0].cost_usd)} · ${shortDate(paid[0].updated_at)}` : "—"],
                  [t("{label} image", { label: BUILD_IMAGE?.label ?? "—" }), price(BUILD_IMAGE)],
                  [t("{label} mesh", { label: BUILD_MESH?.label ?? "—" }), price(BUILD_MESH)],
                  [t("Sprites, export, inventory"), t("free · local")],
                ]}
              />
            </Panel>
          </div>
        </div>
      </section>

      <section className="zone" id="zone-station">
        <div className="zone-head">
          <span className="step-num">2</span>
          <div className="grow">
            <p className="eyebrow">{t("This machine")}</p>
            <h2>{t("Tools, connections, procedures")}</h2>
          </div>
          <div className="row gap-3">
            <span className="eyebrow">{t("Language")}</span>
            <Seg<Lang>
              value={lang}
              options={LANGS.map((entry) => ({ value: entry.id, label: entry.label }))}
              onChange={setLang}
            />
          </div>
        </div>
        <Station diagnostic={diagnostic} connections={connections} check={check} />
      </section>

      {confirming && (
        <BuildDialog
          project={project}
          characters={confirming.characters}
          roster={declared.length}
          force={confirming.force}
          onForce={(force) => setConfirming({ ...confirming, force })}
          onLaunch={() => launch(confirming)}
          onClose={() => setConfirming(null)}
        />
      )}

      {exploring && recipe && (
        <ExploreDialog
          project={project}
          style={recipe.style}
          onClose={() => setExploring(false)}
          onQueued={() => refresh(project)}
        />
      )}
    </>
  );
}

/**
 * Building a recipe: the amount is stated before sending, per character and
 * per model. It is a ceiling -- a step already computed comes from the cache
 * and costs nothing --, except when recomputing everything, where it is due in
 * full.
 */
function BuildDialog({ project, characters, roster, force, onForce, onLaunch, onClose }: {
  project: string;
  /** The characters to build; none means the whole roster. */
  characters: string[];
  /** How many characters the recipe declares. */
  roster: number;
  force: boolean;
  onForce: (force: boolean) => void;
  onLaunch: () => void;
  onClose: () => void;
}) {
  const count = characters.length || roster;
  const total = count * ((BUILD_IMAGE?.usd ?? 0) + (BUILD_MESH?.usd ?? 0));
  const priced = Boolean(BUILD_IMAGE && BUILD_MESH);
  return (
    <Dialog
      eyebrow={t("Paid generation")}
      title={t("Build the {project} recipe?", { project })}
      onClose={onClose}
      foot={
        <>
          <Badge tone="warn">{t("paid")}</Badge>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Go back")}</button>
          <button className="btn btn-primary" disabled={!priced} onClick={onLaunch}>
            {force
              ? t("Pay {total} and start", { total: cost(total) })
              : t("Pay at most {total} and start", { total: cost(total) })}
          </button>
        </>
      }
    >
      <div className="summary">
        <div className="line">
          <b className="grow">{t("Characters")}</b>
          <span className="mono">{characters.length ? characters.join(", ") : t("the whole roster")}</span>
        </div>
        <div className="line">
          <b className="grow">{t("{label} image", { label: BUILD_IMAGE?.label ?? "—" })}</b>
          <span className="mono">{count} × {price(BUILD_IMAGE)}</span>
        </div>
        <div className="line">
          <b className="grow">{t("{label} mesh", { label: BUILD_MESH?.label ?? "—" })}</b>
          <span className="mono">{count} × {price(BUILD_MESH)}</span>
        </div>
        <div className="line">
          <b className="grow">{t("Godot export, library")}</b>
          <Badge tone="quiet">{t("local")}</Badge>
          <span className="mono">{cost(0)}</span>
        </div>
      </div>
      <Setting label={t("Recompute everything — ignores the cache, costs the full price again")}>
        <Toggle checked={force} onChange={onForce} label={t("recompute everything")} />
      </Setting>
      <p className="hint" style={{ margin: 0 }}>
        {t("Steps already computed come from the cache and cost nothing. If you cancel, nothing is billed.")}
      </p>
    </Dialog>
  );
}

/**
 * Exploring the art direction: variants of one subject in the project's style,
 * to choose what will train the LoRA. The price is small but real: it is
 * stated before sending.
 */
function ExploreDialog({ project, style, onClose, onQueued }: {
  project: string;
  style?: string;
  onClose: () => void;
  onQueued: () => void;
}) {
  const { notify } = useStudio();
  const [subject, setSubject] = useState("");
  const [count, setCount] = useState(8);
  const [busy, setBusy] = useState(false);
  const estimate = count * (EXPLORE_MODEL?.usd ?? 0);

  async function submit() {
    setBusy(true);
    try {
      const result = await api.explore({ recipe: project, subject, count, confirm: true });
      notify({ kind: "success", title: t("Exploration queued"), body: t("{count} variants — {project}", { count, project: result.project }) });
      onQueued();
      onClose();
    } catch (error) {
      notify({ kind: "error", title: t("Exploration refused"), body: (error as ApiError).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      eyebrow={t("Art direction")}
      title={t("Explore the {v} style", { v: style ?? project })}
      onClose={onClose}
      foot={
        <>
          <Badge tone="warn">{t("paid")}</Badge>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={busy || !subject.trim() || !EXPLORE_MODEL} onClick={submit}>
            {busy ? t("sending…") : t("Pay {estimate} and start", { estimate: cost(estimate) })}
          </button>
        </>
      }
    >
      <Field label={t("Subject of the variants")}>
        <input
          value={subject}
          autoFocus
          onChange={(event) => setSubject(event.target.value)}
          placeholder={t("a village blacksmith, full body, front view")}
        />
      </Field>
      <div className="field">
        <span>
          {t("Number of variants")} <span className="mono" style={{ color: "var(--fg)" }}>{count}</span>
        </span>
        <Slider value={count} min={4} max={16} step={1} onChange={setCount} bounds={false} />
      </div>
      <div className="cost-box">
        <span className="eyebrow">{t("Estimated cost")}</span>
        <strong>{cost(estimate)}</strong>
        <span className="hint">
          {t("{label} · charged to the Runware account", { label: EXPLORE_MODEL?.label ?? "—" })}
        </span>
      </div>
    </Dialog>
  );
}

/**
 * An entity and its chain: each step says whether it is done, and leads to the
 * step of its world card where it is taken up again. An entity created before
 * the world has no card: it can be given one.
 */
function Entity({ character, entities }: { character: Character; entities: WorldEntity[] | undefined }) {
  const [adopting, setAdopting] = useState(false);
  const at = (step: "concepts" | "3d") =>
    (cardHref(entities, character.id, step) ?? "#library").slice(1);
  const steps = [
    { label: t("Image"), done: Boolean(character.concept_asset), page: at("concepts") },
    { label: t("Mesh"), done: character.has_rig3d, page: at("3d") },
    { label: t("Export"), done: Object.keys(character.exports).length > 0, page: at("3d") },
  ];
  const linked = cardHref(entities, character.id) !== null;
  const next = steps.find((step) => !step.done);

  return (
    <div className="entity">
      <span className="board ent-thumb">
        {character.concept_asset ? (
          <img src={assetPreviewUrl(character.concept_asset, 240)} alt={character.name} loading="lazy" />
        ) : (
          <span className="muted" style={{ display: "grid", placeItems: "center", height: "100%" }}>
            <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="4" y="4" width="16" height="16" rx="2" />
              <path d="M4 16l5-5 4 4 2-2 5 5" />
            </svg>
          </span>
        )}
      </span>
      <div className="ent-body">
        <div className="row row-wrap" style={{ gap: "var(--space-3)" }}>
          <h3>{character.name}</h3>
          {character.state !== "done" && <State state={character.state} />}
        </div>
        <nav className="chain" aria-label={t("Progress of {name}", { name: character.name })}>
          {steps.map((step, index) => (
            <Fragment key={step.label}>
              {index > 0 && <span className="link" />}
              <button className={`step ${step.done ? "is-done" : "is-todo"}`} onClick={go(step.page)}>
                <span className="dot" />
                {step.label}
              </button>
            </Fragment>
          ))}
        </nav>
        {character.errors.length > 0 && (
          <p className="hint" style={{ color: "var(--danger)" }}>{character.errors[0]}</p>
        )}
      </div>
      {linked ? (
        <button className="btn btn-secondary btn-sm" onClick={go(next?.page ?? at("3d"))}>
          {next ? t("Resume") : t("Retouch")}
        </button>
      ) : (
        <button className="btn btn-secondary btn-sm" onClick={() => setAdopting(true)}>
          {t("Give it a card")}
        </button>
      )}
      {adopting && <Adopt character={character} onClose={() => setAdopting(false)} />}
    </div>
  );
}

/**
 * Gives a world card to an entity without one: the card takes the entity's
 * name, which is enough to link them -- its concepts and 3D then open from it.
 */
function Adopt({ character, onClose }: { character: Character; onClose: () => void }) {
  const { project, notify } = useStudio();
  const { data: sections } = useWorld(project);
  const refresh = useRefreshProject();
  const [section, setSection] = useState("");
  const chosen = section || sections?.[0]?.id || "";

  async function adopt() {
    try {
      const created = await api.createDocument(project, character.id, "card", `world/${chosen}`);
      refresh(project);
      window.location.hash = `world:${chosen}/${created.name}/card`;
    } catch (failure) {
      notify({ kind: "error", title: t("Card refused"), body: (failure as ApiError).message });
    }
  }

  return (
    <Dialog
      eyebrow={character.id}
      title={t("Give it a card")}
      onClose={onClose}
      foot={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!chosen} onClick={() => void adopt()}>{t("Create the card")}</button>
        </>
      }
    >
      {sections?.length ? (
        <Setting label={t("Section")}>
          <Select
            label={t("Section")}
            value={chosen}
            options={sections.map((entry) => ({ value: entry.id, label: entry.label }))}
            onChange={setSection}
          />
        </Setting>
      ) : (
        <Empty title={t("No world section")} hint={t("First create a section from the “+” in the rail.")} />
      )}
    </Dialog>
  );
}
