/**
 * VFX: effect concepts, and their handoff to an agent who builds them in Godot.
 *
 * A concept is written and refined here (`.gamestudio/documents/design/vfx/`).
 * The studio does not make the effect: "Create in Godot" hands the concept to
 * an agent -- a new conversation or an open one in the Chats window --, which
 * receives a complete brief and builds the effect in the Godot project.
 */

import { useState } from "react";
import { api, type ProjectDocument } from "../api";
import HandoffDialog from "../components/Handoff";
import { useStudio } from "../lib/store";
import { Shelf } from "./Documents";
import { t } from "../lib/i18n";

const SHELF = "design/vfx";

export default function Vfx() {
  const { project } = useStudio();
  const [sending, setSending] = useState<ProjectDocument | null>(null);
  return (
    <>
      <Shelf
        title="VFX"
        folder={SHELF}
        template="vfx"
        docActions={(document, dirty) => (
          <button
            className="btn btn-primary btn-sm"
            disabled={dirty}
            title={dirty ? t("Save the concept before sending it") : undefined}
            onClick={() => setSending(document)}
          >
            {t("Create in Godot")}
          </button>
        )}
      />
      {sending && (
        <HandoffDialog
          title={t("Create in Godot")}
          eyebrow={sending.title}
          load={() => api.vfxBrief(project, sending.name)}
          rows={(brief) => [
            [t("Godot project"), <span className="mono">{brief.godot}</span>],
            [t("Deliverable"), <span className="mono">{brief.scene}</span>],
          ]}
          refusal={(brief) => (brief.godot_ready ? null : t("No project.godot in this folder"))}
          submit={(choice) => api.vfxHandoff(project, sending.name, choice)}
          sent={(session) => t("{title} handed to {title2}", { title: sending.title, title2: session.title })}
          onClose={() => setSending(null)}
        />
      )}
    </>
  );
}
