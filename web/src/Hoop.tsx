import { useEffect, useRef } from "react";
import * as THREE from "three";

/* A basketball on a loop: launch, arc, swish, net ripple, reset.
   Lazy-loaded from Landing so the signed-in tracker never pays for three.js. */

const CYCLE = 2.7;        // seconds per shot
const FLIGHT = 0.5;       // fraction of the cycle spent in the air
const DROP = 0.72;        // ...and falling through the net
const FOV = 48;
const DESIGN_ASPECT = 1.25;   // the hero panel on a wide screen
const RIM_Y = 0.55;
const RIM_R = 0.9;

const LAUNCH = new THREE.Vector3(-2.55, -2.0, 0.8);
const APEX = new THREE.Vector3(-1.35, 4.5, 0.35);          // bezier control point
const ENTRY = new THREE.Vector3(0, RIM_Y + 0.2, -0.05);
const FLOOR = new THREE.Vector3(0.12, -3.0, 0.15);

/** Net as strands hanging off the rim: 4 hoops of decreasing radius, joined vertically. */
const NET_RINGS = [
  { y: RIM_Y, r: RIM_R },
  { y: RIM_Y - 0.4, r: RIM_R * 0.87 },
  { y: RIM_Y - 0.8, r: RIM_R * 0.7 },
  { y: RIM_Y - 1.15, r: RIM_R * 0.56 },
];
const STRANDS = 14;

function netGeometry() {
  const pts: number[] = [];
  const at = (ring: { y: number; r: number }, k: number) => {
    const a = (k / STRANDS) * Math.PI * 2;
    return [Math.cos(a) * ring.r, ring.y, Math.sin(a) * ring.r];
  };
  for (let i = 0; i < NET_RINGS.length - 1; i++) {
    for (let k = 0; k < STRANDS; k++) {
      pts.push(...at(NET_RINGS[i], k), ...at(NET_RINGS[i + 1], k));       // vertical strand
      pts.push(...at(NET_RINGS[i + 1], k), ...at(NET_RINGS[i + 1], k + 1)); // ring below
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return g;
}

export default function Hoop() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100);
    camera.position.set(0.15, -0.95, 6.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    el.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffe9c4, 0x140f06, 1.1));
    const key = new THREE.DirectionalLight(0xffd89a, 2.4);
    key.position.set(4, 7, 6);
    scene.add(key);
    const rim = new THREE.PointLight(0xd8a13c, 26, 22);
    rim.position.set(-4, 1.5, 3.5);
    scene.add(rim);

    // backboard + its shooter's square
    const board = new THREE.Mesh(
      new THREE.BoxGeometry(3.2, 1.9, 0.09),
      new THREE.MeshStandardMaterial({
        color: 0xf0e6d2, roughness: 0.25, metalness: 0.05,
        transparent: true, opacity: 0.2,
      }),
    );
    board.position.set(0, 1.5, -1.05);
    scene.add(board);
    const edges = (w: number, h: number, y: number, z: number, color: number) => {
      const l = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.PlaneGeometry(w, h)),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.75 }),
      );
      l.position.set(0, y, z);
      scene.add(l);
    };
    edges(3.2, 1.9, 1.5, -1.0, 0x8f6a26);
    edges(1.25, 0.92, 1.12, -0.99, 0xd8a13c);

    const hoop = new THREE.Mesh(
      new THREE.TorusGeometry(RIM_R, 0.055, 14, 60),
      new THREE.MeshStandardMaterial({ color: 0xe2622c, roughness: 0.35, metalness: 0.6 }),
    );
    hoop.rotation.x = Math.PI / 2;
    hoop.position.set(0, RIM_Y, -0.02);
    scene.add(hoop);

    const arm = new THREE.Mesh(
      new THREE.BoxGeometry(0.16, 0.16, 0.95),
      new THREE.MeshStandardMaterial({ color: 0x3d5a46, roughness: 0.6 }),
    );
    arm.position.set(0, RIM_Y, -0.6);
    scene.add(arm);

    const netGeo = netGeometry();
    const netBase = Float32Array.from(netGeo.getAttribute("position").array);
    const net = new THREE.LineSegments(
      netGeo,
      new THREE.LineBasicMaterial({ color: 0xf0e6d2, transparent: true, opacity: 0.5 }),
    );
    net.position.z = -0.02;
    scene.add(net);

    const ball = new THREE.Group();
    ball.add(new THREE.Mesh(
      new THREE.SphereGeometry(RIM_R * 0.5, 40, 28),
      new THREE.MeshStandardMaterial({ color: 0xd9722e, roughness: 0.82 }),
    ));
    const seam = new THREE.MeshStandardMaterial({ color: 0x1a1208, roughness: 0.9 });
    for (const rot of [[0, 0, 0], [0, 0, Math.PI / 2], [Math.PI / 2, 0, 0.9], [Math.PI / 2, 0, -0.9]]) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(RIM_R * 0.5, 0.016, 8, 48), seam);
      ring.rotation.set(rot[0], rot[1], rot[2]);
      ball.add(ring);
    }
    ball.position.copy(LAUNCH);
    scene.add(ball);

    const spin = new THREE.Vector3(0.35, 1, 0.15).normalize();
    const pos = netGeo.getAttribute("position") as THREE.BufferAttribute;
    const flight = new THREE.QuadraticBezierCurve3(LAUNCH, APEX, ENTRY);

    const frame = (t: number) => {
      const u = still ? 0.4 : (t / CYCLE) % 1;

      if (u < FLIGHT) {
        flight.getPointAt(u / FLIGHT, ball.position);
        ball.visible = true;
      } else if (u < DROP) {
        const s = (u - FLIGHT) / (DROP - FLIGHT);
        ball.position.lerpVectors(ENTRY, FLOOR, s * s); // gravity, roughly
        ball.visible = true;
      } else {
        ball.visible = false;
        ball.position.copy(LAUNCH);
      }
      ball.rotateOnAxis(spin, still ? 0 : 0.045);

      // net snaps as the ball passes, then settles
      const shove = u > FLIGHT - 0.02 && u < DROP + 0.12
        ? Math.sin(((u - FLIGHT + 0.02) / (DROP + 0.14 - FLIGHT)) * Math.PI) : 0;
      for (let i = 0; i < pos.count; i++) {
        const x = netBase[i * 3], y = netBase[i * 3 + 1], z = netBase[i * 3 + 2];
        const depth = Math.min(1, Math.max(0, (RIM_Y - y) / 1.2));
        const k = 1 + shove * 0.42 * depth;
        pos.setXYZ(i, x * k, y - shove * 0.3 * depth, z * k);
      }
      pos.needsUpdate = true;

      camera.position.x = 0.15 + Math.sin(t * 0.28) * 0.28;
      camera.position.y = -0.95 + Math.sin(t * 0.19) * 0.16;
      camera.lookAt(0, 0.55, 0);
      renderer.render(scene, camera);
    };

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = el;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      // hold the horizontal field of view on narrow panels, or the hoop clips off the top
      camera.fov = camera.aspect < DESIGN_ASPECT
        ? THREE.MathUtils.radToDeg(2 * Math.atan(
            Math.tan(THREE.MathUtils.degToRad(FOV) / 2) * (DESIGN_ASPECT / camera.aspect)))
        : FOV;
      camera.updateProjectionMatrix();
      frame(still ? 0 : performance.now() / 1000);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    if (!still) renderer.setAnimationLoop((ms) => frame(ms / 1000));

    return () => {
      ro.disconnect();
      renderer.setAnimationLoop(null);
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        for (const mat of [m.material].flat()) mat?.dispose();
      });
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  return <div className="court" ref={host} aria-hidden="true" />;
}
