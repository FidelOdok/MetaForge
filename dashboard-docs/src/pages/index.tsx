import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link
            className="button button--secondary button--lg"
            to="/docs/intro">
            Dashboard reference
          </Link>
        </div>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title={siteConfig.title}
      description="Per-page reference for the MetaForge dashboard.">
      <HomepageHeader />
      <main>
        <div className="container" style={{padding: '2rem 0'}}>
          <p>
            Per-page reference for the engineering workspace (
            <code>dashboard/</code>
            ): what each route does, which gateway endpoints it calls, and
            how to check outcomes against the twin.
          </p>
          <p>
            System architecture truth lives in the{' '}
            <a href="https://fidelodok.github.io/MetaForge/">
              main MkDocs site
            </a>
            . This site never duplicates it; it links there.
          </p>
        </div>
      </main>
    </Layout>
  );
}
