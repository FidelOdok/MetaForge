import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import { CHAT_WIDTH_DEFAULT, CHAT_WIDTH_MAX, CHAT_WIDTH_MIN, useCreateLayoutStore } from '../../store/create-layout-store';

const STEP = 16;

/**
 * The conversation's right edge: drag it to make the conversation wider or
 * narrower, use the arrow keys when it has focus, double-click to reset. The
 * width is the person's own preference, kept in this browser.
 */
export function ChatResizer() {
  const width = useCreateLayoutStore((s) => s.chatWidth);
  const setWidth = useCreateLayoutStore((s) => s.setChatWidth);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== undefined && e.button > 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    const move = (ev: PointerEvent) => setWidth(startW + ev.clientX - startX);
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      document.body.classList.remove('cv-resizing');
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    document.body.classList.add('cv-resizing');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === 'ArrowLeft' ? width - STEP
      : e.key === 'ArrowRight' ? width + STEP
      : e.key === 'Home' ? CHAT_WIDTH_MIN
      : e.key === 'End' ? CHAT_WIDTH_MAX
      : null;
    if (next === null) return;
    e.preventDefault();
    setWidth(next);
  };

  return (
    <div
      className="cv-chat-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the conversation"
      aria-valuenow={width}
      aria-valuemin={CHAT_WIDTH_MIN}
      aria-valuemax={CHAT_WIDTH_MAX}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => setWidth(CHAT_WIDTH_DEFAULT)}
    />
  );
}
