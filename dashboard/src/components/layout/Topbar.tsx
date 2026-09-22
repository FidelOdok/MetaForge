import { useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronRight, Menu, PlugZap } from 'lucide-react';
import { ProjectSwitcher } from '../shared/ProjectSwitcher';
import { useHealth } from '../../hooks/use-health';
const labels: Record<string,string> = { projects:'Projects', sessions:'Agent sessions', runs:'Runs', approvals:'Approvals', bom:'Bill of materials', twin:'Digital twin', files:'Files & artifacts', knowledge:'Knowledge', compliance:'Compliance', settings:'Settings & connection' };
export function Topbar({ onMenu, navOpen }: { onMenu?:()=>void; navOpen?:boolean }) {
  const { pathname } = useLocation();
  const parts = pathname.split('/').filter(Boolean);
  const title = labels[parts[0] ?? ''] ?? 'Workspace';
  const health = useHealth();
  const status = health.isError ? 'Unavailable' : health.data?.status ?? 'Connecting';
  useEffect(()=>{document.title=`${title} — MetaForge`;},[title]);
  return <header className="workspace-topbar"><div className="topbar-location"><button className="mobile-menu icon-control" onClick={onMenu} aria-label="Toggle navigation" aria-expanded={navOpen}><Menu size={20}/></button><nav aria-label="Breadcrumb"><ol><li className="workspace-crumb">Workspace</li><li><ChevronRight size={14} aria-hidden="true"/><Link to={`/${parts[0] || 'projects'}`} aria-current={parts.length<2?'page':undefined}>{title}</Link></li>{parts.length>1 && <li><ChevronRight size={14} aria-hidden="true"/><span aria-current="page">Details</span></li>}</ol></nav></div><div className="topbar-actions"><Link to="/settings" className={`gateway-chip ${health.isError?'disconnected':''}`}><PlugZap size={15} aria-hidden="true"/><span>Gateway · {status}</span></Link><ProjectSwitcher/></div></header>;
}
