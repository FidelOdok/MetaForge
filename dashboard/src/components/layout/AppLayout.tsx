import { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current !== pathname) { main.current?.focus(); previousPath.current = pathname; }
    setNavOpen(false);
  }, [pathname]);
  useEffect(() => {
    const panel = document.querySelector<HTMLElement>('.workspace-sidebar');
    const body = document.querySelector<HTMLElement>('.workspace-body');
    const mobile = window.matchMedia('(max-width: 760px)');
    const syncNavigation = () => {
      if (panel) panel.inert = mobile.matches && !navOpen;
      if (body) body.inert = mobile.matches && navOpen;
    };
    syncNavigation();
    mobile.addEventListener('change', syncNavigation);
    if (navOpen) panel?.querySelector<HTMLElement>('a,button')?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && navOpen) {
        setNavOpen(false);
        if (body) body.inert = false;
        body?.querySelector<HTMLElement>('.mobile-menu')?.focus();
      }
      if (event.key === 'Tab' && navOpen && mobile.matches) {
        const controls = Array.from(panel?.querySelectorAll<HTMLElement>('a[href],button') ?? []).filter(el => el.getClientRects().length);
        const first = controls[0]; const last = controls[controls.length-1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    window.addEventListener('keydown', close);
    return () => { window.removeEventListener('keydown', close); mobile.removeEventListener('change', syncNavigation); if (body) body.inert = false; };
  }, [navOpen]);
  return <div className="workspace-shell">
    <a href="#main-content" className="skip-link">Skip to main content</a>
    <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
    <div className="workspace-body"><Topbar onMenu={() => setNavOpen(value => !value)} navOpen={navOpen}/><main ref={main} id="main-content" tabIndex={-1} className="workspace-main"><Outlet /></main></div>
  </div>;
}
