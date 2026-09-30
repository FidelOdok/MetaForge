/** FORGE-250: urdf-loader ships `URDFDragControls`/`PointerURDFDragControls`
 * as a real published file (package.json's `files` includes `src/*`) but
 * only re-exports `URDFLoader.js` from its package root -- there is no
 * `.d.ts` alongside this specific deep import, so TypeScript needs a manual
 * ambient declaration to type-check `import ... from 'urdf-loader/src/URDFDragControls.js'`.
 * Kept intentionally narrow -- only the members this codebase actually uses. */
declare module 'urdf-loader/src/URDFDragControls.js' {
  import type { Object3D, Camera } from 'three';
  import type { URDFJoint } from 'urdf-loader';

  export class URDFDragControls {
    constructor(scene: Object3D);
    enabled: boolean;
    hovered: URDFJoint | null;
    manipulating: URDFJoint | null;
    update(): void;
    updateJoint(joint: URDFJoint, angle: number): void;
    onDragStart(joint: URDFJoint): void;
    onDragEnd(joint: URDFJoint): void;
    onHover(joint: URDFJoint): void;
    onUnhover(joint: URDFJoint): void;
  }

  export class PointerURDFDragControls extends URDFDragControls {
    constructor(scene: Object3D, camera: Camera, domElement: HTMLElement);
    dispose(): void;
  }
}
