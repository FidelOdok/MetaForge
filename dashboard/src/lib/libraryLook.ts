/**
 * How a library part's model is drawn (2026-11-12).
 *
 * A maker's model keeps the colours its STEP states (sRGB, as the gateway returns them); a
 * group with no stated colour is plain grey, never a made-up colour. An authored model names
 * each shape "<what> [<material>]", and the material's look comes from this table: the
 * typical appearance of that material, for drawing only.
 */

export interface Look {
  /** sRGB colour as 0xRRGGBB. */
  color: number;
  roughness: number;
  metalness: number;
  opacity: number;
}

const L = (color: number, roughness: number, metalness: number, opacity = 1): Look => ({
  color,
  roughness,
  metalness,
  opacity,
});

export const MATERIAL_LOOKS: Record<string, Look> = {
  epoxy_black: L(0x1c1c1f, 0.55, 0),
  epoxy_dark: L(0x141416, 0.35, 0),
  epoxy_blue: L(0x2c5aa8, 0.4, 0),
  tin: L(0xd2d4d6, 0.3, 0.9),
  solder: L(0xd5d8dc, 0.25, 0.9),
  gold: L(0xd9b45a, 0.28, 0.95),
  copper: L(0xc07a4a, 0.35, 0.9),
  brass: L(0xc9a54a, 0.3, 0.9),
  nickel_silver: L(0xc4c7cb, 0.32, 0.85),
  nickel_steel: L(0xb8bcc0, 0.3, 0.9),
  stainless: L(0xc9ccd0, 0.25, 0.95),
  steel_zinc: L(0xb3b7bb, 0.4, 0.85),
  steel_black: L(0x2a2b2e, 0.45, 0.6),
  aluminium: L(0xc8ccd1, 0.4, 0.9),
  alu_black: L(0x2b2d31, 0.45, 0.5),
  alu_pouch: L(0xb9bdc2, 0.35, 0.6),
  silicon: L(0x2a3140, 0.2, 0.5),
  solder_mask_green: L(0x1f5a36, 0.45, 0),
  solder_mask_black: L(0x18191b, 0.45, 0),
  solder_mask_blue: L(0x1f4f8f, 0.45, 0),
  bt_substrate: L(0x3c5a3a, 0.5, 0),
  rogers_white: L(0xece6d6, 0.5, 0),
  fpc_white: L(0xf4f4ef, 0.5, 0),
  ppa_white: L(0xf3f2ec, 0.6, 0),
  emc_white: L(0xf6f6f2, 0.5, 0),
  ceramic_white: L(0xf4f1e8, 0.7, 0),
  phosphor_yellow: L(0xf2d36b, 0.35, 0),
  silicone_clear: L(0xeef6fa, 0.08, 0, 0.45),
  silicone_white: L(0xf2f2f2, 0.5, 0),
  pmma_clear: L(0xdcecf4, 0.05, 0, 0.4),
  pc_opal: L(0xf4f4f4, 0.6, 0, 0.85),
  pc_clear: L(0xe4eef3, 0.05, 0, 0.35),
  wood_beech: L(0xd9b382, 0.75, 0),
  die_red: L(0xd23a2a, 0.3, 0.1),
  die_green: L(0x2ea84a, 0.3, 0.1),
  die_blue: L(0x2f5fd0, 0.3, 0.1),
  marking_white: L(0xe8e8e8, 0.6, 0),
  marking_grey: L(0x6a6d72, 0.6, 0),
  abs_black: L(0x232427, 0.6, 0),
  abs_grey: L(0x8d9096, 0.6, 0),
  pa_white: L(0xf0eee6, 0.6, 0),
  pa_black: L(0x202124, 0.6, 0),
  lcp_black: L(0x151517, 0.5, 0),
  pp_black: L(0x1d1d1f, 0.6, 0),
  pvc_sleeve: L(0x2d4f9e, 0.35, 0),
  pvc_black: L(0x1b1b1d, 0.5, 0),
  glass_black: L(0x0c0f14, 0.08, 0.2),
  hdpe_white: L(0xf1f0ea, 0.7, 0),
  tim_grey: L(0x8a8d90, 0.8, 0),
};

/** Plain grey: a maker's face with no stated colour, or an untagged drawn shape. */
export const PLAIN: Look = L(0xb8b8b8, 0.6, 0);

/** The material tag at the end of a drawn shape's name, e.g. "lead [tin]" -> "tin". */
export function materialTag(name: string): string | null {
  const m = /\[([a-z_]+)\]\s*$/.exec(name || '');
  return m?.[1] ?? null;
}

/** The look of one shape: its stated sRGB colour, else its material's, else plain grey. */
export function lookFor(name: string, colour?: [number, number, number] | null): Look {
  if (colour) {
    const to255 = (c: number) => Math.round(Math.min(1, Math.max(0, c)) * 255);
    return { ...PLAIN, color: (to255(colour[0]) << 16) | (to255(colour[1]) << 8) | to255(colour[2]) };
  }
  const tag = materialTag(name);
  return (tag && MATERIAL_LOOKS[tag]) || PLAIN;
}

/** A drawable shape: flat positions in mm (Y up), triangle indices and a look. */
export interface DrawShape {
  name: string;
  positions: number[];
  indices: number[];
  look: Look;
}

/** Shapes moved so their joint bounding box is centred on the origin and sits on y = 0. */
export function centred(shapes: DrawShape[]): { shapes: DrawShape[]; size: [number, number, number] } {
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const s of shapes) {
    const p = s.positions;
    for (let i = 0; i + 2 < p.length; i += 3) {
      const x = p[i] ?? 0;
      const y = p[i + 1] ?? 0;
      const z = p[i + 2] ?? 0;
      min[0] = Math.min(min[0], x);
      min[1] = Math.min(min[1], y);
      min[2] = Math.min(min[2], z);
      max[0] = Math.max(max[0], x);
      max[1] = Math.max(max[1], y);
      max[2] = Math.max(max[2], z);
    }
  }
  if (!Number.isFinite(min[0])) return { shapes, size: [0, 0, 0] };
  const shift: [number, number, number] = [(min[0] + max[0]) / 2, min[1], (min[2] + max[2]) / 2];
  const moved = shapes.map((s) => ({
    ...s,
    positions: s.positions.map((v, i) => v - shift[(i % 3) as 0 | 1 | 2]),
  }));
  return { shapes: moved, size: [max[0] - min[0], max[1] - min[1], max[2] - min[2]] };
}
