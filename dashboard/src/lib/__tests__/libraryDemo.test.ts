import { describe, expect, it } from 'vitest';
import { demosOf } from '../libraryDemo';
import type { LibraryComponent } from '../../api/endpoints/library';

const part = (extra: Partial<LibraryComponent>): LibraryComponent => ({
  id: 'x', name: 'x', title: 'x', family: 'electronic', origin: 'purchased', specs: {}, notes: '', version: 1, updated_at: 0, ...extra,
});

describe('demosOf', () => {
  it('reads the demos from the research note', () => {
    expect(demosOf(part({ notes: 'Demo parts research (watch demo), read 2026-11-14.' }))).toEqual(['Watch']);
    expect(demosOf(part({ notes: 'Demo parts research (watch, speaker demo), read 2026-11-14.' }))).toEqual(['Watch', 'Speaker']);
    expect(demosOf(part({ notes: 'Smart-lamp market research, 02-leds-and-optics.md section 1 (x)' }))).toEqual(['Lamp']);
  });
  it('knows the smartwatch parts added before the research, and leaves others out', () => {
    expect(demosOf(part({ name: 'seeed_xiao_nrf52840' }))).toEqual(['Watch']);
    expect(demosOf(part({ name: 'dowel_beech_fluted_bosch_6x30' }))).toEqual([]);
  });
});
