import type { ReactNode, Ref } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

interface PageHeaderProps {
  title: ReactNode;
  /** A short summary beside the title, in monospace: counts, scope. */
  meta?: ReactNode;
  /** Shown after the title, e.g. a status badge. */
  badge?: ReactNode;
  /** One plain sentence under the title, when the page needs it. */
  description?: ReactNode;
  back?: { to: string; label: string };
  /** The page's buttons, filters or status, on the right. */
  actions?: ReactNode;
  /** For a page that moves focus to its title, e.g. between steps. */
  titleRef?: Ref<HTMLHeadingElement>;
}

/** The Engineering view's page header, laid out like the Files page. */
export function PageHeader({ title, meta, badge, description, back, actions, titleRef }: PageHeaderProps) {
  return (
    <header className="mf-page-header">
      <div>
        {back && (
          <Link to={back.to} className="mf-page-header-back">
            <ArrowLeft size={13} aria-hidden="true" />
            {back.label}
          </Link>
        )}
        <div className="mf-page-header-title">
          <h1 ref={titleRef} tabIndex={titleRef ? -1 : undefined}>
            {title}
          </h1>
          {badge}
          {meta && <span className="mf-page-header-meta">{meta}</span>}
        </div>
      </div>
      {actions && <div className="mf-actions">{actions}</div>}
      {description && <p className="mf-page-header-description">{description}</p>}
    </header>
  );
}
