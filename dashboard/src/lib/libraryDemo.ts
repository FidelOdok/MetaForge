/**
 * Which of the three demo products a library part was gathered for (2026-11-14): the smart
 * watch, the smart lamp or the smart speaker. Read from the research note each part carries;
 * a part can serve more than one demo. For browsing only.
 */
import type { LibraryComponent } from '../api/endpoints/library';

export const DEMOS = ['Watch', 'Lamp', 'Speaker'] as const;
export type Demo = (typeof DEMOS)[number];

/** The smartwatch parts added before the demo research, by name. */
const EARLY_WATCH = /^(seeed_xiao_nrf52840|waveshare_24155|ublox_max_m10s|johanson_1575|ti_drv2605l|adafruit_2305|adafruit_1201|adafruit_1317|alps_skrtlae010|iso4762_m1_6x3|iso4762_m2x4)/;

export function demosOf(part: LibraryComponent): Demo[] {
  const notes = part.notes || '';
  const out: Demo[] = [];
  const research = /Demo parts research \(([^)]*) demo\)/.exec(notes)?.[1] ?? '';
  if (/watch/i.test(research) || EARLY_WATCH.test(part.name)) out.push('Watch');
  if (notes.startsWith('Smart-lamp market research') || /lamp/i.test(research)) out.push('Lamp');
  if (/speaker/i.test(research)) out.push('Speaker');
  return out;
}
