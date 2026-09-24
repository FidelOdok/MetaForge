import {
  BadgeCheck,
  BookOpen,
  Box,
  Boxes,
  Files,
  Package,
  Play,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'WORKSPACE',
    items: [
      { to: '/projects', label: 'Projects', icon: Boxes },
      { to: '/sessions', label: 'Agent sessions', icon: Workflow },
      { to: '/runs', label: 'Runs', icon: Play },
      { to: '/approvals', label: 'Approvals', icon: ShieldCheck },
    ],
  },
  {
    label: 'ENGINEERING',
    items: [
      { to: '/twin', label: 'Digital twin', icon: Box },
      { to: '/bom', label: 'Bill of materials', icon: Package },
      { to: '/files', label: 'Files & artifacts', icon: Files },
      { to: '/knowledge', label: 'Knowledge', icon: BookOpen },
      { to: '/compliance', label: 'Compliance', icon: BadgeCheck },
    ],
  },
];

/** Top-level route segment -> section label (breadcrumb + document title). */
export const SECTION_LABELS: Record<string, string> = {
  projects: 'Projects',
  sessions: 'Agent sessions',
  runs: 'Runs',
  approvals: 'Approvals',
  bom: 'Bill of materials',
  twin: 'Digital twin',
  files: 'Files & artifacts',
  knowledge: 'Knowledge',
  compliance: 'Compliance',
  settings: 'Settings & connection',
};

/** Viewport width at or below which the nav becomes a drawer. */
export const MOBILE_NAV_QUERY = '(max-width: 760px)';
export const MOBILE_NAV_MAX_WIDTH = 760;

/** The sample workspace is opened with `?demo=1` (see the Sample link). */
export function isSampleSearch(search: string): boolean {
  return new URLSearchParams(search).get('demo') === '1';
}

/**
 * Like the hosted build, a page load with `?demo=1` puts the whole session in
 * sample mode until the next full reload (the Sample / Exit sample links are
 * plain anchors, so they always reload).
 */
const SAMPLE_AT_LOAD =
  typeof window !== 'undefined' && isSampleSearch(window.location.search);

export function isSampleWorkspace(search: string): boolean {
  return SAMPLE_AT_LOAD || isSampleSearch(search);
}

export const SAMPLE_WORKSPACE_HREF = '/twin?demo=1&node=sample-pcb';
