/**
 * A mesh in a frame: the studio scene reduced to what it takes to judge.
 *
 * The library shows a `.glb` in it instead of its extension; the comparator
 * puts two meshes under one camera (`link`), or one over the other (`ghost`,
 * drawn as wireframe on top). Loaded on demand: three.js only weighs on the
 * pages that show 3D.
 */

import { Suspense } from "react";
import { Bounds } from "@react-three/drei";
import {
  Floor, Lights, Model, Navigation, Reframe, Stage, type CameraLink, type Shading, type Stats,
} from "./Scene3D";

export default function MeshPreview({
  url, ghost, shading = "texture", grid = true, light = false, spin = false, frame = 0,
  link, id, onStats, onGhostStats,
}: {
  url: string;
  /** A second mesh, as wireframe, in the same scene. */
  ghost?: string | null;
  shading?: Shading;
  grid?: boolean;
  light?: boolean;
  spin?: boolean;
  /** A counter: each change reframes the view. */
  frame?: number;
  link?: CameraLink;
  id?: string;
  onStats?: (stats: Stats) => void;
  onGhostStats?: (stats: Stats) => void;
}) {
  return (
    // A linked camera follows another scene's: it must render every frame,
    // since nothing in its own scene tells it the other one moved.
    <Stage continuous={Boolean(link)}>
      <Lights />
      {grid && <Floor light={light} />}
      <Suspense fallback={null}>
        <Bounds fit clip observe margin={1.3}>
          <Model key={url} url={url} shading={shading} skeleton={false} onStats={onStats ?? ignore} />
          {ghost && (
            <Model key={`ghost:${ghost}`} url={ghost} shading="wire" skeleton={false}
                   onStats={onGhostStats ?? ignore} />
          )}
          <Reframe signal={frame} />
        </Bounds>
      </Suspense>
      <Navigation spin={spin} link={link} id={id} />
    </Stage>
  );
}

function ignore() {
  /* A scene whose measurements nobody reads. */
}
