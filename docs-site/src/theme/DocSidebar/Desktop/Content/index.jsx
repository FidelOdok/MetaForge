import React from 'react';
import Content from '@theme-original/DocSidebar/Desktop/Content';

/**
 * Frames the sidebar menu with the console's rail furniture: a monospace
 * label and version chip at the top, and two outbound links pinned to the
 * bottom.
 *
 * Wrapping `Content` rather than `Desktop` matters — these elements belong
 * inside the scrollable rail, not beside it, or they float over the menu when
 * it is long enough to scroll.
 */
export default function ContentWrapper(props) {
  return (
    <>
      <div className="dx-sidebar-label">
        DOCUMENTATION <span>Latest</span>
      </div>
      <Content {...props} />
      <div className="dx-sidebar-footer">
        <a
          href="https://fidelodok.github.io/MetaForge/reference/openapi.json"
          target="_blank"
          rel="noreferrer"
        >
          OpenAPI schema <span aria-hidden="true">↗</span>
        </a>
        <a href="https://github.com/FidelOdok/MetaForge" target="_blank" rel="noreferrer">
          Source repository <span aria-hidden="true">↗</span>
        </a>
      </div>
    </>
  );
}
