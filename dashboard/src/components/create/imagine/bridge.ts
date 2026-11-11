import {
  STUDIO_COLLECTIONS,
  type ImagineEvent,
  type ImagineEventKind,
  type StudioCollection,
} from '../../../types/imagine';

/**
 * The host side of the studio bridge (FORGE-822): the studio page runs in a
 * sandboxed frame with no same-origin access and talks to Create only by
 * `postMessage`. Every message carries `{fg: 1, type, ...}`. These pure
 * functions decide which messages are valid; anything else is ignored.
 * Nothing from the page is ever executed.
 */

export type PageMessage =
  | { type: 'fg:ready' }
  | { type: 'fg:write'; collection: StudioCollection; id: string; data: Record<string, unknown> }
  | { type: 'fg:height'; px: number }
  | { type: 'fg:select'; component_id: string }
  | { type: 'fg:download'; name: string; mime: string; base64: string }
  | ({ type: 'fg:view' } & StudioView);

/** Where the person was looking: kept across a new revision's page (camera and scroll). */
export interface StudioView {
  camera: [number, number, number];
  target: [number, number, number];
  moved: boolean;
  scroll: number;
}

export type HostMessage =
  | { fg: 1; type: 'fg:snapshot'; collections: Record<string, unknown> }
  | { fg: 1; type: 'fg:readonly'; value: boolean }
  | ({ fg: 1; type: 'fg:view' } & StudioView);

function vec3(x: unknown): x is [number, number, number] {
  return Array.isArray(x) && x.length === 3 && x.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) < 1e6);
}

/** Each studio collection's event kind on `POST /imagine/events`. */
export const COLLECTION_KIND: Record<StudioCollection, ImagineEventKind> = {
  choices: 'choice',
  scenarios: 'scenario',
  contextEdits: 'context_edit',
  assumptions: 'assumption_confirmed',
  requirements: 'requirement',
  ledger: 'ledger',
  screening: 'screening',
  formSeen: 'form_seen',
};

/**
 * The frame's height is kept within these bounds, whatever the page asks.
 * The page asks for the height it would like at its width (its bar, a
 * bounded 3D view and room for the panels) and scrolls inside the frame
 * beyond that, so the frame never grows to the length of the whole page.
 */
export const MIN_HEIGHT = 420;
export const MAX_HEIGHT = 960;
/** The height the frame starts at, before the page has said what it would like. */
export const START_HEIGHT = 720;
/** Downloads larger than this (as base64 text) are refused. */
export const MAX_DOWNLOAD_BASE64 = 28_000_000;
const MAX_ID = 200;

function isRecord(x: unknown): x is Record<string, unknown> {
  return !!x && typeof x === 'object' && !Array.isArray(x);
}

function shortString(x: unknown, max = MAX_ID): x is string {
  return typeof x === 'string' && x.length > 0 && x.length <= max;
}

/** A valid page message, or null for anything with the wrong shape. */
export function parsePageMessage(raw: unknown): PageMessage | null {
  if (!isRecord(raw) || raw.fg !== 1 || typeof raw.type !== 'string') return null;
  switch (raw.type) {
    case 'fg:ready':
      return { type: 'fg:ready' };
    case 'fg:write': {
      const collection = raw.collection;
      if (typeof collection !== 'string' || !(STUDIO_COLLECTIONS as readonly string[]).includes(collection)) return null;
      if (!shortString(raw.id) || !isRecord(raw.data)) return null;
      return { type: 'fg:write', collection: collection as StudioCollection, id: raw.id, data: raw.data };
    }
    case 'fg:height': {
      const px = raw.px;
      if (typeof px !== 'number' || !Number.isFinite(px) || px <= 0) return null;
      return { type: 'fg:height', px };
    }
    case 'fg:select':
      return shortString(raw.component_id) ? { type: 'fg:select', component_id: raw.component_id } : null;
    case 'fg:download': {
      const { name, mime, base64 } = raw;
      if (!shortString(name) || typeof mime !== 'string' || mime.length > 100) return null;
      if (typeof base64 !== 'string' || base64.length === 0 || base64.length > MAX_DOWNLOAD_BASE64) return null;
      if (!/^[A-Za-z0-9+/=\s]+$/.test(base64)) return null;
      return { type: 'fg:download', name, mime, base64 };
    }
    case 'fg:view': {
      const { camera, target, moved, scroll } = raw;
      if (!vec3(camera) || !vec3(target) || typeof moved !== 'boolean') return null;
      if (typeof scroll !== 'number' || !Number.isFinite(scroll)) return null;
      return { type: 'fg:view', camera, target, moved, scroll: Math.max(0, Math.round(scroll)) };
    }
    default:
      return null;
  }
}

/** The event a write becomes, with a fresh id so a retry is idempotent. */
export function writeToEvent(
  msg: Extract<PageMessage, { type: 'fg:write' }>,
  eventId: string,
): ImagineEvent {
  return {
    event_id: eventId,
    kind: COLLECTION_KIND[msg.collection],
    data: { collection: msg.collection, id: msg.id, data: msg.data },
  };
}

export function clampHeight(px: number): number {
  return Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, px)));
}

/**
 * The page's address with Create's resolved theme on it (`?theme=`), so the
 * studio matches the dashboard rather than the system theme. Addresses that
 * are not pages (`about:`, `data:`, `blob:`) are left alone.
 */
export function themedSrc(src: string, theme: 'light' | 'dark'): string {
  if (/^(about|data|blob|javascript):/i.test(src)) return src;
  const hashAt = src.indexOf('#');
  const hash = hashAt >= 0 ? src.slice(hashAt) : '';
  const base = (hashAt >= 0 ? src.slice(0, hashAt) : src).replace(/([?&])theme=[^&]*(&|$)/, (_m, lead: string, tail: string) => (tail ? lead : ''));
  const sep = base.includes('?') ? (/[?&]$/.test(base) ? '' : '&') : '?';
  return `${base}${sep}theme=${theme}${hash}`;
}

/** A file name with no path in it, safe to offer as a download. */
export function safeFileName(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? '';
  const clean = base.replace(/[^\w.\- ]+/g, '_').replace(/^\.+/, '').trim();
  return clean.slice(0, 120) || 'download';
}

/** Only a plain `type/subtype` is kept; anything else downloads as bytes. */
export function safeMime(mime: string): string {
  return /^[a-z]+\/[a-z0-9.+-]+$/i.test(mime) ? mime : 'application/octet-stream';
}

export function base64ToBlob(base64: string, mime: string): Blob {
  const bin = atob(base64.replace(/\s+/g, ''));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: safeMime(mime) });
}

/** Hand the page's file to the person as a download; it is never opened or run. */
export function triggerDownload(msg: Extract<PageMessage, { type: 'fg:download' }>): boolean {
  let blob: Blob;
  try {
    blob = base64ToBlob(msg.base64, msg.mime);
  } catch {
    return false;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = safeFileName(msg.name);
  a.rel = 'noopener';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  return true;
}
