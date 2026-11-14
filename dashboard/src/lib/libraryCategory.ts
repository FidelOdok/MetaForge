/**
 * What a library part is for, as the Library page groups it (2026-11-14).
 *
 * A part the smart-lamp research added carries the research file and section it came from in
 * its notes ("02-leds-and-optics.md section 8 (...)"); that section decides its group. Any
 * other part is grouped by its family and name. The groups are for finding parts, not a
 * classification anything else relies on.
 */
import type { LibraryComponent } from '../api/endpoints/library';

export const CATEGORIES = [
  'Lighting',
  'Power',
  'Control & radio',
  'Sensors',
  'Controls & display',
  'Audio',
  'Wires & connectors',
  'Mechanical',
  'Materials',
  'Other',
] as const;
export type Category = (typeof CATEGORIES)[number];

/** Research file number -> section number -> group. */
const BY_SECTION: Record<string, Record<number, Category>> = {
  // 01 power and drivers: every section is power (drivers, batteries, protection, Qi)
  '01': { 1: 'Power', 2: 'Power', 3: 'Power', 4: 'Power', 5: 'Power', 6: 'Power', 7: 'Power', 8: 'Power', 9: 'Power', 10: 'Power' },
  // 02 LEDs and optics; section 10 is metal-core boards, thermal interface and heatsinks
  '02': { 1: 'Lighting', 2: 'Lighting', 3: 'Lighting', 4: 'Lighting', 5: 'Lighting', 6: 'Lighting', 7: 'Lighting', 8: 'Lighting', 9: 'Lighting', 10: 'Mechanical' },
  // 03 MCU and radio; 8 audio and 9 displays are what a person hears and sees
  '03': { 1: 'Control & radio', 2: 'Control & radio', 3: 'Control & radio', 4: 'Control & radio', 5: 'Control & radio', 6: 'Control & radio', 7: 'Control & radio', 8: 'Audio', 9: 'Controls & display', 10: 'Control & radio' },
  // 04 controls, sensors, mechanical
  '04': { 1: 'Controls & display', 2: 'Controls & display', 3: 'Sensors', 4: 'Sensors', 5: 'Sensors', 6: 'Sensors', 7: 'Wires & connectors', 8: 'Mechanical', 9: 'Mechanical', 10: 'Mechanical' },
};

const SECTION = /(0\d)-[a-z-]+\.md section (\d+)/;
const MECHANICAL_FAMILIES = new Set([
  'screw', 'nut', 'washer', 'bearing', 'bushing', 'standoff', 'pin', 'retaining_ring', 'shaft_collar',
  'linear_rail', 'linear_bearing', 'profile_extrusion', 'hinge', 'slide', 'gear', 'pulley', 'belt', 'spring',
]);

export function categoryOf(part: LibraryComponent): Category {
  // A moulding resin or sheet grade is drawn as a material sample plate.
  if (/material sample \[/.test(part.form_code || '')) return 'Materials';
  const m = SECTION.exec(part.notes || '');
  if (m) {
    const file = m[1] ?? '';
    const found = BY_SECTION[file]?.[Number(m[2])];
    if (found) return found;
  }
  const text = `${part.name} ${part.title}`.toLowerCase();
  if (/speaker|woofer|tweeter|passive radiator|amplifier|class-d|codec|\bdac\b|microphone|mic array|audio|grille/.test(text)) return 'Audio';
  if (MECHANICAL_FAMILIES.has(part.family)) return 'Mechanical';
  if (part.family === 'battery') return 'Power';
  if (/display|lcd|oled|tact|button|switch|haptic|vibration|buzzer/.test(text)) return 'Controls & display';
  if (/strap|spring bar|o-ring|gasket|cover glass|sapphire|foam|screw|insert/.test(text)) return 'Mechanical';
  if (/pogo|connector|wire|cable|terminal|charging cradle/.test(text)) return 'Wires & connectors';
  if (/charger|pmic|power management|regulator|fuel gauge/.test(text)) return 'Power';
  if (/imu|accelerometer|gyro|heart|spo2|pressure|barometer|sensor/.test(text)) return 'Sensors';
  if (part.family === 'motor' || part.family === 'actuator') return 'Mechanical';
  if (/antenna|gnss|gps|ble|wi-?fi|zigbee|thread|nrf|esp32|mcu|xiao/.test(text)) return 'Control & radio';
  if (part.family === 'module' || part.family === 'electronic') return 'Control & radio';
  return 'Other';
}
