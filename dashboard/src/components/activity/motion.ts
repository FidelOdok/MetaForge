import { useEffect, useRef, useState, type RefObject } from 'react';

/** The user's `prefers-reduced-motion` setting, kept current. */
export function usePrefersReducedMotion(): boolean {
  const query = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia(query);
    const onChange = () => setReduced(media.matches);
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

/**
 * False while the element is scrolled out of view or the tab is hidden, so
 * decorative motion stops costing frames nobody sees (ASVG-014).
 */
export function useOnScreen(ref: RefObject<Element>): boolean {
  const [inView, setInView] = useState(true);
  const [visible, setVisible] = useState(() => (typeof document === 'undefined' ? true : !document.hidden));
  useEffect(() => {
    const onVisibility = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) setInView(entry.isIntersecting);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return inView && visible;
}

/**
 * Text for an `aria-live` region, updated at most once per `intervalMs`
 * unless `urgent`, so a busy run does not read every update aloud (RT-035).
 */
export function useThrottledAnnouncement(text: string, urgent: boolean, intervalMs = 5_000): string {
  const [announced, setAnnounced] = useState(text);
  const last = useRef(0);
  useEffect(() => {
    if (text === announced) return undefined;
    const now = Date.now();
    const wait = urgent ? 0 : Math.max(0, last.current + intervalMs - now);
    const timer = setTimeout(() => {
      last.current = Date.now();
      setAnnounced(text);
    }, wait);
    return () => clearTimeout(timer);
  }, [text, urgent, intervalMs, announced]);
  return announced;
}

/** Wall-clock now, ticking once a second while `running`. */
export function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, [running]);
  return now;
}
