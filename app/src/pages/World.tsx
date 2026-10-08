/**
 * World: the sections the user declares, and their cards.
 *
 * No section exists by default -- characters, buildings, factions: the user
 * creates them, from the category's "+" in the rail. A section is a documents
 * shelf (`.gamestudio/documents/world/<id>/`); its page shows its cards as
 * tiles, grouped by its first **axis** if it declares one, and each card opens
 * its workbench (`Workbench.tsx`): there, and only there, things are generated
 * and filed.
 *
 * An axis is a grid of closed values -- "Race": Human, Elf, Orc --, declared
 * **at project level**: a section reuses the axes that already exist instead
 * of rewriting them, and fixing an axis from one section fixes it everywhere.
 * A card carries one value per axis, which files it without renaming it;
 * renaming a label detaches no card, only removing a value does.
 *
 * Each group has its "New card" -- the request goes to an agent, and what it
 * writes is filed there --, and each card its bubble: an agent conversation on
 * it (`EntityHandoff`).
 *
 * The address says it all: `#world:<section>` for the section,
 * `#world:<section>/<card>/<step>` for a card's workbench.
 */

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, assetPreviewUrl, type WorldAxis, type WorldEntity, type WorldProjectAxis, type WorldSection,
} from "../api";
import {
  Badge, CloseCross, Dialog, Empty, Field, PageHeader, Panel, Section, State, shortDate,
} from "../components/ui";
import { useDocumentTemplates, useEntities, useWorld, useWorldAxes } from "../lib/queries";
import { useStudio } from "../lib/store";
import { ChatPlusGlyph, NewDocument } from "./Documents";
import WorkbenchPage, { EntityHandoff, type Step } from "./Workbench";
import { t } from "../lib/i18n";

/* The icons a section can carry: the same names as `service/world.py`, which
   refuses any other. */
export const WORLD_ICONS: Record<string, { label: string; glyph: ReactNode }> = {
  character: {
    label: t("Character"),
    glyph: (
      <>
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 20.5c.8-3.8 3.6-6 7-6s6.2 2.2 7 6" />
      </>
    ),
  },
  building: {
    label: t("Building"),
    glyph: <path d="M4 20.5h16M6 20.5V9l6-4.5L18 9v11.5M10 20.5v-5h4v5" />,
  },
  place: {
    label: t("Place"),
    glyph: (
      <>
        <path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z" />
        <circle cx="12" cy="10" r="2.3" />
      </>
    ),
  },
  map: {
    label: t("Map"),
    glyph: <path d="M3.5 6.5l5.5-2 6 2 5.5-2v13l-5.5 2-6-2-5.5 2zM9 4.5v13M15 6.5v13" />,
  },
  item: {
    label: t("Item"),
    glyph: <path d="M4 9.5h16v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5zM4 9.5l1.5-4h13l1.5 4M10 13h4" />,
  },
  creature: {
    label: t("Creature"),
    glyph: (
      <>
        <circle cx="6.5" cy="10" r="1.8" />
        <circle cx="12" cy="6.5" r="1.8" />
        <circle cx="17.5" cy="10" r="1.8" />
        <path d="M12 12c-3 0-5.5 3-5.5 5.5 0 1.7 1.5 2.5 3 2 1-.3 1.6-.6 2.5-.6s1.5.3 2.5.6c1.5.5 3-.3 3-2 0-2.5-2.5-5.5-5.5-5.5z" />
      </>
    ),
  },
  faction: {
    label: t("Faction"),
    glyph: <path d="M12 3.5l7 2.5v5.5c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6z" />,
  },
  book: {
    label: t("Book"),
    glyph: <path d="M4.5 5.5A1.5 1.5 0 0 1 6 4h12.5v14H6a1.5 1.5 0 0 0-1.5 1.5zM4.5 19.5A1.5 1.5 0 0 0 6 21h12.5v-3" />,
  },
  weapon: {
    label: t("Weapon"),
    glyph: <path d="M19.5 4.5L9.5 14.5M19.5 4.5h-4M19.5 4.5v4M7 12l5 5M8.5 15.5l-4 4" />,
  },
  vehicle: {
    label: t("Vehicle"),
    glyph: (
      <>
        <path d="M3.5 6.5h11v9h-11zM14.5 10h3.5l2.5 3v2.5h-6" />
        <circle cx="7" cy="17.5" r="1.8" />
        <circle cx="17" cy="17.5" r="1.8" />
      </>
    ),
  },
  plant: {
    label: t("Plant"),
    glyph: <path d="M12 20.5V11M12 11c0-4 3-6.5 7-6.5 0 4-3 6.5-7 6.5zM12 14c0-3-2.5-5-6-5 0 3 2.5 5 6 5z" />,
  },
  star: {
    label: t("Star"),
    glyph: <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />,
  },
};

export function WorldGlyph({ icon }: { icon: string }) {
  return (
    <span className="glyph">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        {(WORLD_ICONS[icon] ?? WORLD_ICONS.book!).glyph}
      </svg>
    </span>
  );
}

/* A glyph in an icon button is a path, never a character: "+" depends on the
   font, and font substitution changes its size as much as its shape. The
   cross is the studio-wide one (`CloseCross`). */
const PLUS = <path d="M12 6v12M6 12h12" />;
/** A card and its "+": create in this group. */
const CARD_PLUS = (
  <>
    <path d="M7 3.5h7l4 4v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" />
    <path d="M14 3.5V8h4M12 11v6M9 14h6" />
  </>
);

/* A label's stable name, as `documents.slug` derives it on the server: it is
   what tells that "Race" already exists under the id `race`. */
const slugOf = (text: string) => text.trim().toLowerCase().normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** Picking an icon: a grid of buttons, one pressed. */
function IconPicker({ value, onChange }: { value: string; onChange: (icon: string) => void }) {
  return (
    <div className="icon-picker" role="radiogroup" aria-label={t("Icon")}>
      {Object.entries(WORLD_ICONS).map(([id, entry]) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={id === value}
          title={entry.label}
          aria-label={entry.label}
          onClick={() => onChange(id)}
        >
          <WorldGlyph icon={id} />
        </button>
      ))}
    </div>
  );
}

/* An axis draft: `key` exists only while the dialog is open, `id` is the
   server's -- empty for what was just added, and the service derives it from
   the label. Keeping `id` is what makes a rename detach nothing. */
type DraftValue = { key: string; id: string; label: string };
type DraftAxis = { key: string; id: string; label: string; values: DraftValue[] };

let draftRank = 0;
const draftKey = () => `draft-${(draftRank += 1)}`;

function toDraft(axes: WorldAxis[]): DraftAxis[] {
  return axes.map((axis) => ({
    key: draftKey(), id: axis.id, label: axis.label,
    values: axis.values.map((value) => ({ key: draftKey(), id: value.id, label: value.label })),
  }));
}

function fromDraft(axes: DraftAxis[]): WorldAxis[] {
  return axes
    .filter((axis) => axis.label.trim())
    .map((axis) => ({
      id: axis.id, label: axis.label.trim(),
      values: axis.values
        .filter((value) => value.label.trim())
        .map((value) => ({ id: value.id, label: value.label.trim() })),
    }));
}

/**
 * A section's filing axes: each a name, and the values that divide it. An axis
 * belongs to the project: existing ones are offered, and fixing one fixes it in
 * every section that uses it. A label can be fixed without disturbing
 * anything; removing a value detaches the cards that carried it, everywhere.
 */
function AxisEditor({ axes, catalog, section, onChange, onForget }: {
  axes: DraftAxis[];
  /** The project's axes, and who uses them. */
  catalog: WorldProjectAxis[];
  /** The section being edited; none for a creation. */
  section?: string;
  onChange: (axes: DraftAxis[]) => void;
  onForget: (axis: WorldProjectAxis) => void;
}) {
  const patch = (key: string, change: Partial<DraftAxis>) =>
    onChange(axes.map((axis) => (axis.key === key ? { ...axis, ...change } : axis)));
  const known = new Map(catalog.map((axis) => [axis.id, axis]));
  const offers = catalog.filter((axis) => !axes.some((draft) => draft.id === axis.id));
  // Reusing a project axis: its grid as it is, plus the values a draft of the
  // same name already had.
  const adopt = (axis: WorldProjectAxis, replacing?: DraftAxis) => {
    const [taken] = toDraft([axis]);
    if (!taken) return;
    if (!replacing) {
      onChange([...axes, taken]);
      return;
    }
    const have = new Set(axis.values.map((value) => value.id));
    taken.values.push(...replacing.values.filter((value) => value.label.trim()
      && !have.has(slugOf(value.label))));
    onChange(axes.map((entry) => (entry.key === replacing.key ? taken : entry)));
  };

  return (
    <Section title={t("Filing axes")}>
      {axes.map((axis) => {
        const elsewhere = (known.get(axis.id)?.sections ?? [])
          .filter((entry) => entry.id !== section);
        const twin = !axis.id && axis.label.trim()
          ? offers.find((entry) => entry.id === slugOf(axis.label)
              || slugOf(entry.label) === slugOf(axis.label))
          : undefined;
        return (
          <div className="axis-row" key={axis.key}>
            <div className="axis-head">
              <input
                value={axis.label}
                placeholder={t("Race")}
                aria-label={t("Axis name")}
                onChange={(event) => patch(axis.key, { label: event.target.value })}
              />
              {elsewhere.length > 0 && (
                <Badge tone="quiet">{t("also: {list}", { list: elsewhere.map((entry) => entry.label).join(", ") })}</Badge>
              )}
              {twin && (
                <button type="button" className="btn btn-secondary btn-sm"
                        onClick={() => adopt(twin, axis)}>
                  {t("Reuse “{label}”", { label: twin.label })}
                </button>
              )}
              <button
                className="btn btn-ghost btn-icon"
                aria-label={t("Remove the axis")}
                title={t("Remove the axis")}
                onClick={() => onChange(axes.filter((entry) => entry.key !== axis.key))}
              >
                <CloseCross />
              </button>
            </div>
            <div className="axis-values">
              {axis.values.map((value) => (
                <span className="axis-value" key={value.key}>
                  <input
                    value={value.label}
                    placeholder={t("Human")}
                    aria-label={t("Value")}
                    onChange={(event) => patch(axis.key, {
                      values: axis.values.map((entry) => (entry.key === value.key
                        ? { ...entry, label: event.target.value } : entry)),
                    })}
                  />
                  <button
                    className="btn btn-ghost btn-icon"
                    aria-label={t("Remove the value")}
                    title={t("Remove the value")}
                    onClick={() => patch(axis.key, {
                      values: axis.values.filter((entry) => entry.key !== value.key),
                    })}
                  >
                    <CloseCross />
                  </button>
                </span>
              ))}
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => patch(axis.key, {
                  values: [...axis.values, { key: draftKey(), id: "", label: "" }],
                })}
              >
                {t("Value")}
              </button>
            </div>
          </div>
        );
      })}
      <div className="axis-add">
        {offers.map((axis) => (
          <span className="axis-offer" key={axis.id}>
            <button type="button" className="btn btn-secondary btn-sm"
                    aria-label={t("Reuse the {label} axis", { label: axis.label })}
                    title={axis.values.map((value) => value.label).join(", ")}
                    onClick={() => adopt(axis)}>
              <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">{PLUS}</svg>
              {axis.label}
              <span className="mono">
                {axis.values.slice(0, 3).map((value) => value.label).join(" · ")}
                {axis.values.length > 3 ? ` +${axis.values.length - 3}` : ""}
              </span>
            </button>
            {axis.sections.length === 0 && (
              <button type="button" className="btn btn-ghost btn-icon"
                      aria-label={t("Forget {label}", { label: axis.label })} title={t("Forget")}
                      onClick={() => onForget(axis)}>
                <CloseCross />
              </button>
            )}
          </span>
        ))}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => onChange([...axes, { key: draftKey(), id: "", label: "", values: [] }])}
        >
          {t("Axis")}
        </button>
      </div>
    </Section>
  );
}

/**
 * Creating or editing a section: a label, an icon, and its axes. The folder
 * keeps the name it was created with.
 */
export function SectionDialog({ project, section, onClose, onDone }: {
  project: string;
  section?: WorldSection;
  onClose: () => void;
  onDone: (section: WorldSection) => void;
}) {
  const client = useQueryClient();
  const { notify } = useStudio();
  const { data: sections } = useWorld(project);
  const { data: catalog } = useWorldAxes(project);
  const [label, setLabel] = useState(section?.label ?? "");
  const [icon, setIcon] = useState(section?.icon ?? "character");
  const [axes, setAxes] = useState<DraftAxis[]>(() => toDraft(section?.axes ?? []));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ready = label.trim().length > 0 && !busy;

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const done = section
        ? await api.updateWorldSection(project, section.id, { label: label.trim(), icon })
        : await api.createWorldSection(project, label.trim(), icon);
      const saved = await api.setWorldAxes(project, done.id, fromDraft(axes));
      if (saved.detached) {
        const named = (id: string) => sections?.find((entry) => entry.id === id)?.label ?? id;
        notify({
          kind: "info",
          title: t("{n} card(s) detached", { n: saved.detached }),
          body: Object.entries(saved.detached_in)
            .map(([id, count]) => `${named(id)}: ${count}`).join(" · "),
        });
      }
      await client.invalidateQueries({ queryKey: ["world", project] });
      await client.invalidateQueries({ queryKey: ["entities", project] });
      onDone(saved);
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  const forget = async (axis: WorldProjectAxis) => {
    setError(null);
    try {
      await api.forgetWorldAxis(project, axis.id);
      await client.invalidateQueries({ queryKey: ["world", project, "axes"] });
    } catch (failure) {
      setError((failure as Error).message);
    }
  };

  return (
    <Dialog
      title={section ? t("Edit the section") : t("New section")}
      eyebrow={section ? `.gamestudio/documents/${section.folder}/` : t("The world")}
      onClose={onClose}
      foot={
        <>
          <span className="hint grow">{error && <Badge tone="danger">{error}</Badge>}</span>
          <button className="btn btn-ghost" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn btn-primary" disabled={!ready} onClick={() => void submit()}>
            {section ? t("Save") : t("Create")}
          </button>
        </>
      }
    >
      <div className="stack-4">
        <Field label={t("Name")}>
          <input
            value={label}
            autoFocus
            placeholder={t("Characters")}
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void submit()}
          />
        </Field>
        <Section title={t("Icon")}>
          <IconPicker value={icon} onChange={setIcon} />
        </Section>
        <AxisEditor axes={axes} catalog={catalog ?? []} section={section?.id}
                    onChange={setAxes} onForget={(axis) => void forget(axis)} />
      </div>
    </Dialog>
  );
}

/**
 * The address of the workbench of the card an entity was born from, or `null`
 * if no card carries it (a character older than the world, for instance).
 */
export function cardHref(entities: WorldEntity[] | undefined, entity: string,
                          step: Step = "card"): string | null {
  const card = entities?.find((entry) => entry.entity === entity);
  return card ? `#world:${card.section}/${card.name}/${step}` : null;
}

const STEPS: readonly Step[] = ["card", "concepts", "3d"];

/** `section/card/step`: the section alone, or a card's workbench. */
function parse(id: string): { section: string; name: string; step: Step } {
  const [section = "", name = "", step = ""] = id.split("/");
  return { section, name, step: (STEPS as readonly string[]).includes(step) ? (step as Step) : "card" };
}

/** The world page: a section and its cards, or a card's workbench. */
export default function WorldPage({ id, onGone }: { id: string; onGone: () => void }) {
  const { section, name, step } = parse(id);
  const go = (path: string) => { window.location.hash = `world:${path}`; };
  if (name) {
    return (
      <WorkbenchPage
        key={`${section}/${name}`}
        section={section}
        name={name}
        step={step}
        onStep={(next) => go(`${section}/${name}/${next}`)}
        onGone={() => go(section)}
      />
    );
  }
  return <SectionView id={section} onGone={onGone} onOpen={(card) => go(`${section}/${card}/card`)} />;
}

/** A section's page: its cards filed by axis, and the means to edit it. */
function SectionView({ id, onGone, onOpen }: { id: string; onGone: () => void; onOpen: (name: string) => void }) {
  const { project, notify } = useStudio();
  const client = useQueryClient();
  const { data: sections, isLoading } = useWorld(project);
  const { data: entities } = useEntities(project);
  const { data: templates } = useDocumentTemplates();
  const [editing, setEditing] = useState(false);
  // A creation: in the whole section, or in one value's group.
  const [creating, setCreating] = useState<null | { values: Record<string, string>; label: string } | "section">(null);
  const [discussing, setDiscussing] = useState<WorldEntity | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const section = sections?.find((entry) => entry.id === id);
  const cards = (entities ?? []).filter((entry) => entry.section === id);

  if (!section) {
    return (
      <div className="stack-4">
        <PageHeader title={t("The world")} project={project} />
        <Empty title={isLoading ? t("Reading…") : t("Section not found")} />
      </div>
    );
  }

  // The first axis lays out the page; the others show as badges on the tile.
  // A declared axis shows its values even when empty: that is its grid.
  const [primary, ...rest] = section.axes ?? [];
  const groups = primary
    ? [
        ...primary.values.map((value) => ({
          key: value.id,
          label: value.label,
          filing: { values: { [primary.id]: value.id }, label: value.label },
          cards: cards.filter((card) => card.axes?.[primary.id] === value.id),
        })),
        ...(cards.some((card) => !card.axes?.[primary.id])
          ? [{
              // Not a slug (`[a-z0-9-]`): cannot collide with a value id.
              key: ":none",
              label: t("No {axis}", { axis: primary.label.toLowerCase() }),
              filing: null,
              cards: cards.filter((card) => !card.axes?.[primary.id]),
            }]
          : []),
      ]
    : [];

  const remove = async () => {
    setBusy(true);
    try {
      const gone = await api.deleteWorldSection(project, section.id, cards.length > 0);
      await client.invalidateQueries({ queryKey: ["world", project] });
      await client.invalidateQueries({ queryKey: ["entities", project] });
      notify({
        kind: "info",
        title: t("{label} removed", { label: section.label }),
        body: gone.cards.length ? t("{length} card(s) deleted", { length: gone.cards.length }) : undefined,
      });
      onGone();
    } catch (failure) {
      notify({ kind: "error", title: t("Removal refused"), body: (failure as Error).message });
      setBusy(false);
      setRemoving(false);
    }
  };

  const cardGrid = (list: WorldEntity[]) => (
    <div className="assets">
      {list.map((card) => (
        <div key={card.name} className="card-tile">
          <button className="asset" onClick={() => onOpen(card.name)}>
            <span className={card.cover ? "thumb" : "thumb plain"}>
              {card.cover ? (
                <img src={assetPreviewUrl(card.cover, 320)} alt="" loading="lazy" />
              ) : (
                <WorldGlyph icon={section.icon} />
              )}
            </span>
            <span className="meta">
              <b>{card.title}</b>
              <span className="card-steps">
                {card.concept ? <State state="done" label={t("chosen concept")} />
                  : card.concepts ? <span>{t("{concepts} concept(s)", { concepts: card.concepts })}</span>
                    : <span>{t("card")}</span>}
                {card.character?.has_rig3d && <State state="done" label="3D" />}
                {rest.map((axis) => {
                  const value = axis.values.find((entry) => entry.id === card.axes?.[axis.id]);
                  return value ? <Badge key={axis.id}>{value.label}</Badge> : null;
                })}
              </span>
              {card.modified_at && <span>{shortDate(card.modified_at)}</span>}
            </span>
          </button>
          <span className="card-tools">
            <button type="button" className="btn btn-secondary btn-icon"
                    aria-label={t("Discuss {title}", { title: card.title })} title={t("Discuss")}
                    onClick={() => setDiscussing(card)}>
              <ChatPlusGlyph />
            </button>
          </span>
        </div>
      ))}
    </div>
  );

  // The project folder is already in the breadcrumb: the path starts from it.
  const eyebrow = `.gamestudio/documents/${section.folder}/`;

  return (
    <div className="stack-4">
      <PageHeader
        title={section.label}
        project={project}
        actions={
          <>
            <button className="btn btn-ghost" onClick={() => setEditing(true)}>{t("Edit")}</button>
            <button className="btn btn-ghost" onClick={() => setRemoving(true)}>{t("Remove")}</button>
            <button className="btn btn-primary" disabled={!project} onClick={() => setCreating("section")}>
              {t("New card")}
            </button>
          </>
        }
      />

      {!primary ? (
        <Panel eyebrow={eyebrow} title={t("{n} card(s)", { n: cards.length })}>
          {!cards.length ? <Empty title={t("No card")} /> : cardGrid(cards)}
        </Panel>
      ) : (
        groups.map((group) => (
          <Panel key={group.key} eyebrow={eyebrow}
                 title={`${group.label} · ${group.cards.length}`}
                 actions={group.filing && (
                   <button type="button" className="btn btn-secondary btn-icon"
                           disabled={!project}
                           aria-label={t("New card — {label}", { label: group.label })} title={t("New card")}
                           onClick={() => setCreating(group.filing)}>
                     <svg viewBox="0 0 24 24" aria-hidden="true">{CARD_PLUS}</svg>
                   </button>
                 )}>
            {!group.cards.length ? <Empty title={t("No card")} /> : cardGrid(group.cards)}
          </Panel>
        ))
      )}

      {creating && (
        <NewDocument
          project={project}
          folder={section.folder}
          label={section.label}
          initial="card"
          templates={templates ?? []}
          filing={creating === "section" ? undefined : creating}
          onClose={() => setCreating(null)}
          onCreated={(name) => {
            setCreating(null);
            void client.invalidateQueries({ queryKey: ["entities", project] });
            void client.invalidateQueries({ queryKey: ["world", project] });
            onOpen(name);
          }}
        />
      )}
      {discussing && (
        <EntityHandoff
          project={project}
          card={{ section: section.id, name: discussing.name, title: discussing.title,
                   axes: discussing.axes ?? {}, concepts: discussing.concepts,
                   concept: discussing.concept, mesh: Boolean(discussing.character?.has_rig3d) }}
          axes={section.axes}
          onClose={() => setDiscussing(null)}
        />
      )}
      {editing && (
        <SectionDialog
          project={project}
          section={section}
          onClose={() => setEditing(false)}
          onDone={() => setEditing(false)}
        />
      )}
      {removing && (
        <Dialog
          title={t("Remove {label}", { label: section.label })}
          eyebrow={eyebrow}
          onClose={() => setRemoving(false)}
          foot={
            <>
              <span className="hint grow" />
              <button className="btn btn-ghost" onClick={() => setRemoving(false)}>{t("Cancel")}</button>
              <button className="btn btn-danger" disabled={busy} onClick={() => void remove()}>
                {cards.length ? t("Remove and delete {length} card(s)", { length: cards.length }) : t("Remove")}
              </button>
            </>
          }
        >
          {cards.length ? (
            <p className="hint">
              {t("The cards and their workbenches will be deleted; the concepts and meshes already produced stay in the library.")}
            </p>
          ) : null}
        </Dialog>
      )}
    </div>
  );
}
