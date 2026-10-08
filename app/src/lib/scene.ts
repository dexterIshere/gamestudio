/**
 * What pages know about a 3D scene without loading three.js.
 *
 * The shading modes, a mesh's measurements and the shared camera are read by
 * pages loaded at startup (the comparator): declaring them next to the scene
 * would pull three.js, drei and fiber into the entry point, while only the
 * scene itself, loaded on demand, needs them.
 */

import { t } from "./i18n";

/** What the scene measures on the loaded GLB. */
export interface Stats {
  triangles: number;
  vertices: number;
  bones: number;
  materials: number;
  /** The file's animations, under their glTF names (`walk_loop`). */
  animations: string[];
}

/**
 * The name a glTF animation takes in Godot, and whether it loops there.
 *
 * Godot strips the `_loop` (or `-loop`) suffix on import and loops the
 * animation: the studio reads names the same way (`domain/gltf.py`), so the
 * scene, the sprite sheets and the `AnimationPlayer` all mean the same `walk`.
 */
export function godotAnimation(name: string): { name: string; loop: boolean } {
  const base = /^(.+)[_-]loop$/i.exec(name)?.[1];
  return base ? { name: base, loop: true } : { name, loop: false };
}

export type Shading = "texture" | "clay" | "wire" | "normals";

export const SHADINGS: { key: Shading; label: string }[] = [
  { key: "texture", label: t("Texture") },
  { key: "clay", label: t("Clay") },
  { key: "wire", label: t("Wireframe") },
  { key: "normals", label: t("Normals") },
];

/** A point in space, without depending on three.js types. */
export interface Point {
  x: number;
  y: number;
  z: number;
}

/**
 * A camera shared by several scenes: the last one handled leads, the others
 * follow, so two meshes are judged from the same angle.
 */
export interface CameraLink {
  leader: string | null;
  position: Point;
  target: Point;
}

export const cameraLink = (): CameraLink => ({
  leader: null,
  position: { x: 0, y: 0, z: 0 },
  target: { x: 0, y: 0, z: 0 },
});
