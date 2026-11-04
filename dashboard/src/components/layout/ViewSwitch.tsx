import { Link, useLocation } from 'react-router-dom';
import { Sparkles, Wrench } from 'lucide-react';
import { useProjectStore } from '../../store/project-store';

/**
 * Create / Engineering: two views of the same project. Both follow the
 * active project, so switching never opens, copies or loses one; the Create
 * view's draft and attachments live in its own store and are still there on
 * the way back.
 */
export function ViewSwitch() {
  const { pathname } = useLocation();
  const activeProjectId = useProjectStore((s) => s.activeProjectId);
  const inCreate = pathname === '/create' || pathname.startsWith('/create/');
  const createHref = activeProjectId ? `/create/${encodeURIComponent(activeProjectId)}` : '/create';
  const engineeringHref = activeProjectId ? `/projects/${encodeURIComponent(activeProjectId)}` : '/projects';

  return (
    <nav className="view-switch" aria-label="View">
      <Link to={createHref} className={`view-switch-option ${inCreate ? 'is-on' : ''}`} aria-current={inCreate ? 'page' : undefined}>
        <Sparkles size={14} aria-hidden="true" />
        <span>Create</span>
      </Link>
      <Link
        to={inCreate ? engineeringHref : pathname}
        className={`view-switch-option ${inCreate ? '' : 'is-on'}`}
        aria-current={inCreate ? undefined : 'page'}
      >
        <Wrench size={14} aria-hidden="true" />
        <span>Engineering</span>
      </Link>
    </nav>
  );
}
