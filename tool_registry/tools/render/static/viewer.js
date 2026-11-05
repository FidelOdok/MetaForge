// MetaForge model views: renders a GLB from fixed views for render.model_views.
//
// Loaded by the adapter's local page (page.py); three.js comes from the image
// through the page's import map, never a CDN. The adapter calls
// window.renderViews(plan) and reads back one PNG data URL per view, plus an
// optional contact sheet. Camera directions in the plan are already in this
// page's Y-up frame; the bounding box returned is in CAD axes (Z up), mm.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const INK = '#1f2937';
const DIM = '#b45309';
const MARGIN = 1.22;

/** 1, 2 or 5 times a power of ten, at most `x`. */
function niceStep(x) {
  if (!(x > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const m = x / p;
  return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
}

function fmt(mm) {
  const v = Math.abs(mm) >= 100 ? mm.toFixed(0) : Math.abs(mm) >= 10 ? mm.toFixed(1) : mm.toFixed(2);
  return `${v} mm`;
}

/** The page frame's box in CAD axes: page (x, y, z) is CAD (x, -z, y). */
function cadBox(box, scale) {
  return {
    min_x: box.min.x / scale,
    max_x: box.max.x / scale,
    min_y: -box.max.z / scale,
    max_y: -box.min.z / scale,
    min_z: box.min.y / scale,
    max_z: box.max.y / scale,
  };
}

function cornersOf(box) {
  const out = [];
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) out.push(new THREE.Vector3(x, y, z));
  return out;
}

function makeCamera(view, plan, box, center, radius) {
  const aspect = plan.width / plan.height;
  const dir = new THREE.Vector3(...view.direction).normalize();
  const up = new THREE.Vector3(...view.up);
  if (view.projection === 'perspective') {
    const fov = 30;
    const cam = new THREE.PerspectiveCamera(fov, aspect, radius / 100, radius * 100);
    const fit = Math.max(1, 1 / aspect);
    const dist = (radius * MARGIN * fit) / Math.sin(THREE.MathUtils.degToRad(fov / 2));
    cam.position.copy(center).addScaledVector(dir, dist);
    cam.up.copy(up);
    cam.lookAt(center);
    cam.updateMatrixWorld(true);
    return cam;
  }
  const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, radius / 100, radius * 100);
  cam.position.copy(center).addScaledVector(dir, radius * 4);
  cam.up.copy(up);
  cam.lookAt(center);
  cam.updateMatrixWorld(true);
  // Fit the box tightly as seen from this direction, with room for the overlay.
  const inv = cam.matrixWorldInverse;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const c of cornersOf(box)) {
    const p = c.clone().applyMatrix4(inv);
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
  }
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  let halfW = Math.max((maxX - minX) / 2, radius * 0.02) * MARGIN;
  let halfH = Math.max((maxY - minY) / 2, radius * 0.02) * MARGIN;
  if (halfW / halfH > aspect) halfH = halfW / aspect;
  else halfW = halfH * aspect;
  cam.left = cx - halfW; cam.right = cx + halfW;
  cam.top = cy + halfH; cam.bottom = cy - halfH;
  cam.updateProjectionMatrix();
  return cam;
}

function screenRect(box, cam, plan) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const c of cornersOf(box)) {
    const p = c.clone().project(cam);
    const sx = ((p.x + 1) / 2) * plan.width;
    const sy = ((1 - p.y) / 2) * plan.height;
    minX = Math.min(minX, sx); maxX = Math.max(maxX, sx);
    minY = Math.min(minY, sy); maxY = Math.max(maxY, sy);
  }
  return { minX, maxX, minY, maxY };
}

function arrowLine(g, x1, y1, x2, y2) {
  g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
  const a = Math.atan2(y2 - y1, x2 - x1);
  for (const [x, y, s] of [[x1, y1, 1], [x2, y2, -1]]) {
    g.beginPath();
    g.moveTo(x, y);
    g.lineTo(x + s * 7 * Math.cos(a - 0.4), y + s * 7 * Math.sin(a - 0.4));
    g.moveTo(x, y);
    g.lineTo(x + s * 7 * Math.cos(a + 0.4), y + s * 7 * Math.sin(a + 0.4));
    g.stroke();
  }
}

function overlay(g, view, plan, cam, box, measured, dims) {
  const w = plan.width, h = plan.height;
  g.font = '600 15px sans-serif';
  g.fillStyle = INK;
  g.textBaseline = 'top';
  const title = plan.title ? `${plan.title} · ${view.label}` : view.label;
  g.fillText(`${title} (${view.projection === 'perspective' ? 'perspective' : 'orthographic'})`, 12, 10);
  let mmPerPx = null;
  if (view.projection === 'orthographic') {
    mmPerPx = (cam.right - cam.left) / w / plan.unit_scale;
    if (plan.scale_bar) {
      const len = niceStep(mmPerPx * w * 0.22);
      const px = len / mmPerPx;
      g.strokeStyle = INK; g.lineWidth = 2;
      g.beginPath();
      g.moveTo(14, h - 18); g.lineTo(14 + px, h - 18);
      g.moveTo(14, h - 24); g.lineTo(14, h - 12);
      g.moveTo(14 + px, h - 24); g.lineTo(14 + px, h - 12);
      g.stroke();
      g.font = '13px sans-serif'; g.textBaseline = 'bottom';
      g.fillText(fmt(len), 14, h - 26);
    }
  }
  const value = (axis) => (dims && dims[axis] != null ? dims[axis] : measured[`max_${axis}`] - measured[`min_${axis}`]);
  const source = dims ? dims.source || 'measured' : 'mesh bounding box';
  g.font = '13px sans-serif';
  if (view.dim_axes && view.projection === 'orthographic' && !view.section) {
    const r = screenRect(box, cam, plan);
    const [across, upward] = view.dim_axes;
    g.strokeStyle = DIM; g.fillStyle = DIM; g.lineWidth = 1.5;
    const yLine = Math.min(r.maxY + 22, h - 44);
    arrowLine(g, r.minX, yLine, r.maxX, yLine);
    g.textBaseline = 'top'; g.textAlign = 'center';
    g.fillText(`${across.toUpperCase()} ${fmt(value(across))}`, (r.minX + r.maxX) / 2, yLine + 4);
    const xLine = Math.min(r.maxX + 22, w - 16);
    arrowLine(g, xLine, r.minY, xLine, r.maxY);
    g.save();
    g.translate(xLine + 6, (r.minY + r.maxY) / 2);
    g.rotate(-Math.PI / 2);
    g.textBaseline = 'top';
    g.fillText(`${upward.toUpperCase()} ${fmt(value(upward))}`, 0, 0);
    g.restore();
    g.textAlign = 'left';
  } else if (!view.section) {
    g.fillStyle = DIM; g.textBaseline = 'bottom'; g.textAlign = 'right';
    g.fillText(`X ${fmt(value('x'))} · Y ${fmt(value('y'))} · Z ${fmt(value('z'))}`, w - 12, h - 10);
    g.textAlign = 'left';
  }
  g.fillStyle = '#6b7280'; g.textBaseline = 'top'; g.textAlign = 'right';
  g.fillText(`dimensions: ${source}`, w - 12, 12);
  g.textAlign = 'left';
  return mmPerPx;
}

function sheetOf(images, plan) {
  const s = plan.sheet;
  const canvas = document.createElement('canvas');
  canvas.width = s.width; canvas.height = s.height;
  const g = canvas.getContext('2d');
  g.fillStyle = '#ffffff';
  g.fillRect(0, 0, s.width, s.height);
  images.forEach((img, i) => {
    const col = i % s.cols, row = Math.floor(i / s.cols);
    const k = Math.min(s.cell / img.width, s.cell / img.height);
    const dw = img.width * k, dh = img.height * k;
    g.drawImage(img, col * s.cell + (s.cell - dw) / 2, row * s.cell + (s.cell - dh) / 2, dw, dh);
    g.strokeStyle = '#d1d5db'; g.lineWidth = 1;
    g.strokeRect(col * s.cell + 0.5, row * s.cell + 0.5, s.cell - 1, s.cell - 1);
  });
  return canvas.toDataURL('image/png');
}

async function renderViews(plan, modelUrl = '/model.glb') {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(plan.width, plan.height);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.localClippingEnabled = true;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(plan.background);
  if (plan.environment !== 'none') {
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  }
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9ca3af, 0.8));

  const gltf = await new GLTFLoader().loadAsync(modelUrl);
  const model = gltf.scene;
  if (plan.up_axis === 'z') model.rotation.x = -Math.PI / 2;
  model.updateMatrixWorld(true);
  let triangles = 0;
  const sectionMaterials = [];
  const meshes = [];
  model.traverse((o) => {
    if (o.isMesh) meshes.push(o);
  });
  const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x374151 });
  sectionMaterials.push(edgeMaterial);
  for (const o of meshes) {
    // Feature edges make the shape readable in flat light.
    o.add(new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 25), edgeMaterial));
    const index = o.geometry.index;
    triangles += (index ? index.count : o.geometry.attributes.position.count) / 3;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    o.material = mats.map((m) => {
      const c = (m && m.clone()) || new THREE.MeshStandardMaterial({ color: 0xc8ccd2 });
      c.side = THREE.DoubleSide;
      // Push faces back a little so the edge lines draw solid on top.
      c.polygonOffset = true;
      c.polygonOffsetFactor = 1;
      c.polygonOffsetUnits = 1;
      sectionMaterials.push(c);
      return c;
    });
    if (o.material.length === 1) o.material = o.material[0];
  }
  scene.add(model);
  model.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(model);
  if (box.isEmpty()) throw new Error('the model has no geometry to render');
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1e-6);
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.copy(center).add(new THREE.Vector3(radius * 2, radius * 3, radius * 2.5));
  scene.add(key);

  if (plan.grid) {
    const extent = Math.max(size.x, size.z) * 1.6;
    const step = niceStep(extent / 12);
    const divisions = Math.max(2, Math.ceil(extent / step));
    const grid = new THREE.GridHelper(divisions * step, divisions, 0x9ca3af, 0xd1d5db);
    grid.position.set(center.x, box.min.y - radius * 0.002, center.z);
    scene.add(grid);
  }

  const measured = cadBox(box, plan.unit_scale);
  const dims = plan.dimensions;
  const out = [];
  const images = [];
  for (const view of plan.views) {
    const cam = makeCamera(view, plan, box, center, radius);
    if (view.section) {
      const axis = new THREE.Vector3(...view.section.axis_three);
      const lo = axis.dot(box.min), hi = axis.dot(box.max);
      const at = Math.min(lo, hi) + view.section.offset * Math.abs(hi - lo);
      const plane = new THREE.Plane(new THREE.Vector3(...view.section.normal), at);
      for (const m of sectionMaterials) m.clippingPlanes = [plane];
    } else {
      for (const m of sectionMaterials) m.clippingPlanes = [];
    }
    renderer.render(scene, cam);
    const canvas = document.createElement('canvas');
    canvas.width = plan.width; canvas.height = plan.height;
    const g = canvas.getContext('2d');
    g.drawImage(renderer.domElement, 0, 0);
    const mmPerPx = overlay(g, view, plan, cam, box, measured, dims);
    images.push(canvas);
    out.push({
      name: view.name,
      label: view.label,
      projection: view.projection,
      section: view.section ? { axis: view.section.axis, offset: view.section.offset } : null,
      mm_per_px: mmPerPx,
      png: canvas.toDataURL('image/png'),
    });
  }
  const sheet = plan.sheet ? sheetOf(images, plan) : null;
  renderer.dispose();
  return { views: out, sheet, bbox_mm: measured, triangles: Math.round(triangles) };
}

window.renderViews = renderViews;
window.renderReady = true;
