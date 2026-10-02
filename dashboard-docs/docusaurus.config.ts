import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'MetaForge Dashboard Docs',
  tagline: 'User guide for the MetaForge engineering workspace',
  favicon: 'img/favicon.ico',

  // Future flags, see https://docusaurus.io/docs/api/docusaurus-config#future
  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Set the production url of your site here
  url: 'https://fidelodok.github.io',
  // Set the /<baseUrl>/ pathname under which your site is served
  // '/' suits a dedicated Vercel project (same pattern as dashboard +
  // marketing, see docs/deployment/vercel.md). If you deploy to GitHub
  // Pages under the main site instead, use '/MetaForge/dashboard-docs/'.
  baseUrl: '/',

  // GitHub pages deployment config.
  organizationName: 'FidelOdok', // Usually your GitHub org/user name.
  projectName: 'MetaForge', // Usually your repo name.

  onBrokenLinks: 'throw',

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/FidelOdok/MetaForge/tree/main/dashboard-docs/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Replace with your project's social card
    image: 'img/docusaurus-social-card.jpg',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'MetaForge Dashboard Docs',
      logo: {
        alt: 'MetaForge Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'dashboardSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://fidelodok.github.io/MetaForge/',
          label: 'Main docs',
          position: 'right',
        },
        {
          href: 'https://github.com/FidelOdok/MetaForge',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Intro',
              to: '/docs/intro',
            },
            {
              label: 'Current limitations',
              to: '/docs/known-gaps',
            },
          ],
        },
        {
          title: 'MetaForge',
          items: [
            {
              label: 'Main docs (MkDocs)',
              href: 'https://fidelodok.github.io/MetaForge/',
            },
            {
              label: 'GitHub',
              href: 'https://github.com/FidelOdok/MetaForge',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} MetaForge contributors. Built with Docusaurus. User guide for the dashboard; system architecture lives in the MkDocs site.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
