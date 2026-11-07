import { useCallback, useEffect, useRef, useState } from 'react';
import { newId, postImagineEvent } from '../../../api/endpoints/imagine';
import { useComponentSelection } from '../../../store/component-selection-store';
import {
  MIN_HEIGHT,
  clampHeight,
  parsePageMessage,
  triggerDownload,
  writeToEvent,
  type HostMessage,
} from './bridge';

interface StudioFrameProps {
  projectId: string;
  /** The engine page, already resolved for the browser. */
  src: string;
  /** The twin-backed state of each collection, sent as `fg:snapshot`. */
  collections: Record<string, unknown>;
  /** After handover the studio is read only: the page is told, and writes are not forwarded. */
  readOnly: boolean;
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
 */
export function StudioFrame({ projectId, src, collections, readOnly }: StudioFrameProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(640);
  const [ready, setReady] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const select = useComponentSelection((s) => s.select);
  // The latest values, read by the listener without re-subscribing.
  const state = useRef({ collections, readOnly });
  useEffect(() => {
    state.current = { collections, readOnly };
  }, [collections, readOnly]);

  const send = useCallback((msg: HostMessage) => {
    // A sandboxed frame has an opaque origin, so there is no origin to target.
    frame.current?.contentWindow?.postMessage(msg, '*');
  }, []);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || e.source !== win) return;
      if (e.origin !== 'null') return;
      const msg = parsePageMessage(e.data);
      if (!msg) return;
      switch (msg.type) {
        case 'fg:ready':
          setReady(true);
          send({ fg: 1, type: 'fg:snapshot', collections: state.current.collections });
          if (state.current.readOnly) send({ fg: 1, type: 'fg:readonly', value: true });
          return;
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
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [projectId, select, send]);

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
      send({ fg: 1, type: 'fg:readonly', value: readOnly });
    }
  }, [ready, readOnly, send]);

  return (
    <div className="cv-studio">
      <iframe
        ref={frame}
        className="cv-studio-frame"
        title="Imagine studio"
        src={src}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        style={{ height: Math.max(MIN_HEIGHT, height) }}
        data-testid="studio-frame"
      />
      {saveError && (
        <p className="cv-error" role="alert">
          {saveError}
        </p>
      )}
    </div>
  );
}
