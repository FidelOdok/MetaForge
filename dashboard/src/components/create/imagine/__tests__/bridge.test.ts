import { describe, expect, it } from 'vitest';
import {
  MAX_HEIGHT,
  MIN_HEIGHT,
  base64ToBlob,
  clampHeight,
  parsePageMessage,
  safeFileName,
  safeMime,
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
  });
});

describe('downloads and sizes', () => {
  it('keeps the frame height within bounds', () => {
    expect(clampHeight(10)).toBe(MIN_HEIGHT);
    expect(clampHeight(1e9)).toBe(MAX_HEIGHT);
    expect(clampHeight(812.4)).toBe(812);
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
