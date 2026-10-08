/**
 * The studio's 3D scene: a GLB, its shadings, its animations, Blender-style navigation.
 *
 * Shared by a world card's 3D step, the comparator and the library: a mesh is
 * seen everywhere with the same light, the same floor and the same shortcuts
 * (1/3/7 front, side, top).
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Grid, OrbitControls, PerformanceMonitor, useAnimations, useBounds, useGLTF,
} from "@react-three/drei";
import { SkeletonUtils, type OrbitControls as OrbitControlsImpl } from "three-stdlib";
import * as THREE from "three";
import type { CameraLink, Shading, Stats } from "../lib/scene";

export { SHADINGS, cameraLink, type CameraLink, type Shading, type Stats } from "../lib/scene";

/** The scene's colors come from the design system, never from a hard-coded value. */
export function token(name: string): THREE.Color {
  return new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#808080");
}

/**
 * The frame of every studio scene, tuned to spend nothing while idle.
 *
 * - `frameloop="demand"`: a frame is only rendered when something moved
 *   (navigation, clip, rotation, shading change). A still scene costs the GPU
 *   nothing, instead of 60 frames per second for nothing, and the comparator
 *   opens two. `continuous` hands back to the loop when another scene drives
 *   the camera (`CameraLink`).
 * - `powerPreference: "high-performance"`: on a dual-GPU machine, the web view
 *   takes the dedicated GPU rather than the integrated one.
 * - Pixel density follows the machine: up to 2 on a dense screen, then it
 *   steps down if the frame rate collapses (big mesh, small GPU), and back up
 *   when it recovers.
 */
export function Stage({ children, continuous = false }: {
  children: ReactNode;
  continuous?: boolean;
}) {
  const max = Math.min(window.devicePixelRatio || 1, 2);
  const [dpr, setDpr] = useState(max);
  return (
    <Canvas
      camera={{ position: [2.5, 2, 3.5], fov: 35, near: 0.01, far: 500 }}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      dpr={dpr}
      frameloop={continuous ? "always" : "demand"}
      style={{ position: "absolute", inset: 0 }}
    >
      <PerformanceMonitor
        onDecline={() => setDpr((current) => Math.max(1, current - 0.5))}
        onIncline={() => setDpr((current) => Math.min(max, current + 0.5))}
        flipflops={3}
        onFallback={() => setDpr(1)}
      />
      {children}
    </Canvas>
  );
}

/** The lights of every studio scene: a key, a back light, an ambient. */
export function Lights() {
  return (
    <>
      <ambientLight intensity={0.6} />
      <directionalLight position={[3, 6, 4]} intensity={2.2} />
      <directionalLight position={[-4, 2, -3]} intensity={0.8} />
    </>
  );
}

/** The floor: a grid fading in the distance, tinted for the background. */
export function Floor({ light }: { light: boolean }) {
  const colors = useMemo(
    () => ({
      // Opaque colors only: three.js ignores a CSS color's alpha, and a 30 %
      // hairline would become a solid line.
      cell: token(light ? "--muted" : "--surface-hover"),
      section: token("--ink-hint"),
    }),
    [light],
  );
  return (
    <Grid
      infiniteGrid
      cellSize={0.5}
      sectionSize={2.5}
      cellColor={colors.cell}
      sectionColor={colors.section}
      fadeDistance={28}
      fadeStrength={1.2}
      followCamera={false}
    />
  );
}

/** Reframes the view on the model whenever `signal` changes. */
export function Reframe({ signal }: { signal: number }) {
  const bounds = useBounds();
  const { invalidate } = useThree();
  // Mounted with the model (same `Suspense`), it also frames on arrival: with
  // on-demand rendering, the initial framing of `Bounds` stops short, since a
  // rigged mesh only has correct bounds after a first frame.
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      bounds.refresh().clip().fit();
      invalidate();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [signal, bounds, invalidate]);
  return null;
}

/**
 * The model, its shading and its animations.
 *
 * A GLB carries every animation of its entity (those the agent who rigged it
 * put there): one plays at a time. `animation` names it; missing, it is the
 * first one; `null`, none (rest pose). The original materials are kept aside:
 * changing the shading must never lose the texture.
 */
export function Model({ url, shading, skeleton, onStats, animation }: {
  url: string;
  shading: Shading;
  skeleton: boolean;
  onStats: (stats: Stats) => void;
  animation?: string | null;
}) {
  const group = useRef<THREE.Group>(null);
  // No Draco: drei would fetch its decoder from gstatic, which the shell's CSP
  // refuses and an offline studio does not have. The studio produces no Draco
  // GLB (neither its Blender scripts nor the meshes found in projects use
  // `KHR_draco_mesh_compression`). An imported GLB that does will not load: the
  // loader refuses it instead of calling the network.
  const { scene: loaded, animations } = useGLTF(url, false);
  // The loaded GLB is cached and shared by every scene opening it: a three.js
  // object has a single parent, so each scene keeps its own copy (skeleton
  // included), and can change its materials without touching the others.
  const scene = useMemo(() => SkeletonUtils.clone(loaded), [loaded]);
  const { actions, mixer } = useAnimations(animations, group);
  const originals = useRef(new Map<THREE.Mesh, THREE.Material | THREE.Material[]>());
  const playing = animation === undefined ? animations[0]?.name ?? null : animation;

  useEffect(() => {
    const action = playing ? actions[playing] : null;
    action?.reset().play();
    return () => { mixer.stopAllAction(); };
  }, [actions, mixer, playing]);

  // On-demand rendering (`Stage`): a playing animation requests the next
  // frame, a still mesh requests none.
  const animated = Boolean(playing && actions[playing]);
  useFrame(({ invalidate }) => { if (animated) invalidate(); });
  const { invalidate } = useThree();

  useEffect(() => {
    let triangles = 0;
    let vertices = 0;
    const bones = new Set<THREE.Bone>();
    const materials = new Set<THREE.Material>();
    scene.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      const geometry = mesh.geometry;
      const count = geometry.attributes.position?.count ?? 0;
      vertices += count;
      triangles += (geometry.index ? geometry.index.count : count) / 3;
      const own = originals.current.get(mesh) ?? mesh.material;
      (Array.isArray(own) ? own : [own]).forEach((material) => materials.add(material));
      if ((node as THREE.SkinnedMesh).isSkinnedMesh) {
        (node as THREE.SkinnedMesh).skeleton.bones.forEach((bone) => bones.add(bone));
      }
    });
    onStats({
      triangles: Math.round(triangles), vertices, bones: bones.size, materials: materials.size,
      animations: animations.map((clip) => clip.name),
    });
  }, [scene, animations, onStats]);

  useEffect(() => {
    const clay = new THREE.MeshStandardMaterial({ color: token("--muted"), roughness: 0.85 });
    const wire = new THREE.MeshBasicMaterial({ color: token("--accent"), wireframe: true });
    const normals = new THREE.MeshNormalMaterial();
    scene.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      if (!originals.current.has(mesh)) originals.current.set(mesh, mesh.material);
      mesh.material =
        shading === "clay" ? clay
          : shading === "wire" ? wire
            : shading === "normals" ? normals
              : originals.current.get(mesh)!;
    });
    invalidate();
    return () => {
      clay.dispose();
      wire.dispose();
      normals.dispose();
    };
  }, [scene, shading, invalidate]);

  // The skeleton helper only exists while asked for: leaving it in place,
  // hidden, would add a node to traverse every frame.
  const { scene: root } = useThree();
  useEffect(() => {
    if (!skeleton) return;
    const helpers: THREE.SkeletonHelper[] = [];
    scene.traverse((node) => {
      if ((node as THREE.SkinnedMesh).isSkinnedMesh) {
        const helper = new THREE.SkeletonHelper((node as THREE.SkinnedMesh).skeleton.bones[0] ?? node);
        (helper.material as THREE.LineBasicMaterial).depthTest = false;
        helper.renderOrder = 1;
        helpers.push(helper);
        root.add(helper);
      }
    });
    invalidate();
    return () => {
      helpers.forEach((helper) => { root.remove(helper); helper.dispose(); });
      invalidate();
    };
  }, [skeleton, scene, root, invalidate]);

  return (
    <group ref={group}>
      <primitive object={scene} />
    </group>
  );
}

/** Navigation controls and shortcuts, modelled on Blender's. */
export function Navigation({ spin, link, id = "" }: {
  spin: boolean;
  /** A camera linked to other scenes (`cameraLink`), and this scene's name. */
  link?: CameraLink;
  id?: string;
}) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera } = useThree();

  useFrame(() => {
    const current = controls.current;
    if (!link || !current) return;
    if (link.leader === id || link.leader === null) {
      link.leader = id;
      Object.assign(link.position, { x: camera.position.x, y: camera.position.y, z: camera.position.z });
      Object.assign(link.target, { x: current.target.x, y: current.target.y, z: current.target.z });
    } else {
      camera.position.set(link.position.x, link.position.y, link.position.z);
      current.target.set(link.target.x, link.target.y, link.target.z);
      camera.lookAt(current.target);
    }
  });

  useEffect(() => {
    const distance = () => camera.position.distanceTo(controls.current?.target ?? new THREE.Vector3());
    const look = (x: number, y: number, z: number) => {
      const target = controls.current?.target ?? new THREE.Vector3();
      const radius = distance();
      camera.position.set(target.x + x * radius, target.y + y * radius, target.z + z * radius);
      camera.lookAt(target);
      controls.current?.update();
    };
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest("input, textarea, select")) return;
      if (event.key === "1") look(0, 0, 1);
      if (event.key === "3") look(1, 0, 0);
      if (event.key === "7") look(0, 1, 0.001);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [camera]);

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      onStart={() => { if (link) link.leader = id; }}
      enableDamping
      dampingFactor={0.12}
      autoRotate={spin}
      autoRotateSpeed={2}
      mouseButtons={{
        LEFT: THREE.MOUSE.ROTATE,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: THREE.MOUSE.PAN,
      }}
    />
  );
}
