import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { newId, postImagineEvent } from '../../../api/endpoints/imagine';
import { useComponentSelection } from '../../../store/component-selection-store';
import { useThemeStore } from '../../../store/theme-store';
import {
  MIN_HEIGHT,
  START_HEIGHT,
  clampHeight,
  onlyFormChanged,
  parsePageMessage,
  revisedSrc,
  themedSrc,
  triggerDownload,
  writeToEvent,
  type HostMessage,
  type StudioView,
} from './bridge';

/** A new revision that never says it is ready replaces the old one anyway after this. */
const SWAP_TIMEOUT_MS = 20_000;
/** A form pushed into the page that is not confirmed by then is loaded as a new page instead. */
const APPLY_TIMEOUT_MS = 5_000;

interface StudioFrameProps {
  projectId: string;
  /** The engine page, already resolved for the browser. */
  src: string;
  /** The twin-backed state of each collection, sent as `fg:snapshot`. */
  collections: Record<string, unknown>;
  /** After handover the studio is read only: the page is told, and writes are not forwarded. */
  readOnly: boolean;
  /** The studio revision and its config. A change to the form alone is pushed into the page. */
  revision?: number | null;
  config?: Record<string, unknown> | null;
  /** Puts a request from the page (its next step) in the conversation, for the person to send. */
  onAsk?: (text: string) => void;
}

/**
 * The studio page, hosted in a sandboxed frame (scripts only, no same
 * origin, so it can reach neither this page nor its storage).
 *
 * The host accepts a message only from this frame's own window, with the
 * opaque origin a sandboxed frame has, and only in one of the contract's
 * shapes. Writes go to the gateway as Imagine events; a selected part is
 * highlighted wherever the 3D view shows it; a download is handed to the
 * person as a file. Nothing the page sends is ever executed here.
 *
 * A new revision (a new address) loads in a second, hidden frame behind the
 * one on screen. When it is ready it is given the person's camera and scroll
 * and takes the old one's place, so a regeneration neither blanks the studio
 * nor throws the view away.
 *
 * A revision that changes only the authored form (sizes, parts, makes,
 * behaviours) is not loaded at all: the form is pushed into the running page,
 * which redraws in place and keeps everything else as it was. If the page
 * cannot apply it, the new revision loads behind as above.
 *
 * The frame takes the stage's full width. Its height is the one the page
 * asks for, within bounds, and never more than the window shows (CSS); the
 * page scrolls inside it beyond that. The page is given Create's theme.
 */
export function StudioFrame({ projectId, src, collections, readOnly, revision, config, onAsk }: StudioFrameProps) {
  const [height, setHeight] = useState(START_HEIGHT);
  // The frame never runs past the window's bottom edge: its room is the window's height less
  // where the frame starts on the page (header, stages and tabs above it), measured, not assumed.
  const wrap = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return undefined;
    const measure = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      setRoom(Math.max(MIN_HEIGHT, Math.round(window.innerHeight - top - 16)));
    };
    measure();
    window.addEventListener('resize', measure);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    ro?.observe(document.body);
    return () => {
      window.removeEventListener('resize', measure);
      ro?.disconnect();
    };
  }, []);
  const shownHeight = room === null ? height : Math.min(height, room);
  const theme = useThemeStore((s) => s.resolvedTheme);
  // The revision in the address only forces a load; a form pushed in place leaves it alone.
  const [addrRev, setAddrRev] = useState(revision);
  const frameSrc = useMemo(() => revisedSrc(themedSrc(src, theme), addrRev), [src, theme, addrRev]);
  const pageRev = useRef(revision);
  const pageConfig = useRef(config ?? null);
  const applying = useRef<{ revision: number; config: Record<string, unknown>; timer: number } | null>(null);
  const [shown, setShown] = useState(frameSrc);
  const [pending, setPending] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const select = useComponentSelection((s) => s.select);
  const frames = useRef(new Map<string, HTMLIFrameElement>());
  const view = useRef<StudioView | null>(null);
  // The latest values, read by the listener without re-subscribing.
  const state = useRef({ collections, readOnly, shown, pending, onAsk });
  useEffect(() => {
    state.current = { collections, readOnly, shown, pending, onAsk };
  }, [collections, readOnly, shown, pending, onAsk]);

  // A new address loads behind the current page; going back to it cancels the load.
  useEffect(() => {
    setPending(frameSrc === state.current.shown ? null : frameSrc);
  }, [frameSrc]);

  const swap = useCallback((next: string) => {
    state.current = { ...state.current, shown: next, pending: null };
    setShown(next);
    setPending(null);
  }, []);

  useEffect(() => {
    if (!pending) return;
    const t = window.setTimeout(() => swap(pending), SWAP_TIMEOUT_MS);
    return () => window.clearTimeout(t);
  }, [pending, swap]);

  const loadRevision = useCallback((rev: number, cfg: Record<string, unknown> | null) => {
    pageRev.current = rev;
    pageConfig.current = cfg;
    setAddrRev(rev);
  }, []);

  const sendTo = useCallback((src: string | null, msg: HostMessage) => {
    // A sandboxed frame has an opaque origin, so there is no origin to target.
    if (src) frames.current.get(src)?.contentWindow?.postMessage(msg, '*');
  }, []);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== 'null') return;
      let from: string | null = null;
      frames.current.forEach((el, key) => {
        if (el.contentWindow && e.source === el.contentWindow) from = key;
      });
      if (from === null) return;
      const msg = parsePageMessage(e.data);
      if (!msg) return;
      const { shown: current, pending: next } = state.current;
      if (msg.type === 'fg:applied') {
        const a = applying.current;
        if (from !== current || !a || msg.revision !== a.revision) return;
        window.clearTimeout(a.timer);
        applying.current = null;
        if (msg.ok) {
          pageRev.current = a.revision;
          pageConfig.current = a.config;
        } else {
          loadRevision(a.revision, a.config);
        }
        return;
      }
      if (msg.type === 'fg:ready') {
        sendTo(from, { fg: 1, type: 'fg:snapshot', collections: state.current.collections });
        if (state.current.readOnly) sendTo(from, { fg: 1, type: 'fg:readonly', value: true });
        if (view.current) sendTo(from, { fg: 1, type: 'fg:view', ...view.current });
        if (from === next) swap(from);
        setReady(true);
        return;
      }
      // Everything else counts only from the page on screen.
      if (from !== current) return;
      switch (msg.type) {
        case 'fg:write':
          if (state.current.readOnly) return;
          postImagineEvent(projectId, writeToEvent(msg, newId('evt'))).then(
            () => setSaveError(null),
            () => setSaveError('A change in the studio could not be saved. Try it again.'),
          );
          return;
        case 'fg:height':
          setHeight(clampHeight(msg.px));
          return;
        case 'fg:select':
          select(projectId, { componentId: msg.component_id, from: 'create' });
          return;
        case 'fg:download':
          triggerDownload(msg);
          return;
        case 'fg:ask':
          state.current.onAsk?.(msg.text);
          return;
        case 'fg:view':
          view.current = { camera: msg.camera, target: msg.target, moved: msg.moved, scroll: msg.scroll, ui: msg.ui };
          return;
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [projectId, select, sendTo, swap, loadRevision]);

  // A new revision: push a form-only change into the page, or load the revision behind it.
  useEffect(() => {
    if (revision === undefined || revision === null || revision === pageRev.current) return;
    const next = config ?? null;
    const prev = pageConfig.current;
    if (ready && prev && next && onlyFormChanged(prev, next)) {
      if (applying.current) window.clearTimeout(applying.current.timer);
      const timer = window.setTimeout(() => {
        applying.current = null;
        loadRevision(revision, next);
      }, APPLY_TIMEOUT_MS);
      applying.current = { revision, config: next, timer };
      sendTo(state.current.shown, { fg: 1, type: 'fg:form', form: next.form as Record<string, unknown>, revision });
      return;
    }
    loadRevision(revision, next);
  }, [revision, config, ready, sendTo, loadRevision]);

  // A handover (or a rework) after the page is up is told to it at once.
  const told = useRef<boolean | null>(null);
  useEffect(() => {
    if (!ready) return;
    if (told.current === null) {
      told.current = readOnly;
      return;
    }
    if (told.current !== readOnly) {
      told.current = readOnly;
      sendTo(state.current.shown, { fg: 1, type: 'fg:readonly', value: readOnly });
    }
  }, [ready, readOnly, sendTo]);

  return (
    <div className="cv-studio" ref={wrap}>
      {[shown, pending].filter((s): s is string => !!s).map((s) => {
        const onScreen = s === shown;
        return (
          <iframe
            key={s}
            ref={(el) => {
              if (el) frames.current.set(s, el);
              else frames.current.delete(s);
            }}
            className={`cv-studio-frame${onScreen ? '' : ' is-pending'}`}
            title={onScreen ? 'Imagine studio' : 'Imagine studio (loading the new revision)'}
            src={s}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            style={{ height: shownHeight }}
            aria-hidden={onScreen ? undefined : true}
            tabIndex={onScreen ? undefined : -1}
            data-testid={onScreen ? 'studio-frame' : 'studio-frame-pending'}
          />
        );
      })}
      {saveError && (
        <p className="cv-error" role="alert">
          {saveError}
        </p>
      )}
    </div>
  );
}
