import { describe, it, expect, afterEach, vi } from 'vitest';
import * as THREE from 'three';
import { TinkercadControls } from '../TinkercadControls';

/** A DOM element with the handful of things the controls touch. jsdom gives us
 * the listeners for free; clientHeight and getBoundingClientRect it does not. */
function domElement(width = 800, height = 600) {
  const el = document.createElement('div');
  Object.defineProperty(el, 'clientHeight', { value: height });
  Object.defineProperty(el, 'clientWidth', { value: width });
  el.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width, height, right: width, bottom: height, x: 0, y: 0 }) as DOMRect;
  el.setPointerCapture = vi.fn();
  el.releasePointerCapture = vi.fn();
  return el;
}

function makeControls(options = {}) {
  const camera = new THREE.PerspectiveCamera(45, 800 / 600, 0.1, 10000);
  const el = domElement();
  document.body.appendChild(el);
  // damping 1 so a single update() lands on the goal; the easing itself is a
  // separate concern from where the camera ends up.
  const controls = new TinkercadControls(camera, el, { upAxis: 'y', damping: 1, ...options });
  return { controls, camera, el };
}

/** The camera's right vector, in world space. For a Y-up turntable it must
 * stay level with the ground: any y component is roll. */
function rollDegrees(camera: THREE.Camera) {
  const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
  return THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(right.y, -1, 1)));
}

describe('TinkercadControls', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  describe('the invariant that makes it a turntable', () => {
    it('never rolls the camera, whatever the drag', () => {
      const { controls, camera } = makeControls();

      // Orientation is rebuilt from (azimuth, elevation) every frame rather
      // than accumulated, so roll is structurally impossible. This is the
      // whole reason for the control; the demo ships it as a live readout.
      const drags: Array<[number, number]> = [
        [120, 40],
        [-500, -260],
        [37, 900],
        [-2000, 15],
        [8, -8],
      ];
      for (const [dx, dy] of drags) {
        controls.rotate(dx, dy);
        controls.update();
        expect(Math.abs(rollDegrees(camera))).toBeLessThan(1e-6);
      }
    });

    it('keeps the up vector pinned to the chosen axis', () => {
      const { controls, camera } = makeControls();
      controls.rotate(300, 200);
      controls.update();

      expect(camera.up.toArray()).toEqual([0, 1, 0]);
    });

    it('uses Z-up when asked, for CAD-convention scenes', () => {
      const { controls, camera } = makeControls({ upAxis: 'z' });
      controls.update();

      expect(camera.up.toArray()).toEqual([0, 0, 1]);
    });
  });

  describe('rotation limits', () => {
    it('stops just short of straight overhead, instead of tumbling past it', () => {
      const { controls } = makeControls();

      // Crossing the pole makes cos(elevation) negative, which swings the
      // camera to the opposite side and inverts the horizon. Tinkercad clamps
      // for the same reason.
      controls.rotate(0, 100_000);
      controls.update();

      expect(THREE.MathUtils.radToDeg(controls.elevation)).toBeCloseTo(89.5, 3);
    });

    it('stops just short of straight underneath', () => {
      const { controls } = makeControls();
      controls.rotate(0, -100_000);
      controls.update();

      expect(THREE.MathUtils.radToDeg(controls.elevation)).toBeCloseTo(-89.5, 3);
    });

    it('lets the azimuth run round and round without limit', () => {
      const { controls } = makeControls();
      const start = controls.azimuth;

      controls.rotate(10_000, 0);
      controls.update();

      // Several full turns, never clamped.
      expect(Math.abs(controls.azimuth - start)).toBeGreaterThan(4 * Math.PI);
    });
  });

  describe('framing', () => {
    it('centres on a box and backs off far enough to hold its sphere', () => {
      const { controls, camera } = makeControls();
      const box = new THREE.Box3(new THREE.Vector3(8, 4, -4), new THREE.Vector3(12, 6, 0));

      controls.frame(box);
      controls.update();

      expect(controls.target.toArray()).toEqual([10, 5, -2]);
      // r = sphereRadius * 1.4 / sin(fov/2)
      const sphereRadius = new THREE.Vector3(2, 1, 2).length();
      const expected = (sphereRadius * 1.4) / Math.sin(THREE.MathUtils.degToRad(45) / 2);
      expect(controls.radius).toBeCloseTo(expected, 4);
      expect(camera.position.distanceTo(controls.target)).toBeCloseTo(expected, 4);
    });

    it('ignores an empty box rather than flying to nowhere', () => {
      const { controls } = makeControls();
      const before = controls.target.clone();

      controls.frame(new THREE.Box3());
      controls.update();

      expect(controls.target.toArray()).toEqual(before.toArray());
    });
  });

  describe('named views', () => {
    it.each([
      ['top', 0, 89.5],
      ['front', -90, 0],
      ['right', 0, 0],
      ['home', -45, 35.264],
    ] as const)('%s sets the expected angles', (name, az, el) => {
      const { controls } = makeControls();

      controls.setView(name);
      controls.update();

      expect(THREE.MathUtils.radToDeg(controls.azimuth)).toBeCloseTo(az, 3);
      // Top is clamped by the same limit that stops a drag at the pole.
      expect(THREE.MathUtils.radToDeg(controls.elevation)).toBeCloseTo(el, 3);
    });
  });

  describe('mouse map', () => {
    it('leaves the left button alone, so it stays free for picking', () => {
      const { controls, el } = makeControls();
      const before = controls.azimuth;

      el.dispatchEvent(new PointerEvent('pointerdown', { button: 0, pointerId: 1, bubbles: true }));
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 200, clientY: 50, bubbles: true }),
      );
      controls.update();

      expect(controls.azimuth).toBe(before);
    });

    it('orbits on the right button', () => {
      const { controls, el } = makeControls();
      const before = controls.azimuth;

      el.dispatchEvent(new PointerEvent('pointerdown', { button: 2, pointerId: 1, bubbles: true }));
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 120, clientY: 0, bubbles: true }),
      );
      controls.update();

      expect(controls.azimuth).not.toBe(before);
    });

    it('pans on the middle button, moving the target rather than the angles', () => {
      const { controls, el } = makeControls();
      const angles = [controls.azimuth, controls.elevation];

      el.dispatchEvent(new PointerEvent('pointerdown', { button: 1, pointerId: 1, bubbles: true }));
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 60, clientY: 30, bubbles: true }),
      );
      controls.update();

      expect(controls.target.length()).toBeGreaterThan(0);
      expect([controls.azimuth, controls.elevation]).toEqual(angles);
    });

    it('stops orbiting when the drag ends', () => {
      const { controls, el } = makeControls();

      el.dispatchEvent(new PointerEvent('pointerdown', { button: 2, pointerId: 1, bubbles: true }));
      el.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, bubbles: true }));
      const after = controls.azimuth;
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 400, clientY: 0, bubbles: true }),
      );
      controls.update();

      expect(controls.azimuth).toBe(after);
    });

    it('ignores every gesture while disabled, which is how drag-to-pose suppresses it', () => {
      const { controls, el } = makeControls();
      controls.enabled = false;
      const before = controls.azimuth;

      el.dispatchEvent(new PointerEvent('pointerdown', { button: 2, pointerId: 1, bubbles: true }));
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 300, clientY: 0, bubbles: true }),
      );
      controls.update();

      expect(controls.azimuth).toBe(before);
    });
  });

  describe('zoom', () => {
    it('moves the target toward the cursor, so what you point at stays put', () => {
      const { controls } = makeControls();
      controls.update(true);
      const before = controls.target.clone();

      controls.dolly(0.5, 1, 1); // zoom in, cursor at the top-right corner
      controls.update();

      expect(controls.target.distanceTo(before)).toBeGreaterThan(0);
    });

    it('leaves the target alone when cursor zoom is off', () => {
      const { controls } = makeControls({ zoomToCursor: false });
      const before = controls.target.clone();

      controls.dolly(0.5, 1, 1);
      controls.update();

      expect(controls.target.toArray()).toEqual(before.toArray());
    });

    it('will not zoom closer than its minimum', () => {
      const { controls } = makeControls({ minRadius: 10 });

      for (let i = 0; i < 50; i += 1) controls.dolly(0.5, 0, 0);
      controls.update();

      expect(controls.radius).toBeGreaterThanOrEqual(10);
    });
  });

  describe('lifecycle', () => {
    it('eases toward the goal rather than snapping, and reports while it settles', () => {
      const { controls } = makeControls({ damping: 0.25 });
      controls.rotate(100, 0);

      expect(controls.settling).toBe(true);
      const first = controls.azimuth;
      controls.update();
      // A quarter of the way, not all of it.
      expect(controls.azimuth).not.toBe(first);
      expect(controls.settling).toBe(true);

      for (let i = 0; i < 200; i += 1) controls.update();
      expect(controls.settling).toBe(false);
    });

    it('stops listening once disposed', () => {
      const { controls, el } = makeControls();
      const before = controls.azimuth;

      controls.dispose();
      el.dispatchEvent(new PointerEvent('pointerdown', { button: 2, pointerId: 1, bubbles: true }));
      el.dispatchEvent(
        new PointerEvent('pointermove', { pointerId: 1, clientX: 300, clientY: 0, bubbles: true }),
      );
      controls.update();

      expect(controls.azimuth).toBe(before);
    });
  });
});
