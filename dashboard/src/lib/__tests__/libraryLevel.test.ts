import { describe, expect, it } from 'vitest';
import { levelOf } from '../libraryLevel';
import type { LibraryComponent } from '../../api/endpoints/library';

function part(extra: Partial<LibraryComponent>): LibraryComponent {
  return { id: 'x', name: 'x', title: 'x', family: 'electronic', origin: 'purchased', specs: {}, notes: '', version: 1, updated_at: 0, ...extra };
}
const section = (file: string, n: number) => `Smart-lamp market research, ${file} section ${n} (heading), read 2026-11-12.`;

describe('levelOf', () => {
  it('puts a development kit on the board rung and a radio module on the module rung', () => {
    expect(levelOf(part({ family: 'module', name: 'nordic_nrf54l15_dk', title: 'Nordic nRF54L15 DK' }))).toBe('L6 Board');
    expect(levelOf(part({ family: 'module', name: 'espressif_esp32_c6_mini_1_n4', title: 'Espressif ESP32-C6-MINI-1: Wi-Fi 6 module' }))).toBe('L5 Module');
  });

  it('reads a chip from its package or from what it is named', () => {
    expect(levelOf(part({ title: 'Nordic nRF52832: BLE SoC', notes: 'QFN48 6x6' }))).toBe('L3 Chip');
    expect(levelOf(part({ family: 'module', title: 'Maxim MAX7219: 8-digit LED matrix driver' }))).toBe('L3 Chip');
  });

  it('keeps a MOSFET discrete even in a QFN-like package, and an LED discrete', () => {
    expect(levelOf(part({ title: 'AOS AON7400A: N-channel MOSFET', notes: 'DFN 3x3' }))).toBe('Discrete');
    expect(levelOf(part({ title: 'Cree J Series 2835: mid-power white', notes: section('02-leds-and-optics.md', 1) }))).toBe('Discrete');
  });

  it('reads "SIP" (single in-line) as no system-in-package, but "SiP" as one', () => {
    expect(levelOf(part({ family: 'module', title: 'PBO-3-S5: 3 W SIP open-frame AC-DC module' }))).toBe('L5 Module');
    expect(levelOf(part({ title: 'Nordic nRF9160: LTE-M SiP' }))).toBe('L4 SiP');
  });

  it('treats an LED strip as a module, and mechanical parts and wires as off the ladder', () => {
    expect(levelOf(part({ family: 'other', title: 'Adafruit NeoPixel strip, 60 LED/m' }))).toBe('L5 Module');
    expect(levelOf(part({ family: 'pin', name: 'dowel_beech' }))).toBe('Off the ladder');
    expect(levelOf(part({ notes: section('04-controls-sensors-mechanical.md', 7), title: 'Southwire SPT-2 lamp cord' }))).toBe('Off the ladder');
  });

  it('counts sensor parts and MEMS microphones as chips, and a radar board as a module', () => {
    expect(levelOf(part({ notes: section('04-controls-sensors-mechanical.md', 3), title: 'Vishay VEML7700: ambient light sensor' }))).toBe('L3 Chip');
    expect(levelOf(part({ title: 'Infineon IM69D130: PDM MEMS microphone' }))).toBe('L3 Chip');
    expect(levelOf(part({ notes: section('04-controls-sensors-mechanical.md', 4), title: 'Hi-Link HLK-LD2450: 24 GHz radar' }))).toBe('L5 Module');
  });
});
