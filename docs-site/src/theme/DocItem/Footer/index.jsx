import React from 'react';
import Footer from '@theme-original/DocItem/Footer';

const SCHEMA = 'https://fidelodok.github.io/MetaForge/reference/openapi.json';

/**
 * A hairline bar closing every article, before the edit/updated metadata.
 *
 * It exists to keep the gateway schema one click from any page: the question
 * that sends a reader back to the navigation most often is "what does the API
 * actually return", and that answer is generated from the running app rather
 * than written by hand.
 */
export default function FooterWrapper(props) {
  return (
    <>
      <div className="dx-article-end">
        <span>MetaForge documentation</span>
        <a href={SCHEMA} target="_blank" rel="noreferrer">
          Gateway schema <span aria-hidden="true">↗</span>
        </a>
      </div>
      <Footer {...props} />
    </>
  );
}
