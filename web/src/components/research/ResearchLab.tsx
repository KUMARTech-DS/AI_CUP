"use client";

import "./research-lab.css";

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { garmentColorHex, titleCase } from "@/lib/garmentColors";

type TopItem = { value: string; count: number; share: number | null };

export type LabProfile = {
  garments: number | null;
  customers: number | null;
  suppressed: boolean;
  size_curve?: { size: string; count: number; share: number }[];
  materials?: TopItem[];
  colors?: TopItem[];
  styles?: TopItem[];
};

export type LabItem = {
  rank: number;
  commodity: string;
  silhouette: "hoodie" | "dress" | "pants" | "tee" | "generic";
  opportunity_score: number;
  zones_touched: number;
  support_quantity: number | null;
  confidence: string;
  garments: LabProfile;
};

export type LabData = {
  items: LabItem[];
  overall: LabProfile;
  privacy_k: number;
  note: string;
};

const PREVIEW_FABRICS = ["cotton", "denim", "polyester", "wool"];
const PREVIEW_COLORS = ["charcoal", "olive", "violet", "white"];

function fabricSpec(name: string): { roughness: number; metalness: number; finish: string } {
  const n = name.toLowerCase();
  if (/silk|satin/.test(n)) return { roughness: 0.25, metalness: 0.1, finish: "Smooth sheen" };
  if (/poly|nylon|synthetic|rayon|viscose|spandex|elastane/.test(n)) return { roughness: 0.35, metalness: 0.25, finish: "Synthetic · matte finish" };
  if (/denim/.test(n)) return { roughness: 0.85, metalness: 0.02, finish: "Twill weave" };
  if (/wool|knit|fleece|acrylic/.test(n)) return { roughness: 0.95, metalness: 0, finish: "Brushed knit" };
  if (/leather|suede/.test(n)) return { roughness: 0.45, metalness: 0.15, finish: "Grain finish" };
  if (/linen/.test(n)) return { roughness: 0.9, metalness: 0, finish: "Open weave" };
  return { roughness: 0.85, metalness: 0, finish: "Natural fibre" };
}

function live(p: LabProfile | null | undefined): LabProfile | null {
  return p && !p.suppressed && (p.garments || 0) > 0 ? p : null;
}

function buildGarment(silhouette: LabItem["silhouette"], mat: THREE.Material): THREE.Group {
  const g = new THREE.Group();
  const add = (geo: THREE.BufferGeometry, pos: [number, number, number], rot: [number, number, number] = [0, 0, 0]) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(...pos);
    m.rotation.set(...rot);
    g.add(m);
  };
  if (silhouette === "hoodie") {
    add(new THREE.CylinderGeometry(0.45, 0.42, 0.8, 16), [0, 0.2, 0]);
    add(new THREE.BoxGeometry(0.38, 0.25, 0.15), [0, 0.05, 0.22]);
    add(new THREE.CylinderGeometry(0.16, 0.13, 0.7, 12), [-0.45, 0.22, 0], [0, 0, Math.PI / 6]);
    add(new THREE.CylinderGeometry(0.16, 0.13, 0.7, 12), [0.45, 0.22, 0], [0, 0, -Math.PI / 6]);
    add(new THREE.SphereGeometry(0.28, 16, 12, 0, Math.PI * 2, 0, Math.PI * 0.7), [0, 0.65, -0.05], [0.2, 0, 0]);
  } else if (silhouette === "pants") {
    add(new THREE.CylinderGeometry(0.36, 0.36, 0.2, 16), [0, 0.5, 0]);
    add(new THREE.CylinderGeometry(0.17, 0.13, 0.9, 14), [-0.19, -0.05, 0]);
    add(new THREE.CylinderGeometry(0.17, 0.13, 0.9, 14), [0.19, -0.05, 0]);
    add(new THREE.BoxGeometry(0.08, 0.22, 0.18), [-0.3, 0, 0]);
    add(new THREE.BoxGeometry(0.08, 0.22, 0.18), [0.3, 0, 0]);
  } else if (silhouette === "tee") {
    add(new THREE.CylinderGeometry(0.46, 0.46, 0.85, 16), [0, 0.15, 0]);
    add(new THREE.CylinderGeometry(0.18, 0.17, 0.38, 12), [-0.44, 0.38, 0], [0, 0, Math.PI / 4]);
    add(new THREE.CylinderGeometry(0.18, 0.17, 0.38, 12), [0.44, 0.38, 0], [0, 0, -Math.PI / 4]);
    add(new THREE.TorusGeometry(0.2, 0.03, 8, 20), [0, 0.58, 0], [Math.PI / 2, 0, 0]);
  } else if (silhouette === "dress") {
    add(new THREE.CylinderGeometry(0.28, 0.3, 0.45, 16), [0, 0.45, 0]);
    add(new THREE.CylinderGeometry(0.3, 0.62, 1.0, 24, 1, true), [0, -0.27, 0]);
    add(new THREE.TorusGeometry(0.31, 0.025, 8, 24), [0, 0.22, 0], [Math.PI / 2, 0, 0]);
    add(new THREE.BoxGeometry(0.05, 0.22, 0.04), [-0.16, 0.76, 0]);
    add(new THREE.BoxGeometry(0.05, 0.22, 0.04), [0.16, 0.76, 0]);
  } else {
    for (let i = 0; i < 3; i++) {
      add(new THREE.BoxGeometry(0.9 - i * 0.06, 0.14, 0.7 - i * 0.04), [0, -0.2 + i * 0.16, 0], [0, i * 0.12, 0]);
    }
  }
  return g;
}

function disposeGroup(g: THREE.Object3D) {
  g.traverse((o) => {
    if (o instanceof THREE.Mesh) o.geometry.dispose();
  });
}

type SceneRefs = {
  garmentGroup: THREE.Group;
  current: THREE.Group | null;
  material: THREE.MeshStandardMaterial | null;
};

export function ResearchLab({ lab }: { lab: LabData | null }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<SceneRefs | null>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const [idx, setIdx] = useState(0);
  const [fabric, setFabric] = useState("");
  const [color, setColor] = useState("");
  const [wireframe, setWireframe] = useState(false);

  const items = lab?.items || [];
  const item = items[idx] || null;
  const itemProfile = live(item?.garments);
  const profile = itemProfile || live(lab?.overall);
  const profileScope = itemProfile ? "this silhouette" : profile ? "all garments" : null;
  const fabrics = profile?.materials?.length ? profile.materials : null;
  const colors = profile?.colors?.length ? profile.colors : null;
  const fabricOptions = fabrics ? fabrics.map((f) => f.value) : PREVIEW_FABRICS;
  const colorOptions = colors ? colors.map((c) => c.value) : PREVIEW_COLORS;
  const k = lab?.privacy_k ?? 30;

  useEffect(() => {
    setFabric(fabricOptions[0] || "cotton");
    setColor(colorOptions[0] || "charcoal");
  }, [idx, lab]);

  const activeFabric = fabricOptions.includes(fabric) ? fabric : fabricOptions[0] || "cotton";
  const activeColor = colorOptions.includes(color) ? color : colorOptions[0] || "charcoal";
  const spec = fabricSpec(activeFabric);
  const hex = useMemo(() => `#${new THREE.Color(garmentColorHex(activeColor)).getHexString().toUpperCase()}`, [activeColor]);
  const fabricShare = fabrics?.find((f) => f.value === activeFabric)?.share;
  const colorShare = colors?.find((c) => c.value === activeColor)?.share;
  const silhouette = item?.silhouette || "generic";

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const width = mount.clientWidth || 600;
    const height = mount.clientHeight || 400;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x090b10);
    scene.fog = new THREE.FogExp2(0x090b10, 0.08);

    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 100);
    camera.position.set(0, 1.2, 3.8);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 + 0.05;
    controls.minDistance = 2.0;
    controls.maxDistance = 6.0;
    controls.target.set(0, 0.2, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(3, 5, 4);
    scene.add(dirLight);
    const rimLight = new THREE.DirectionalLight(0x3186ff, 1.5);
    rimLight.position.set(-3, 2, -3);
    scene.add(rimLight);

    const grid = new THREE.GridHelper(10, 20, 0x3186ff, 0x1f2430);
    grid.position.y = -1.2;
    scene.add(grid);

    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(0.8, 0.9, 0.1, 32),
      new THREE.MeshStandardMaterial({ color: 0x141824, metalness: 0.2, roughness: 0.2 }),
    );
    base.position.y = -1.15;
    scene.add(base);

    const ring = new THREE.Mesh(new THREE.RingGeometry(0.65, 0.75, 32), new THREE.MeshBasicMaterial({ color: 0x3186ff, side: THREE.DoubleSide }));
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -1.09;
    scene.add(ring);

    const coneMat = new THREE.MeshBasicMaterial({
      color: 0x3186ff,
      transparent: true,
      opacity: 0.12,
      side: THREE.DoubleSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const cone = new THREE.Mesh(new THREE.ConeGeometry(1.2, 2.6, 32, 1, true), coneMat);
    cone.position.y = 0.1;
    scene.add(cone);

    const particleCount = 120;
    const positions = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i++) {
      const radius = Math.random() * 0.7;
      const theta = Math.random() * Math.PI * 2;
      positions[i * 3] = Math.cos(theta) * radius;
      positions[i * 3 + 1] = (Math.random() - 0.5) * 2.2;
      positions[i * 3 + 2] = Math.sin(theta) * radius;
    }
    const particleGeo = new THREE.BufferGeometry();
    particleGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    const particles = new THREE.Points(
      particleGeo,
      new THREE.PointsMaterial({ size: 0.025, color: 0x9dd2ff, transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending }),
    );
    scene.add(particles);

    const garmentGroup = new THREE.Group();
    scene.add(garmentGroup);
    sceneRef.current = { garmentGroup, current: null, material: null };
    setSceneReady(true);

    const clock = new THREE.Clock();
    let frame = 0;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();
      garmentGroup.rotation.y = t * 0.4;
      garmentGroup.position.y = Math.sin(t * 1.5) * 0.04;
      coneMat.opacity = 0.12 + Math.sin(t * 3) * 0.03;
      const arr = particleGeo.attributes.position.array as Float32Array;
      for (let i = 1; i < arr.length; i += 3) {
        arr[i] += 0.002;
        if (arr[i] > 1.1) arr[i] = -1.1;
      }
      particleGeo.attributes.position.needsUpdate = true;
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const ro = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    ro.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      controls.dispose();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.Points) {
          o.geometry.dispose();
          const m = o.material as THREE.Material | THREE.Material[];
          (Array.isArray(m) ? m : [m]).forEach((x) => x.dispose());
        }
      });
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      sceneRef.current = null;
      setSceneReady(false);
    };
  }, []);

  useEffect(() => {
    const s = sceneRef.current;
    if (!sceneReady || !s) return;
    if (s.current) {
      s.garmentGroup.remove(s.current);
      disposeGroup(s.current);
    }
    s.material?.dispose();
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(hex),
      roughness: spec.roughness,
      metalness: spec.metalness,
      wireframe,
    });
    const g = buildGarment(silhouette, mat);
    s.garmentGroup.add(g);
    s.current = g;
    s.material = mat;
  }, [sceneReady, silhouette, hex, spec.roughness, spec.metalness, wireframe]);

  const sizes = profile?.size_curve || [];

  return (
    <div className="panel lab-widget">
      <header className="lab-header">
        <div>
          <h2>Virtual R&amp;D laser lab</h2>
          <p>Interactive 3D projection of the top-ranked opportunities, dressed in the attributes customers actually own.</p>
        </div>
        <span className="lab-status">
          <span className={`lab-status-dot ${profile ? "" : "preview"}`} />
          {profile ? "Emitter active · live attributes" : "Emitter active · preview attributes"}
        </span>
      </header>

      <div className="lab-viewport">
        <div ref={mountRef} className="lab-canvas" />

        <div className="lab-hud lab-hud-left">
          <div className="lab-card">
            <span className="lab-label">Silhouette</span>
            <span className="lab-val">{item ? item.commodity : "No ranked opportunity"}</span>
            <span className="lab-sub">{item ? `SLH-${String(item.rank).padStart(3, "0")}-${silhouette.toUpperCase()}` : "—"}</span>
          </div>
          <div className="lab-card">
            <span className="lab-label">Fabric specification</span>
            <span className="lab-val">{titleCase(activeFabric)}</span>
            <span className="lab-sub">
              {spec.finish}
              {fabricShare != null ? ` · ${Math.round(fabricShare * 100)}% of ${profileScope}` : ""}
            </span>
            {!fabrics && <span className="lab-preview-tag">Preview — no material data ≥ k</span>}
          </div>
          <div className="lab-card">
            <span className="lab-label">Color spectrum</span>
            <span className="lab-val">{titleCase(activeColor)}</span>
            <span className="lab-sub">
              HEX {hex}
              {colorShare != null ? ` · ${Math.round(colorShare * 100)}%` : ""}
            </span>
            {!colors && <span className="lab-preview-tag">Preview — no colour data ≥ k</span>}
          </div>
        </div>

        <div className="lab-hud lab-hud-right">
          <div className="lab-card">
            <span className="lab-label">Regional demand</span>
            <span className="lab-val positive">{item ? `${Math.round(item.opportunity_score)}% index` : "—"}</span>
            <span className="lab-sub">{item ? `${item.zones_touched} zones · ${item.confidence} confidence` : "—"}</span>
          </div>
          <div className="lab-card">
            <span className="lab-label">Size ratio mix</span>
            {sizes.length ? (
              <div className="lab-ratios">
                {sizes.slice(0, 6).map((s) => (
                  <div key={s.size} className="lab-ratio">
                    <span className="lab-ratio-label">{s.size}</span>
                    <span className="lab-ratio-track">
                      <span className="lab-ratio-fill" style={{ width: `${s.share * 100}%`, display: "block" }} />
                    </span>
                    <span className="lab-ratio-pct">{Math.round(s.share * 100)}%</span>
                  </div>
                ))}
              </div>
            ) : (
              <span className="lab-sub">No size labels from ≥{k} customers yet</span>
            )}
          </div>
          <div className="lab-card">
            <span className="lab-label">Laser mesh grid</span>
            <span className="lab-val">{wireframe ? "Laser wireframe" : "Active (standard)"}</span>
            <span className="lab-sub">{wireframe ? "Mesh matrix rendered" : "Projection cone 100%"}</span>
          </div>
        </div>

        <div className="lab-hint">Drag to rotate • Scroll to zoom</div>
      </div>

      <div className="lab-deck">
        <div className="lab-group">
          <label className="lab-group-title" htmlFor="lab-silhouette">
            Garment silhouette (ranked opportunity)
          </label>
          <select id="lab-silhouette" className="lab-select" value={idx} onChange={(e) => setIdx(Number(e.target.value))} disabled={!items.length}>
            {items.length ? (
              items.map((it, i) => (
                <option key={`${it.rank}-${it.commodity}`} value={i}>
                  #{it.rank} {it.commodity}
                </option>
              ))
            ) : (
              <option value={0}>No opportunities above k</option>
            )}
          </select>
        </div>

        <div className="lab-group">
          <label className="lab-group-title" htmlFor="lab-fabric">
            Material profile{fabrics ? "" : " (preview)"}
          </label>
          <select id="lab-fabric" className="lab-select" value={activeFabric} onChange={(e) => setFabric(e.target.value)}>
            {fabricOptions.map((f) => {
              const share = fabrics?.find((x) => x.value === f)?.share;
              return (
                <option key={f} value={f}>
                  {titleCase(f)}
                  {share != null ? ` · ${Math.round(share * 100)}%` : ""}
                </option>
              );
            })}
          </select>
        </div>

        <div className="lab-group">
          <span className="lab-group-title">Colorway spectrum{colors ? "" : " (preview)"}</span>
          <div className="lab-swatches">
            {colorOptions.map((c) => (
              <button
                key={c}
                type="button"
                title={titleCase(c)}
                aria-label={titleCase(c)}
                className={`lab-swatch ${c === activeColor ? "active" : ""}`}
                style={{ backgroundColor: garmentColorHex(c) }}
                onClick={() => setColor(c)}
              />
            ))}
          </div>
        </div>

        <div className="lab-group">
          <div className="lab-switch-row">
            <span className="lab-group-title">Wireframe laser matrix</span>
            <label className="lab-switch">
              <input type="checkbox" checked={wireframe} onChange={(e) => setWireframe(e.target.checked)} />
              <span className="lab-switch-track" />
            </label>
          </div>
        </div>
      </div>

      {lab?.note && <p className="lab-note">{lab.note}</p>}
    </div>
  );
}
