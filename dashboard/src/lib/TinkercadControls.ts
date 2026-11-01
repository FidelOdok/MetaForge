/**
 * TinkercadControls - turntable ("CAD-style") orbit controls for three.js.
 *
 * Vendored from the reference implementation supplied for FORGE-620 and typed
 * for this codebase; the behaviour and the reasoning in the comments are the
 * original author's.
 *
 * The one idea that matters:
 *
 *   The camera orientation is NOT stored as a rotation. It is stored as two
 *   scalars - azimuth and elevation - plus a distance. Every frame the camera
 *   position is rebuilt from those scalars and pointed at the target.
 *
 *   Because orientation is derived and never accumulated, roll is structurally
 *   impossible. The ground plane can never tilt. That is the whole trick.
 *
 * Mouse map matches Tinkercad: right-drag orbits, middle-drag pans, wheel
 * zooms. The left button is deliberately untouched so it stays free for picking.
 */

import * as THREE from 'three';

const DEG = Math.PI / 180;

const _offset = new THREE.Vector3();
const _right = new THREE.Vector3();
const _up = new THREE.Vector3();
const _delta = new THREE.Vector3();
const _box = new THREE.Box3();
const _sphere = new THREE.Sphere();

export type UpAxis = 'y' | 'z';

/** top | bottom | front | back | left | right | home */
export type ViewName = 'top' | 'bottom' | 'front' | 'back' | 'left' | 'right' | 'home';

export interface TinkercadControlsOptions {
  target?: THREE.Vector3;
  upAxis?: UpAxis;
  azimuth?: number;
  elevation?: number;
  radius?: number;
  minElevation?: number;
  maxElevation?: number;
  minRadius?: number;
  maxRadius?: number;
  rotateSpeed?: number;
  panSpeed?: number;
  zoomSpeed?: number;
  zoomToCursor?: boolean;
  damping?: number;
  buttons?: { orbit: number; pan: number };
}

type Camera = THREE.PerspectiveCamera | THREE.OrthographicCamera;

export class TinkercadControls {
  camera: Camera;
  domElement: HTMLElement;
  target: THREE.Vector3;
  upAxis: UpAxis;

  azimuth: number;
  elevation: number;
  radius: number;

  minElevation: number;
  maxElevation: number;
  minRadius: number;
  maxRadius: number;

  rotateSpeed: number;
  panSpeed: number;
  zoomSpeed: number;
  zoomToCursor: boolean;
  damping: number;

  buttons: { orbit: number; pan: number };
  enabled = true;

  private _goal: { az: number; el: number; r: number; target: THREE.Vector3 };
  private _mode: 'orbit' | 'pan' | null = null;
  private _lastX = 0;
  private _lastY = 0;
  private _pointerId: number | null = null;
  private _onContextMenu: (e: Event) => void;

  constructor(camera: Camera, domElement: HTMLElement, options: TinkercadControlsOptions = {}) {
    this.camera = camera;
    this.domElement = domElement;
    this.target = options.target ? options.target.clone() : new THREE.Vector3();

    // 'z' is the CAD convention Tinkercad uses: workplane is XY, height is Z.
    // 'y' is the three.js default, and what this viewer's GLB pipeline emits.
    this.upAxis = options.upAxis || 'z';

    // --- the complete orientation state ---
    this.azimuth = (options.azimuth !== undefined ? options.azimuth : -45) * DEG;
    this.elevation = (options.elevation !== undefined ? options.elevation : 35.264) * DEG;
    this.radius = options.radius !== undefined ? options.radius : 200;

    // Clamp short of +/-90. Modern three survives the pole itself - lookAt()
    // perturbs the up vector when view direction and up are parallel - but
    // CROSSING it makes cos(elevation) go negative, which swings the camera to
    // the opposite side and inverts the horizon. The clamp is what stops the
    // view tumbling there. Tinkercad clamps around +/-87 for the same reason.
    this.minElevation = (options.minElevation !== undefined ? options.minElevation : -89.5) * DEG;
    this.maxElevation = (options.maxElevation !== undefined ? options.maxElevation : 89.5) * DEG;

    this.minRadius = options.minRadius !== undefined ? options.minRadius : 0.1;
    this.maxRadius = options.maxRadius !== undefined ? options.maxRadius : 1e6;

    this.rotateSpeed = options.rotateSpeed !== undefined ? options.rotateSpeed : 1;
    this.panSpeed = options.panSpeed !== undefined ? options.panSpeed : 1;
    this.zoomSpeed = options.zoomSpeed !== undefined ? options.zoomSpeed : 1;
    this.zoomToCursor = options.zoomToCursor !== undefined ? options.zoomToCursor : true;

    // 1 = snap straight to the goal, lower = smoother. ~0.25 feels like Tinkercad.
    this.damping = options.damping !== undefined ? options.damping : 0.25;

    this.buttons = options.buttons || { orbit: 2, pan: 1 };

    // Input writes to the goal; update() eases the live values toward it.
    this._goal = {
      az: this.azimuth,
      el: this.elevation,
      r: this.radius,
      target: this.target.clone(),
    };

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onContextMenu = (e: Event) => e.preventDefault();

    domElement.addEventListener('pointerdown', this._onPointerDown);
    domElement.addEventListener('wheel', this._onWheel, { passive: false });
    // Without this, right-drag opens the browser context menu mid-orbit.
    domElement.addEventListener('contextmenu', this._onContextMenu);

    this.update(true);
  }

  // ---------------------------------------------------------------- core

  /**
   * Rebuild the camera from (azimuth, elevation, radius). Call once per frame
   * before render. Pass true to skip damping and land on the goal immediately.
   */
  update(immediate?: boolean) {
    const a = immediate ? 1 : this.damping;
    const g = this._goal;

    this.azimuth += (g.az - this.azimuth) * a;
    this.elevation += (g.el - this.elevation) * a;
    this.radius += (g.r - this.radius) * a;
    this.target.lerp(g.target, a);

    const cosEl = Math.cos(this.elevation);
    const sinEl = Math.sin(this.elevation);
    const cosAz = Math.cos(this.azimuth);
    const sinAz = Math.sin(this.azimuth);
    const r = this.radius;

    if (this.upAxis === 'z') {
      _offset.set(r * cosEl * cosAz, r * cosEl * sinAz, r * sinEl);
      this.camera.up.set(0, 0, 1);
    } else {
      _offset.set(r * cosEl * sinAz, r * sinEl, r * cosEl * cosAz);
      this.camera.up.set(0, 1, 0);
    }

    this.camera.position.copy(this.target).add(_offset);
    // up must be set BEFORE lookAt - lookAt reads it to build the basis.
    this.camera.lookAt(this.target);
    this.camera.updateMatrixWorld();
  }

  /** True while the camera is still easing toward the goal. */
  get settling(): boolean {
    const g = this._goal;
    return (
      Math.abs(g.az - this.azimuth) > 1e-5 ||
      Math.abs(g.el - this.elevation) > 1e-5 ||
      Math.abs(g.r - this.radius) > 1e-4 ||
      g.target.distanceToSquared(this.target) > 1e-8
    );
  }

  // ------------------------------------------------------------- gestures

  rotate(dx: number, dy: number) {
    // A full sweep of the viewport height is about one full turn.
    const k = ((2 * Math.PI) / (this.domElement.clientHeight || 1)) * this.rotateSpeed;
    this._goal.az -= dx * k;
    this._goal.el = THREE.MathUtils.clamp(
      this._goal.el + dy * k,
      this.minElevation,
      this.maxElevation,
    );
  }

  pan(dx: number, dy: number) {
    const k = this._worldPerPixel() * this.panSpeed;
    _right.setFromMatrixColumn(this.camera.matrixWorld, 0);
    _up.setFromMatrixColumn(this.camera.matrixWorld, 1);
    _delta
      .set(0, 0, 0)
      .addScaledVector(_right, -dx * k)
      .addScaledVector(_up, dy * k);
    this._goal.target.add(_delta);
  }

  dolly(scale: number, ndcX?: number, ndcY?: number) {
    const camera = this.camera;
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      const ortho = camera as THREE.OrthographicCamera;
      ortho.zoom = THREE.MathUtils.clamp(ortho.zoom / scale, 1e-4, 1e5);
      ortho.updateProjectionMatrix();
      return;
    }

    const before = this._goal.r;
    const after = THREE.MathUtils.clamp(before * scale, this.minRadius, this.maxRadius);
    this._goal.r = after;

    if (!this.zoomToCursor) return;

    // Slide the target so the point under the cursor stays put: how far the
    // cursor sits off-centre in world units, scaled by how much we moved in.
    const perspective = camera as THREE.PerspectiveCamera;
    const halfH = this.radius * Math.tan((perspective.fov * DEG) / 2);
    const halfW = halfH * perspective.aspect;
    const shift = 1 - after / before;

    _right.setFromMatrixColumn(this.camera.matrixWorld, 0);
    _up.setFromMatrixColumn(this.camera.matrixWorld, 1);
    _delta
      .set(0, 0, 0)
      .addScaledVector(_right, (ndcX || 0) * halfW * shift)
      .addScaledVector(_up, (ndcY || 0) * halfH * shift);
    this._goal.target.add(_delta);
  }

  // ----------------------------------------------------------- view cube

  /** top | bottom | front | back | left | right | home */
  setView(name: ViewName) {
    const views: Record<ViewName, [number, number]> = {
      top: [0, 90],
      bottom: [0, -90],
      front: [-90, 0],
      back: [90, 0],
      right: [0, 0],
      left: [180, 0],
      home: [-45, 35.264],
    };
    const v = views[name];
    if (!v) return;
    this._goal.az = v[0] * DEG;
    this._goal.el = THREE.MathUtils.clamp(v[1] * DEG, this.minElevation, this.maxElevation);
  }

  /** Fit an object or Box3 in view, keeping the current angles. */
  frame(objectOrBox: THREE.Object3D | THREE.Box3, fitRatio?: number) {
    const ratio = fitRatio !== undefined ? fitRatio : 1.4;
    if ((objectOrBox as THREE.Box3).isBox3) _box.copy(objectOrBox as THREE.Box3);
    else _box.setFromObject(objectOrBox as THREE.Object3D);
    if (_box.isEmpty()) return;
    _box.getBoundingSphere(_sphere);
    this._goal.target.copy(_sphere.center);
    const camera = this.camera;
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      const ortho = camera as THREE.OrthographicCamera;
      ortho.zoom = (ortho.top - ortho.bottom) / (2 * _sphere.radius * ratio);
      ortho.updateProjectionMatrix();
    } else {
      const fov = (camera as THREE.PerspectiveCamera).fov * DEG;
      this._goal.r = (_sphere.radius * ratio) / Math.sin(fov / 2);
    }
  }

  // -------------------------------------------------------------- events

  private _worldPerPixel(): number {
    const h = this.domElement.clientHeight || 1;
    const camera = this.camera;
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      const ortho = camera as THREE.OrthographicCamera;
      return (ortho.top - ortho.bottom) / ortho.zoom / h;
    }
    return (2 * this.radius * Math.tan(((camera as THREE.PerspectiveCamera).fov * DEG) / 2)) / h;
  }

  private _onPointerDown(e: PointerEvent) {
    if (!this.enabled || this._mode) return;
    let mode: 'orbit' | 'pan' | null = null;
    if (e.button === this.buttons.orbit) mode = 'orbit';
    else if (e.button === this.buttons.pan) mode = 'pan';
    if (!mode) return; // left button left alone for picking
    e.preventDefault();
    this._mode = mode;
    this._pointerId = e.pointerId;
    this._lastX = e.clientX;
    this._lastY = e.clientY;
    this.domElement.setPointerCapture?.(e.pointerId);
    this.domElement.addEventListener('pointermove', this._onPointerMove);
    this.domElement.addEventListener('pointerup', this._onPointerUp);
    this.domElement.addEventListener('pointercancel', this._onPointerUp);
  }

  private _onPointerMove(e: PointerEvent) {
    if (e.pointerId !== this._pointerId) return;
    const dx = e.clientX - this._lastX;
    const dy = e.clientY - this._lastY;
    this._lastX = e.clientX;
    this._lastY = e.clientY;
    if (this._mode === 'orbit') this.rotate(dx, dy);
    else if (this._mode === 'pan') this.pan(dx, dy);
  }

  private _onPointerUp(e: { pointerId: number }) {
    if (!e || e.pointerId !== this._pointerId) return;
    this._mode = null;
    this._pointerId = null;
    if (this.domElement.releasePointerCapture) {
      try {
        this.domElement.releasePointerCapture(e.pointerId);
      } catch {
        /* the pointer was already released */
      }
    }
    this.domElement.removeEventListener('pointermove', this._onPointerMove);
    this.domElement.removeEventListener('pointerup', this._onPointerUp);
    this.domElement.removeEventListener('pointercancel', this._onPointerUp);
  }

  private _onWheel(e: WheelEvent) {
    if (!this.enabled) return;
    e.preventDefault();
    const rect = this.domElement.getBoundingClientRect();
    const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
    const scale = Math.pow(0.95, -Math.sign(e.deltaY) * this.zoomSpeed);
    this.dolly(scale, ndcX, ndcY);
  }

  dispose() {
    this.domElement.removeEventListener('pointerdown', this._onPointerDown);
    this.domElement.removeEventListener('wheel', this._onWheel);
    this.domElement.removeEventListener('contextmenu', this._onContextMenu);
    if (this._pointerId !== null) this._onPointerUp({ pointerId: this._pointerId });
  }
}
