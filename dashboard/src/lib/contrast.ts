/**
 * WCAG 2.x relative luminance and contrast ratio for `#rgb`, `#rrggbb` and
 * `#rrggbbaa` colours. A translucent colour is first composited over the
 * background it is drawn on, so its effective contrast is what is measured.
 */

type Rgb = [number, number, number];

function parseHex(hex: string): { rgb: Rgb; alpha: number } {
  let h = hex.trim().replace(/^#/, '');
  if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join('');
  if (h.length !== 6 && h.length !== 8) throw new Error(`not a hex colour: ${hex}`);
  const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
  return { rgb: [n(0), n(2), n(4)], alpha: h.length === 8 ? n(6) / 255 : 1 };
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance([r, g, b]: Rgb): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** `fg` over `bg`, with `fg`'s own alpha times `opacity`. */
export function composite(fg: string, bg: string, opacity = 1): Rgb {
  const f = parseHex(fg);
  const b = parseHex(bg);
  const a = f.alpha * opacity;
  return [0, 1, 2].map((i) => f.rgb[i]! * a + b.rgb[i]! * (1 - a)) as Rgb;
}

/** `fg` composited over `bg`, as an opaque `#rrggbb`. */
export function compositeHex(fg: string, bg: string, opacity = 1): string {
  return `#${composite(fg, bg, opacity)
    .map((c) => Math.round(c).toString(16).padStart(2, '0'))
    .join('')}`;
}

/** The contrast ratio of `fg` (optionally faded by `opacity`) drawn on an opaque `bg`. */
export function contrastRatio(fg: string, bg: string, opacity = 1): number {
  const l1 = luminance(composite(fg, bg, opacity));
  const l2 = luminance(parseHex(bg).rgb);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
