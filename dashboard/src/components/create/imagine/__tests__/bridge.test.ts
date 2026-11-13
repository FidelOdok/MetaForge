import { describe, expect, it } from 'vitest';
import {
  MAX_HEIGHT,
  MIN_HEIGHT,
  base64ToBlob,
  clampHeight,
  onlyFormChanged,
  parsePageMessage,
  safeFileName,
  safeMime,
  themedSrc,
  writeToEvent,
} from '../bridge';

describe('parsePageMessage', () => {
  it('accepts each message of the contract', () => {
    expect(parsePageMessage({ fg: 1, type: 'fg:ready' })).toEqual({ type: 'fg:ready' });
    expect(parsePageMessage({ fg: 1, type: 'fg:write', collection: 'choices', id: 'c1', data: { option: 'b' } })).toEqual({
      type: 'fg:write',
      collection: 'choices',
      id: 'c1',
      data: { option: 'b' },
    });
    expect(parsePageMessage({ fg: 1, type: 'fg:height', px: 900 })).toEqual({ type: 'fg:height', px: 900 });
    expect(parsePageMessage({ fg: 1, type: 'fg:select', component_id: 'CMP-arm' })).toEqual({ type: 'fg:select', component_id: 'CMP-arm' });
    expect(parsePageMessage({ fg: 1, type: 'fg:download', name: 'brief.md', mime: 'text/markdown', base64: 'aGk=' })).toMatchObject({
      type: 'fg:download',
    });
  });

  it('rejects anything with the wrong shape', () => {
    const bad: unknown[] = [
      null,
      'fg:ready',
      { type: 'fg:ready' },
      { fg: 2, type: 'fg:ready' },
      { fg: 1, type: 'fg:eval', code: 'alert(1)' },
      { fg: 1, type: 'fg:write', collection: 'secrets', id: 'x', data: {} },
      { fg: 1, type: 'fg:write', collection: 'choices', id: '', data: {} },
      { fg: 1, type: 'fg:write', collection: 'choices', id: 'x', data: 'not an object' },
      { fg: 1, type: 'fg:write', collection: 'choices', id: 'x', data: [1] },
      { fg: 1, type: 'fg:height', px: 'tall' },
      { fg: 1, type: 'fg:height', px: Number.NaN },
      { fg: 1, type: 'fg:height', px: -5 },
      { fg: 1, type: 'fg:select' },
      { fg: 1, type: 'fg:download', name: 'x', mime: 'text/plain', base64: '<script>' },
      { fg: 1, type: 'fg:download', name: '', mime: 'text/plain', base64: 'aGk=' },
    ];
    for (const raw of bad) expect(parsePageMessage(raw)).toBeNull();
  });
});

describe('writeToEvent', () => {
  it('maps each collection to its event kind and keeps the write as data', () => {
    const msg = { type: 'fg:write', collection: 'contextEdits', id: 'e1', data: { field: 'process' } } as const;
    expect(writeToEvent(msg, 'evt-1')).toEqual({
      event_id: 'evt-1',
      kind: 'context_edit',
      data: { collection: 'contextEdits', id: 'e1', data: { field: 'process' } },
    });
    expect(writeToEvent({ ...msg, collection: 'assumptions' }, 'e').kind).toBe('assumption_confirmed');
    expect(writeToEvent({ ...msg, collection: 'choices' }, 'e').kind).toBe('choice');
    expect(writeToEvent({ ...msg, collection: 'screening' }, 'e').kind).toBe('screening');
    expect(writeToEvent({ ...msg, collection: 'formSeen' }, 'e').kind).toBe('form_seen');
  });
});

describe('downloads and sizes', () => {
  it('keeps the frame height within bounds', () => {
    expect(clampHeight(10)).toBe(MIN_HEIGHT);
    expect(clampHeight(1e9)).toBe(MAX_HEIGHT);
    expect(clampHeight(812.4)).toBe(812);
  });

  it("puts Create's theme on the page's address, once", () => {
    expect(themedSrc('/api/v1/x/engine', 'dark')).toBe('/api/v1/x/engine?theme=dark');
    expect(themedSrc('/api/v1/x/engine?t=abc', 'light')).toBe('/api/v1/x/engine?t=abc&theme=light');
    expect(themedSrc('/api/v1/x/engine?theme=dark&t=abc', 'light')).toBe('/api/v1/x/engine?t=abc&theme=light');
    expect(themedSrc('https://gw.example/e?t=1#top', 'dark')).toBe('https://gw.example/e?t=1&theme=dark#top');
    expect(themedSrc('about:blank', 'dark')).toBe('about:blank');
  });

  it('strips any path from a file name and keeps only a plain mime type', () => {
    expect(safeFileName('../../etc/passwd')).toBe('passwd');
    expect(safeFileName('C:\\x\\brief<1>.md')).toBe('brief_1_.md');
    expect(safeFileName('...')).toBe('download');
    expect(safeMime('text/markdown')).toBe('text/markdown');
    expect(safeMime('text/html; charset=utf-8')).toBe('application/octet-stream');
  });

  it('decodes the bytes', async () => {
    const blob = base64ToBlob('aGVsbG8=', 'text/plain');
    expect(blob.type).toBe('text/plain');
    expect(await blob.text()).toBe('hello');
  });
});

describe('onlyFormChanged', () => {
  const authored = (h: number) => ({ template: 'authored', params: { h } });
  it('is true when an authored form is all that changed', () => {
    expect(onlyFormChanged({ engine: 'x', process: { id: 'fdm' }, form: authored(1) }, { engine: 'x', process: { id: 'fdm' }, form: authored(2) })).toBe(true);
  });
  it('is false when anything else changed, or the form is not authored, or nothing changed', () => {
    expect(onlyFormChanged({ process: { id: 'fdm' }, form: authored(1) }, { process: { id: 'cnc' }, form: authored(2) })).toBe(false);
    expect(onlyFormChanged({ form: authored(1) }, { form: authored(1), context: {} })).toBe(false);
    expect(onlyFormChanged({ form: { template: 'enclosure' } }, { form: { template: 'enclosure', x: 1 } })).toBe(false);
    expect(onlyFormChanged({ form: authored(1) }, { form: authored(1) })).toBe(false);
  });
});

describe('fg:applied', () => {
  it('is read with its revision and outcome', () => {
    expect(parsePageMessage({ fg: 1, type: 'fg:applied', revision: 3, ok: true })).toEqual({ type: 'fg:applied', revision: 3, ok: true });
    expect(parsePageMessage({ fg: 1, type: 'fg:applied', revision: 'x', ok: true })).toBeNull();
  });
});

