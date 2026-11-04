import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef } from 'react';
import type { TextareaHTMLAttributes } from 'react';

/**
 * A textarea that is always exactly as tall as its text: no height cap, no
 * inner scrollbar. It grows as the draft grows, shrinks when text is
 * removed, and measures again when its width changes (a window resize or a
 * panel opening reflows the lines).
 */
export const AutoGrowTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function AutoGrowTextarea(props, forwarded) {
    const ref = useRef<HTMLTextAreaElement>(null);
    useImperativeHandle(forwarded, () => ref.current as HTMLTextAreaElement);

    const fit = useCallback(() => {
      const el = ref.current;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    }, []);

    useLayoutEffect(fit, [fit, props.value]);

    useEffect(() => {
      const el = ref.current;
      if (!el || typeof ResizeObserver === 'undefined') return;
      let width = el.clientWidth;
      const observer = new ResizeObserver(() => {
        if (el.clientWidth !== width) {
          width = el.clientWidth;
          fit();
        }
      });
      observer.observe(el);
      return () => observer.disconnect();
    }, [fit]);

    return <textarea {...props} ref={ref} rows={props.rows ?? 1} style={{ ...props.style, overflowY: 'hidden', resize: 'none' }} />;
  },
);
