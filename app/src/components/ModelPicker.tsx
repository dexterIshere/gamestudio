/**
 * Choosing an image model: a dropdown, each model with its family, its traits and its price.
 *
 * The button shows the chosen model as a line of the list; the list opens over
 * the dialog (`Select`, in a portal), and grows with the catalog
 * (`lib/catalog.ts`) without pushing the page. The price is per image: the
 * request's total belongs to its button.
 */

import type { ImageModel } from "../lib/catalog";
import { Chevron, Select, cost } from "./ui";
import { t } from "../lib/i18n";

export default function ModelPicker({ models, value, onChange }: {
  models: ImageModel[];
  value: string;
  onChange: (air: string) => void;
}) {
  const current = models.find((model) => model.air === value) ?? models[0];
  const cheapest = Math.min(...models.map((model) => model.usd));
  const line = (model: ImageModel) => (
    <ModelLine model={model} cheapest={models.length > 1 && model.usd === cheapest} />
  );
  return (
    <Select
      label={t("Model")}
      className="model-select"
      value={current?.air ?? ""}
      onChange={onChange}
      options={models.map((model) => ({ value: model.air, label: line(model) }))}
      face={current && (
        <>
          {line(current)}
          <Chevron />
        </>
      )}
    />
  );
}

function ModelLine({ model, cheapest }: { model: ImageModel; cheapest: boolean }) {
  return (
    <span className="model-line">
      <span className="model-mark" aria-hidden="true"><ModelGlyph /></span>
      <span className="model-id">
        <span className="model-name">
          {model.label}
          <span className="model-family">{model.family}</span>
        </span>
        <span className="model-traits">
          {model.traits.map((trait) => <span key={trait} className="model-trait">{trait}</span>)}
          {cheapest && <span className="model-trait is-cheap">{t("Cheapest")}</span>}
        </span>
      </span>
      <span className="model-price">
        <span className="num">{cost(model.usd)}</span>
        <span className="model-unit">{t("/ image")}</span>
      </span>
    </span>
  );
}

/** A four-pointed spark: generate an image. */
function ModelGlyph() {
  return (
    <svg viewBox="0 0 24 24">
      <path d="M12 3.5c.6 4.2 2.3 5.9 6.5 6.5-4.2.6-5.9 2.3-6.5 6.5-.6-4.2-2.3-5.9-6.5-6.5 4.2-.6 5.9-2.3 6.5-6.5z" />
      <path d="M18.5 15.5v4M16.5 17.5h4" />
    </svg>
  );
}
