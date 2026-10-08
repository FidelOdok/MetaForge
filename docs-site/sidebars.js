// @ts-check
/**
 * One hand-written sidebar, flat groups, no collapsing — the console's docs
 * page groups by purpose rather than by directory, and this mirrors it.
 *
 * Every published page must appear here exactly once. `tests/routes.test.mjs`
 * asserts that against the built site, because a page that is reachable but
 * absent from the sidebar is a page nobody finds.
 *
 * The `className` on each category is what draws the group icon (see
 * `src/css/custom.css`); it is not decoration for its own sake, it is the only
 * hook Docusaurus gives for styling a category label.
 */

/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docs: [
    {
      type: 'category',
      label: 'Getting started',
      className: 'dx-group dx-group--start',
      collapsible: false,
      collapsed: false,
      items: [
        { type: 'doc', id: 'index', label: 'Introduction' },
        'getting-started',
        'project-structure',
        'capability-matrix',
        'troubleshooting',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      className: 'dx-group dx-group--guides',
      collapsible: false,
      collapsed: false,
      items: ['cli-reference', 'dashboard-tour', 'simulation-results', 'approvals', 'session-capture', 'knowledge/datasheet-ingestion'],
    },
    {
      type: 'category',
      label: 'Core concepts',
      className: 'dx-group dx-group--concepts',
      collapsible: false,
      collapsed: false,
      items: [
        'architecture',
        'twin_schema',
        'skill_spec',
        'mcp_spec',
        'architecture/robust-harness-design',
        'architecture/design-flow-harness',
        'architecture/workflow-lifecycle',
        'architecture/model-usage',
        'architecture/migrations',
        'roadmap',
        'testing-strategy',
        'governance',
        'research/simulator-landscape-catalog',
      ],
    },
    {
      type: 'category',
      label: 'Integrations',
      className: 'dx-group dx-group--integrations',
      collapsible: false,
      collapsed: false,
      items: [
        'integrations/hosted-harness', 'integrations/claude-code',
        'integrations/codex',
        'integrations/mcp-config-examples',
        'integrations/lightrag-ui',
        'harness-codex-subscription',
      ],
    },
    {
      type: 'category',
      label: 'Deployment',
      className: 'dx-group dx-group--deployment',
      collapsible: false,
      collapsed: false,
      items: ['deployment/vercel', 'deployment/authentication'],
    },
    {
      type: 'category',
      label: 'API reference',
      className: 'dx-group dx-group--api',
      collapsible: false,
      collapsed: false,
      items: [
        'reference/gateway-api',
        { type: 'link', label: 'OpenAPI explorer', href: '/MetaForge/reference/openapi/' },
      ],
    },
  ],
};

export default sidebars;
