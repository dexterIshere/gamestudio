/**
 * Views: the game's main pages, found in its files, each drawn by its own engine.
 *
 * The pages the player moves between -- the one the game starts on first, then
 * the screens and worlds its scripts open (`service/views.py`). Each is drawn
 * by Godot off screen when the page asks for it, and kept until its scene
 * changes. A view says where it is opened from; its title is that of the
 * interface card describing it, when there is one.
 *
 * The images are asked for one after the other: a drawing is a whole engine
 * run, and the browser keeps only a few requests open at once -- drawn all
 * together, they would hold up the rest of the studio.
 */

import { useState } from "react";
import { gameViewImageUrl, type GameView } from "../api";
import { useGameViews } from "../lib/queries";
import { useStudio } from "../lib/store";
import { Badge, Dialog, Empty, PageHeader } from "../components/ui";
import { t, tn, tr } from "../lib/i18n";

const KINDS: Record<GameView["kind"], string> = {
  screen: t("Screen"),
  "world-2d": t("2D world"),
  "world-3d": t("3D world"),
};

const basename = (file: string) => file.split("/").pop() ?? file;

export default function Views() {
  const { project } = useStudio();
  const { data, error, isLoading } = useGameViews(project);
  const [shown, setShown] = useState<GameView | null>(null);
  // How many views may ask for their image: one more each time one arrives.
  const [allowed, setAllowed] = useState(1);
  const next = () => setAllowed((held) => held + 1);

  return (
    <div className="views">
      <PageHeader
        title={t("Views")}
        project={project}
        trail={[{ label: t("Interface"), href: "#interface" }]}
        actions={data && <span className="num views-total">{tn(data.views.length, "{n} view", "{n} views")}</span>}
      />
      {isLoading ? (
        <Empty title={t("Reading the game…")} />
      ) : error ? (
        <Empty title={tr((error as Error).message)} />
      ) : data && data.views.length === 0 ? (
        <Empty title={t("No view found in the game")} />
      ) : data ? (
        <ul className="views-grid">
          {data.views.map((view, rank) => (
            <ViewCard key={view.id} project={project} view={view} asked={rank < allowed}
                      onDone={next} onOpen={() => setShown(view)} />
          ))}
        </ul>
      ) : null}
      {shown && (
        <Dialog title={shown.title} eyebrow={<span className="mono">{shown.file}</span>} wide
                onClose={() => setShown(null)}>
          <img className="views-full" src={gameViewImageUrl(project, shown.id, shown.version)} alt="" />
        </Dialog>
      )}
    </div>
  );
}

function ViewCard({ project, view, asked, onDone, onOpen }: {
  project: string;
  view: GameView;
  /** Its turn to ask for its image has come. */
  asked: boolean;
  /** Its image arrived, or could not be drawn: the next one may ask. */
  onDone: () => void;
  onOpen: () => void;
}) {
  const [state, setState] = useState<"drawing" | "ready" | "failed">("drawing");
  const settle = (next: "ready" | "failed") => {
    setState(next);
    onDone();
  };
  return (
    <li className="views-card">
      <button type="button" className={`views-frame is-${state}`} onClick={onOpen}
              disabled={state !== "ready"} title={view.file}>
        {asked && (
          <img src={gameViewImageUrl(project, view.id, view.version)} alt=""
               onLoad={() => settle("ready")} onError={() => settle("failed")} />
        )}
        {state !== "ready" && (
          <span className="views-state">{state === "failed" ? t("Not drawn") : t("Drawing…")}</span>
        )}
      </button>
      <div className="views-meta">
        <h3>{view.title}</h3>
        <div className="views-tags">
          {view.entry && <Badge>{t("Start page")}</Badge>}
          <span>{KINDS[view.kind]}</span>
        </div>
        <span className="mono views-file" title={view.file}>{view.file}</span>
        {view.opened_from.length > 0 && (
          <span className="views-from">
            {t("Opened from")} <span className="mono">{view.opened_from.map(basename).join(", ")}</span>
          </span>
        )}
      </div>
    </li>
  );
}
