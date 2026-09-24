import { Suspense, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { useLayoutStore } from '../../store/layout-store';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { MOBILE_NAV_QUERY } from './nav';

type InertElement = HTMLElement & { inert: boolean };

/**
 * Workspace shell: skip link, floating nav (desktop rail / mobile drawer),
 * topbar and the routed page. `/twin` gets the full-bleed `twin-shell` variant.
 */
export function AppLayout() {
  const collapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const [navOpen, setNavOpen] = useState(false);
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const lastPath = useRef(pathname);

  // On navigation: move focus to the new page and close the drawer.
  useEffect(() => {
    if (lastPath.current !== pathname) {
      mainRef.current?.focus();
      lastPath.current = pathname;
    }
    setNavOpen(false);
  }, [pathname]);

  // Mobile drawer: inert the hidden half, focus trap, Escape to close.
  useEffect(() => {
    const sidebar = document.querySelector<InertElement>('.workspace-sidebar');
    const body = document.querySelector<InertElement>('.workspace-body');
    const mql = typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_NAV_QUERY) : null;
    const isMobile = () => mql?.matches ?? false;

    const sync = () => {
      if (sidebar) sidebar.inert = isMobile() && !navOpen;
      if (body) body.inert = isMobile() && navOpen;
    };
    sync();
    mql?.addEventListener?.('change', sync);
    if (navOpen) sidebar?.querySelector<HTMLElement>('a,button')?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && navOpen) {
        setNavOpen(false);
        if (body) body.inert = false;
        body?.querySelector<HTMLElement>('.mobile-menu')?.focus();
      }
      if (e.key === 'Tab' && navOpen && isMobile()) {
        const focusable = Array.from(
          sidebar?.querySelectorAll<HTMLElement>('a[href],button') ?? [],
        ).filter((el) => el.getClientRects().length);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      mql?.removeEventListener?.('change', sync);
      if (body) body.inert = false;
    };
  }, [navOpen]);

  return (
    <div
      className={`workspace-shell ${collapsed ? 'nav-collapsed' : ''} ${pathname === '/twin' ? 'twin-shell' : ''}`}
    >
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="workspace-body">
        <Topbar onMenu={() => setNavOpen((v) => !v)} navOpen={navOpen} />
        <main ref={mainRef} id="main-content" tabIndex={-1} className="workspace-main">
          <Suspense
            fallback={
              <div className="workspace-empty" role="status">
                Loading workspace…
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
