import React from 'react';
import TOC from '@theme-original/TOC';

const EXAMPLE =
  'https://github.com/FidelOdok/MetaForge/tree/main/examples/drone_flight_controller#readme';

/**
 * The right-hand rail: a label above the heading list, and one card below it.
 *
 * The card is the only promotional element on the page, and it points at
 * something real — the drone flight-controller example runs end to end on mock
 * adapters with no Docker — rather than at a demo that would have to be built
 * to make the card true.
 */
export default function TOCWrapper(props) {
  return (
    <>
      <p className="dx-toc-label">ON THIS PAGE</p>
      <TOC {...props} />
      <div className="dx-toc-card">
        <strong>See it end to end</strong>
        <p>
          A 4-layer PCB around the STM32F405RGT6, walked through six engineering disciplines on mock
          adapters.
        </p>
        <a href={EXAMPLE} target="_blank" rel="noreferrer">
          Open the example <span aria-hidden="true">↗</span>
        </a>
      </div>
    </>
  );
}
