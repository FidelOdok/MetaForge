import { useEffect, useState } from 'react';
import { ButtonLink, Icon } from './primitives';
import { DASHBOARD_URL, GITHUB_URL, NAV_LINKS } from '../site';

function Logo() {
  return (
    <a href="#top" className="flex items-center" aria-label="MetaForge home">
      {/* The lockup is a fixed-proportion asset, so it is sized by height only.
          Marketing is dark-only (see index.css), hence the -dark variant. */}
      <img
        src="/logo/metaforge-wordmark-dark.svg"
        alt="MetaForge"
        width={146}
        height={40}
        className="h-10 w-auto"
      />
    </a>
  );
}

export function Header() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors ${
        scrolled ? 'border-b border-[rgba(65,72,90,0.2)] bg-[rgba(17,19,25,0.85)] backdrop-blur-md' : ''
      }`}
    >
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-6">
        <Logo />

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {NAV_LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              {...('external' in link && link.external
                ? { target: '_blank', rel: 'noreferrer' }
                : {})}
              className="text-[13.5px] text-on-surface-variant transition-colors hover:text-on-surface"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-3 md:flex">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            aria-label="MetaForge on GitHub"
            className="flex h-8 w-8 items-center justify-center rounded-md text-on-surface-variant transition-colors hover:bg-surface-high hover:text-on-surface"
          >
            <Icon name="code" size={19} />
          </a>
          <ButtonLink href={DASHBOARD_URL} className="!px-4 !py-2">
            Open the dashboard
          </ButtonLink>
        </div>

        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-expanded={menuOpen}
          aria-label="Toggle navigation"
          className="flex h-9 w-9 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-high md:hidden"
        >
          <Icon name={menuOpen ? 'close' : 'menu'} size={22} />
        </button>
      </div>

      {menuOpen && (
        <div className="border-t border-[rgba(65,72,90,0.2)] bg-surface px-6 pb-5 pt-3 md:hidden">
          <nav className="flex flex-col gap-1" aria-label="Primary (mobile)">
            {NAV_LINKS.map((link) => (
              <a
                key={link.label}
                href={link.href}
                onClick={() => setMenuOpen(false)}
                {...('external' in link && link.external
                  ? { target: '_blank', rel: 'noreferrer' }
                  : {})}
                className="rounded px-2 py-2 text-sm text-on-surface-variant hover:bg-surface-high hover:text-on-surface"
              >
                {link.label}
              </a>
            ))}
          </nav>
          <ButtonLink href={DASHBOARD_URL} className="mt-3 w-full">
            Open the dashboard
          </ButtonLink>
        </div>
      )}
    </header>
  );
}
