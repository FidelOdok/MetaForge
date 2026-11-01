// @ts-check
/**
 * MetaForge documentation site.
 *
 * Content lives in `../docs` and is *not* duplicated here: this package is the
 * renderer, the markdown stays where contributors already edit it and where
 * GitHub renders it. `path: '../docs'` is the whole trick.
 *
 * Two constraints that are easy to break and expensive to notice:
 *
 *  - The published URLs must not move. This site replaces a MkDocs Material
 *    build at the same origin, and `https://fidelodok.github.io/MetaForge/...`
 *    is linked from the dashboard's gateway-setup screen, from the console's
 *    own docs page, and from README. MkDocs shipped directory URLs with a
 *    trailing slash, so `trailingSlash: true` is load-bearing, not cosmetic.
 *  - `markdown.format: 'detect'` keeps `.md` on CommonMark. The existing docs
 *    are full of `<project-id>`, `{project_id}` and other sequences that MDX
 *    reads as JSX or an expression. Pages that want components use `.mdx`.
 */

import { themes as prismThemes } from 'prism-react-renderer';

/**
 * Pages that stay in-repo for contributors but are not published.
 *
 * Deliberately identical to the `exclude_docs` list the MkDocs build used.
 * Anything else `docs/` contains was reachable on the old site, so dropping it
 * here would silently 404 a URL that used to work.
 */
const UNPUBLISHED = [
  'agents/**',
  'plans/**',
  'runbooks/**',
  'uat/**',
  'architecture/context-engineering.md',
  'architecture/knowledge-ingestion-playbook.md',
  'architecture/neo4j-migration.md',
  '**/_*.{js,jsx,ts,tsx,md,mdx}',
];

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'MetaForge',
  tagline: 'From intent to evidence.',
  favicon: 'img/favicon.svg',

  url: 'https://fidelodok.github.io',
  baseUrl: '/MetaForge/',
  trailingSlash: true,
  organizationName: 'FidelOdok',
  projectName: 'MetaForge',

  // The MkDocs build ran `--strict` and CI depended on it. Keep that bar.
  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',
  onDuplicateRoutes: 'throw',

  i18n: { defaultLocale: 'en', locales: ['en'] },

  markdown: {
    format: 'detect',
    mermaid: true,
    hooks: { onBrokenMarkdownLinks: 'throw' },
  },

  themes: [
    '@docusaurus/theme-mermaid',
    [
      '@easyops-cn/docusaurus-search-local',
      {
        hashed: true,
        indexBlog: false,
        docsRouteBasePath: '/',
        docsDir: '../docs',
        highlightSearchTermsOnTargetPage: true,
        searchResultLimits: 8,
        searchBarShortcutHint: false,
      },
    ],
  ],

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: '../docs',
          routeBasePath: '/',
          sidebarPath: './sidebars.js',
          exclude: UNPUBLISHED,
          editUrl: 'https://github.com/FidelOdok/MetaForge/edit/main/docs/',
          showLastUpdateTime: true,
          breadcrumbs: true,
        },
        blog: false,
        pages: false,
        theme: { customCss: './src/css/custom.css' },
        sitemap: { changefreq: 'weekly', priority: 0.5 },
      }),
    ],
    [
      'redocusaurus',
      /** @type {import('redocusaurus').PresetEntry[1]} */
      ({
        specs: [
          {
            id: 'gateway',
            // Read from the committed spec, not a copy. `npm run sync` also
            // places it in static/ so the raw JSON URL keeps working.
            spec: '../docs/reference/openapi.json',
            route: '/reference/openapi/',
          },
        ],
        theme: { primaryColor: '#bd4000', primaryColorDark: '#ff8a52' },
      }),
    ],
  ],

  stylesheets: [
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;550;600;700&family=Space+Grotesk:wght@500;600;700&family=Roboto+Mono:wght@400;500&display=swap',
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/metaforge-logo.svg',
      colorMode: { defaultMode: 'dark', respectPrefersColorScheme: true },
      docs: { sidebar: { hideable: false, autoCollapseCategories: false } },
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      navbar: {
        title: 'Docs',
        logo: {
          alt: 'MetaForge',
          src: 'img/metaforge-logo-light.svg',
          srcDark: 'img/metaforge-logo.svg',
          href: '/',
          width: 132,
          height: 42,
        },
        items: [
          { to: '/getting-started/', label: 'Guides', position: 'left' },
          { to: '/architecture/', label: 'Concepts', position: 'left' },
          { to: '/reference/gateway-api/', label: 'API reference', position: 'left' },
          {
            href: 'https://app.metaforge.uk',
            label: 'Open workspace',
            position: 'right',
            className: 'dx-open-app',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              { label: 'Getting started', to: '/getting-started/' },
              { label: 'CLI reference', to: '/cli-reference/' },
              { label: 'Gateway API', to: '/reference/gateway-api/' },
            ],
          },
          {
            title: 'Product',
            items: [
              { label: 'metaforge.uk', href: 'https://www.metaforge.uk' },
              { label: 'Dashboard', href: 'https://app.metaforge.uk' },
            ],
          },
          {
            title: 'Source',
            items: [
              { label: 'GitHub', href: 'https://github.com/FidelOdok/MetaForge' },
              {
                label: 'OpenAPI schema',
                href: 'https://fidelodok.github.io/MetaForge/reference/openapi.json',
              },
            ],
          },
        ],
        copyright: 'MetaForge — from intent to evidence.',
      },
      prism: {
        // The code card is dark in both page themes, so the syntax theme has
        // to be too. Pairing a light Prism theme with it puts near-black
        // tokens on a near-black card — legible in neither.
        theme: prismThemes.vsDark,
        darkTheme: prismThemes.vsDark,
        additionalLanguages: ['bash', 'json', 'yaml', 'python', 'docker', 'toml', 'diff', 'cypher'],
      },
      mermaid: { theme: { light: 'neutral', dark: 'dark' } },
    }),
};

export default config;
