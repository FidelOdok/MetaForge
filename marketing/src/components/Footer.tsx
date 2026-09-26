import { DOCS_URL, GITHUB_URL } from '../site';

const LINKS = [
  { label: 'Documentation', href: DOCS_URL, external: true },
  { label: 'Architecture', href: `${DOCS_URL}architecture/`, external: true },
  { label: 'CLI reference', href: `${DOCS_URL}cli-reference/`, external: true },
  { label: 'GitHub', href: GITHUB_URL, external: true },
] as const;

export function Footer() {
  return (
    <footer className="border-t border-[rgba(65,72,90,0.2)] bg-surface-lowest">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          {/* The mark alone, not the lockup: at footer size the wordmark's
              lettering drops below a legible cap height. */}
          <img
            src="/logo/metaforge-mark-dark.svg"
            alt=""
            aria-hidden="true"
            width={28}
            height={28}
            className="h-7 w-auto"
          />
          <span className="text-[13.5px] text-on-surface-variant">
            MetaForge — local-first hardware design
          </span>
        </div>

        <nav className="flex flex-wrap gap-x-6 gap-y-2" aria-label="Footer">
          {LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] text-on-surface-variant transition-colors hover:text-on-surface"
            >
              {link.label}
            </a>
          ))}
        </nav>
      </div>
    </footer>
  );
}
