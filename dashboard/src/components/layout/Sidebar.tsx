import { NavLink } from 'react-router-dom';
import { Boxes, Workflow, Play, ShieldCheck, Package, Box, Files, BookOpen, BadgeCheck, Settings, ArrowUpRight, X } from 'lucide-react';

const groups = [
  { label: 'WORKSPACE', items: [ ['/projects', 'Projects', Boxes], ['/sessions', 'Agent sessions', Workflow], ['/runs', 'Runs', Play], ['/approvals', 'Approvals', ShieldCheck] ] },
  { label: 'ENGINEERING', items: [ ['/twin', 'Digital twin', Box], ['/bom', 'Bill of materials', Package], ['/files', 'Files & artifacts', Files], ['/knowledge', 'Knowledge', BookOpen], ['/compliance', 'Compliance', BadgeCheck] ] },
] as const;

export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  return <>
    {open && <button className="nav-backdrop" aria-label="Close navigation" onClick={onClose} />}
    <aside className={`workspace-sidebar ${open ? 'is-open' : ''}`}>
      <div className="brand-row"><NavLink to="/projects" className="brand" onClick={onClose}><img className="brand-logo" src="/metaforge-logo.svg" alt="MetaForge" width="200" height="64" /></NavLink><button className="mobile-close icon-control" aria-label="Close navigation" onClick={onClose}><X size={20}/></button></div>
      <div className="workspace-identity"><span className="workspace-monogram">MF</span><div><strong>Engineering workspace</strong><span>Local-first control plane</span></div></div>
      <nav aria-label="Main navigation" className="workspace-nav">{groups.map(group => <div className="nav-group" key={group.label}><p>{group.label}</p>{group.items.map(([to, label, Icon]) => <NavLink key={to} to={to} onClick={onClose} className={({ isActive }) => `workspace-nav-link ${isActive ? 'selected' : ''}`}><Icon size={18} aria-hidden="true"/><span>{label}</span></NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><NavLink to="/settings" onClick={onClose} className={({isActive})=>`workspace-nav-link ${isActive?'selected':''}`}><Settings size={18} aria-hidden="true"/>Settings & connection</NavLink><a className="docs-link" href="https://fidelodok.github.io/MetaForge/" target="_blank" rel="noreferrer">Documentation <ArrowUpRight size={15} aria-hidden="true"/></a><div className="sidebar-footnote">Human judgement at every gate.</div></div>
    </aside>
  </>;
}
