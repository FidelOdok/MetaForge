import { describe, expect, it } from 'vitest';
import { categoryOf } from '../libraryCategory';
import type { LibraryComponent } from '../../api/endpoints/library';

function part(extra: Partial<LibraryComponent>): LibraryComponent {
  return { id: 'x', name: 'x', title: 'x', family: 'electronic', origin: 'purchased', specs: {}, notes: '', version: 1, updated_at: 0, ...extra };
}
const from = (file: string, section: number) =>
  `Smart-lamp market research, ${file} section ${section} (heading), read 2026-11-12.`;

describe('categoryOf', () => {
  it('groups a researched part by the research section it came from', () => {
    expect(categoryOf(part({ notes: from('01-power-and-drivers.md', 9) }))).toBe('Power');
    expect(categoryOf(part({ notes: from('02-leds-and-optics.md', 8) }))).toBe('Lighting');
    expect(categoryOf(part({ notes: from('02-leds-and-optics.md', 10) }))).toBe('Mechanical');
    expect(categoryOf(part({ notes: from('03-mcu-and-radio.md', 9) }))).toBe('Controls & display');
    expect(categoryOf(part({ notes: from('04-controls-sensors-mechanical.md', 4) }))).toBe('Sensors');
    expect(categoryOf(part({ notes: from('04-controls-sensors-mechanical.md', 7) }))).toBe('Wires & connectors');
    expect(categoryOf(part({ notes: from('04-controls-sensors-mechanical.md', 10) }))).toBe('Mechanical');
  });

  it('puts a plastic grade drawn as a sample plate under materials, whatever its section', () => {
    const code = "out.push(g.box(50, 3, 50).name('material sample [pc_opal]'));";
    expect(categoryOf(part({ notes: from('04-controls-sensors-mechanical.md', 9), form_code: code }))).toBe('Materials');
  });

  it('groups any other part by its family and name', () => {
    expect(categoryOf(part({ family: 'pin', name: 'dowel_beech_fluted_bosch_6x30' }))).toBe('Mechanical');
    expect(categoryOf(part({ family: 'battery' }))).toBe('Power');
    expect(categoryOf(part({ family: 'module', name: 'waveshare_24155_round_touch_lcd_1_28' }))).toBe('Controls & display');
    expect(categoryOf(part({ family: 'module', name: 'seeed_xiao_nrf52840' }))).toBe('Control & radio');
  });
});
