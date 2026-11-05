import * as THREE from 'three';
import type { AppearanceLook, AppearanceRecord, ChangeClassification } from '../api/endpoints/changeClassification';

/**
 * The appearance record applied in the viewer (3D-RC-007, SYNC-007).
 *
 * A component's look is keyed by its name, so whatever mesh shows that
 * component now (a concept mesh, a layout mesh, a mesh tessellated from exact
 * CAD) gets it. The matching rule is the gateway's
 * (`api_gateway/twin/appearance.py` `resolve`): the node it was set on, else
 * the component name, else the product's look.
 */

export function componentKey(name: string | null | undefined): string | null {
  if (!name) return null;
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return slug || null;
}

export function appearanceFor(
  record: AppearanceRecord | null | undefined,
  part: { name?: string | null; nodeId?: string | null; meshName?: string | null },
): AppearanceLook | null {
  if (!record) return null;
  if (part.nodeId) {
    const byNode = record.components.find((c) => c.node_id && c.node_id === part.nodeId);
    if (byNode) return byNode;
  }
  const keys = new Set([componentKey(part.name), componentKey(part.meshName)].filter(Boolean));
  const byName = record.components.find((c) => keys.has(c.key));
  return byName ?? record.product ?? null;
}

const ROUGHNESS: Record<string, number> = {
  matte: 0.85,
  soft_touch: 0.9,
  frosted: 0.7,
  speckled: 0.75,
  satin: 0.5,
  pearlescent: 0.35,
  gloss: 0.15,
};

const RENDER: Record<string, { metalness: number; roughness: number; colour?: string; opacity?: number }> = {
  chrome: { metalness: 1, roughness: 0.05, colour: '#e8e8e8' },
  brushed_metal: { metalness: 0.9, roughness: 0.4, colour: '#b8b8b8' },
  metallic_paint: { metalness: 0.6, roughness: 0.3 },
  anodised_metal: { metalness: 0.7, roughness: 0.35 },
  gold: { metalness: 1, roughness: 0.2, colour: '#d4af37' },
  bronze: { metalness: 0.9, roughness: 0.35, colour: '#a97142' },
  copper: { metalness: 0.95, roughness: 0.3, colour: '#b87333' },
  brass: { metalness: 0.95, roughness: 0.3, colour: '#c9a227' },
  wood: { metalness: 0, roughness: 0.75, colour: '#a0703f' },
  marble: { metalness: 0, roughness: 0.3, colour: '#e6e2dc' },
  carbon_fibre: { metalness: 0.2, roughness: 0.35, colour: '#2b2b2b' },
  clear: { metalness: 0, roughness: 0.05, opacity: 0.35 },
  translucent: { metalness: 0, roughness: 0.3, opacity: 0.6 },
};

/** A display material for a look, built from the mesh's own material so its maps survive. */
export function lookMaterial(original: THREE.Material | THREE.Material[], look: AppearanceLook): THREE.Material {
  const base = Array.isArray(original) ? original[0] : original;
  const material =
    base instanceof THREE.MeshStandardMaterial ? base.clone() : new THREE.MeshStandardMaterial({ color: '#cccccc' });
  const render = look.render_material ? RENDER[look.render_material] : undefined;
  const colour = look.colour ?? render?.colour;
  if (colour) material.color = new THREE.Color(colour);
  if (render) {
    material.metalness = render.metalness;
    material.roughness = render.roughness;
    if (render.opacity !== undefined) {
      material.transparent = true;
      material.opacity = render.opacity;
    }
  }
  if (look.finish && ROUGHNESS[look.finish] !== undefined) material.roughness = ROUGHNESS[look.finish]!;
  material.name = `appearance:${look.colour ?? ''}:${look.finish ?? ''}:${look.render_material ?? ''}`;
  return material;
}

/** The plain part of a `classified` operation message (before the bracketed detail). */
export function plainClassificationLine(message: string | null | undefined): string | null {
  if (!message) return null;
  const cut = message.indexOf(' [');
  return (cut >= 0 ? message.slice(0, cut) : message).trim() || null;
}

/** Whether Create should show the line: a recognised change, or a confident question. */
export function showsClassification(c: ChangeClassification | null | undefined): c is ChangeClassification {
  if (!c) return false;
  return !(c.change_class === 'not_a_change' && c.confidence < 0.65);
}
