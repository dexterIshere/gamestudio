/**
 * The models the interface offers, and their unit price.
 *
 * Written once, here, for every page that offers them (the workbench's 3D
 * step, the control room, a card's generation): a model must not have two
 * prices depending on the page it is launched from.
 *
 * `MESH_MODELS` is **the exact mirror** of what the service exposes to an agent
 * (`service/meshes.py`, tool `mesh_providers`): same ids, same prices.
 * `tests/test_mesh_catalog.py` checks it, so the two lists cannot drift apart
 * and show one price while the other side charges another.
 *
 * Two paid routes live side by side: Runware AIR ids, and the ids of the Tripo
 * API called directly. The model id picks the route (the backend routes on its
 * own), but prices differ between routes. For Tripo, `usd` is the studio's
 * request with standard texture and `usdMore` the same in detailed quality
 * (texture, plus geometry for H3.1): prices computed from Tripo's price list
 * (`tripo/catalog.py`, which cites its sources), in credits at $0.01. P2's
 * quad mode costs $0.05 more.
 */

import { t } from "./i18n";

/** An image model: `family` files it under its brand, `traits` say what it is for, a word each. */
export interface ImageModel {
  air: string;
  label: string;
  family: string;
  sub: string;
  traits: string[];
  usd: number;
}

export const IMAGE_MODELS: ImageModel[] = [
  { air: "runware:101@1", label: "FLUX.1 dev", family: "FLUX", sub: t("the best render — recommended"),
    traits: [t("Quality"), t("Recommended")], usd: 0.006 },
  { air: "runware:100@1", label: "FLUX.1 schnell", family: "FLUX", sub: t("to try several ideas quickly"),
    traits: [t("Fast"), t("Draft")], usd: 0.0013 },
  { air: "runware:106@1", label: "FLUX Kontext", family: "FLUX", sub: t("to touch up a reference image"),
    traits: [t("Touch-up"), t("Faithful to the source")], usd: 0.04 },
];

/**
 * What a batch of images costs: the model's price, per megapixel beyond one
 * (the server's grid, `forge.estimate`). `null` for a model outside the catalog.
 */
export function imageCost(air: string, count: number, width: number, height: number): number | null {
  const model = IMAGE_MODELS.find((entry) => entry.air === air);
  if (!model) return null;
  return Math.round(model.usd * count * Math.max(1, (width * height) / (1024 * 1024)) * 10000) / 10000;
}

export const MESH_MODELS = [
  { air: "tripo:v3.1@0", label: "Tripo v3.1", sub: t("clean topology, made for games"), usd: 0.4 },
  { air: "tencent:hunyuan-3d@3.1-pro", label: "Hunyuan 3D 3.1 Pro", sub: t("complex organic shapes"), usd: 0.5 },
  { air: "tencent:hunyuan-3d@3.1-rapid", label: "Hunyuan 3D 3.1 Rapid", sub: t("the same family, faster and cheaper"), usd: 0.25 },
  { air: "microsoft:trellis-2@4b", label: "TRELLIS.2", sub: t("billed by compute time — the cheapest to iterate"), usd: 0.15 },
  { air: "P2-20260801", label: "Tripo P2 (direct)", sub: t("native quads up to 25,000 faces (50,000 as triangles), GLB"), usd: 1.1, usdMore: 1.2 },
  { air: "P1-20260311", label: "Tripo P1 (direct)", sub: t("strict low-poly, base mesh in ~10 s"), usd: 0.5, usdMore: 0.6 },
  { air: "tripo-v3.1", label: "Tripo H3.1 (direct)", sub: t("high fidelity, when texture matters more than topology"), usd: 0.3, usdMore: 0.6 },
];
