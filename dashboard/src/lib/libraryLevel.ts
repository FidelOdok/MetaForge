/**
 * A library part's place on the hardware integration ladder (2026-11-14).
 *
 * L1 IP core, L2 die, L3 monolithic chip (SoC, MCU, ASIC), L4 multi-die package (SiP),
 * L5 module (SoM: a chip with its support parts on a small board you mount), L6 board
 * (PCB/PCBA), L7 unit, L8 system of systems. A library holds what is bought, so only L3 to
 * L6 occur, plus parts that are not on the ladder: discrete parts (an LED, a MOSFET, a cell,
 * a crystal) and mechanical parts, wires and materials.
 *
 * Derived from the part's family, name and package text for browsing; nothing else relies
 * on it.
 */
import type { LibraryComponent } from '../api/endpoints/library';
import { categoryOf } from './libraryCategory';

export const LEVELS = ['L6 Board', 'L5 Module', 'L4 SiP', 'L3 Chip', 'Discrete', 'Off the ladder'] as const;
export type Level = (typeof LEVELS)[number];

/** What each level means, for a tooltip. */
export const LEVEL_HELP: Record<Level, string> = {
  'L6 Board': 'A whole board: a development kit or reference board',
  'L5 Module': 'A chip with its support parts on a small board you mount on yours (SoM)',
  'L4 SiP': 'Several dies sealed in one package by the chip maker (system in package)',
  'L3 Chip': 'One packaged integrated circuit: an SoC, MCU, driver or sensor chip',
  Discrete: 'A single discrete part: an LED, a MOSFET, a diode, a cell, a crystal',
  'Off the ladder': 'Mechanical parts, wires and materials',
};

const BOARD = /(^|[_\s-])(dk|devkit[a-z]*|dev[_\s-]?kit|launchpad|lp[_\s-]em|explorer[_\s-]kit|evk|evaluation[_\s-]board)([_\s-]|$)/i;
// "SiP" in mixed case or the words: an upper-case "SIP" is a single in-line package.
const SIP = /(\bSiP\b|system[_\s-]in[_\s-]package|\bnrf91\d\d)/;
const ASSEMBLY = /\b(module|board|breakout|kit|stemma|grove|shield|carrier)\b/i;
const LED_ASSEMBLY = /\b(strips?|lightstrips?|linear modules?|light engines?|LED modules?|tape)\b/i;
const DISCRETE_WORDS = /\b(MOSFETs?|diodes?|TVS|fuses?|varistors?|MOV|thermistors?|NTC|crystals?|(?<!MEMS-)resonators?|rectifiers?|cells?|supercapacitors?)\b/i;
const IC_PACKAGE =
  /\b(V?QFN|W?QFN|[WUVX]?DFN|WSON|SON|SOIC|SOP|ESOP|MSOP|VSSOP|PSOP|TSSOP|QSOP|SSOP|LQFP|TQFP|QFP|BGA|WLCSP|WLBGA|DSBGA|CSP|LGA|aQFN|SOT-?23-?[5-8]|SOT-?563|TSOT|DIP-?8|SO-?8|SOT25|SOT26)\b/i;
const IC_WORDS = /\b(IC|SoC|MCU|microcontroller|driver IC|driver|controller|transceiver|charger|fuel gauge|regulator|converter|amplifier|codec|RTC|real-time clock|sensor IC|ADC|DAC)\b/i;

export function levelOf(part: LibraryComponent): Level {
  const category = categoryOf(part);
  if (category === 'Mechanical' || category === 'Materials') return 'Off the ladder';
  const text = `${part.name} ${part.title} ${part.mpn ?? ''}`;
  if (category === 'Wires & connectors') return 'Off the ladder';
  if (SIP.test(text)) return 'L4 SiP';
  if (part.family === 'module') {
    if (BOARD.test(part.name) || BOARD.test(part.title)) return 'L6 Board';
    // filed as a module, but the title names one chip (a driver IC) and no board
    if (IC_WORDS.test(part.title) && !ASSEMBLY.test(part.title)) return 'L3 Chip';
    return 'L5 Module';
  }
  if (BOARD.test(part.name)) return 'L6 Board';
  if (part.family === 'battery' || part.family === 'motor') return 'Discrete';
  if (LED_ASSEMBLY.test(part.title) && !/\blens\b/i.test(part.title)) return 'L5 Module';
  // a radar sensor is sold as a small antenna board
  if (part.family === 'electronic' && /\bradar\b/i.test(part.title)) return 'L5 Module';
  if (part.family !== 'electronic') return 'Off the ladder';
  // a MOSFET or a diode is discrete even in a QFN-like package, unless it is named a driver
  if (DISCRETE_WORDS.test(part.title) && !IC_WORDS.test(part.title)) return 'Discrete';
  // light, proximity and motion sensors, MEMS microphones and IR receivers are packaged ICs
  if (categoryOf(part) === 'Sensors' || /\b(microphone|MEMS|IR (remote )?receiver)\b/i.test(part.title)) return 'L3 Chip';
  const packageText = `${part.notes} ${part.form_code ?? ''}`;
  if (IC_PACKAGE.test(packageText) || IC_WORDS.test(text)) return 'L3 Chip';
  return 'Discrete';
}
