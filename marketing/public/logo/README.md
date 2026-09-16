# MetaForge logo assets

Served from `/logo/` on the marketing site, and the canonical copy for anything
else that needs the mark — the GitHub README, a business plan, letterhead.

## Files

| File | Contents | Use on |
|---|---|---|
| `metaforge-lockup-{dark,light}.svg` | Mark + `META FORGE` + strapline | Title pages, social cards, anywhere with room for the strapline |
| `metaforge-wordmark-{dark,light}.svg` | Mark + `META FORGE` | Site header, document headers |
| `metaforge-mark-{dark,light}.svg` | Mark only, square | Favicons, avatars, footers, anywhere under ~40px |
| `metaforge-*.svg` (no suffix) | Neutral ink is `currentColor` | Inline SVG only — see below |

`-dark` means **for use on a dark background** (white ink); `-light` means for
use on a light background (near-black ink). Both keep the orange unchanged.

The unsuffixed `currentColor` variants inherit the surrounding text colour, so
one file covers both themes — but only when the SVG is inlined into the DOM.
Referenced through `<img src>` the colour cannot be inherited and it falls back
to black, so use an explicit `-dark`/`-light` file there. The site uses `<img>`,
and therefore `-dark`.

## Colour

| Token | Value | Notes |
|---|---|---|
| Brand orange | `#FF5A0A` | The logo's own orange. Do not substitute. |
| Ink on dark | `#FFFFFF` | |
| Ink on light | `#0C0E14` | Matches `--color-surface-lowest`. |

⚠️ This is **not** the site's accent. `src/index.css` sets
`--color-primary-container: #e67e22`, a softer amber, and that token is shared
with the dashboard. The two oranges sit next to each other in the header. Either
move the palette to `#FF5A0A` in both apps or accept the difference deliberately
— but do not fix it by recolouring the logo, which is the brand constant here.

## Known limitations

**The source is an auto-trace, not drawn vector type.** The original SVG carried
`<desc>Vector trace of the supplied logo. All lettering is outlined.</desc>` —
48 paths and ~3,500 cubic segments for a horizontal lockup. Consequences:

- lettering is outlined, so the strapline cannot be edited as text
- letterforms carry slight trace wobble, visible in large-format print
- the wordmark is ~30 KB gzipped, heavy for a header

Coordinates have been reduced to one decimal place, which is invisible at any
size the web uses (verified: at 2000px wide, 0.77% of pixels differ by at most
28/255 — antialiasing only) and takes about 21% off the wire size.

**The mark is soft below about 24px.** Its fine parallel slashes merge into a
flat tone at favicon size. `favicon.svg` is still the right mark and beats a
generic placeholder, but a purpose-drawn small-size variant — fewer, thicker
slashes holding the same silhouette — would be a real improvement.

Redrawing the lockup from live type would fix all of the above at once, and is
the recommended next step if the logo is going anywhere near print.

## Strapline

**HARDWARE FROM INTENT.**

The originals read `FROM INTENT HARDWARE`, which is a scramble — it was wrong in
the artwork and in the SVG `<title>`. Because the lettering is outlined, it was
corrected by measuring each glyph, grouping the glyphs into words on their
x-gaps, and translating whole words into the right order. Letter shapes and
intra-word spacing are therefore untouched; the word gap is the mean of the two
original gaps, and the strapline occupies exactly the same span as before, so
the composition is unchanged.

The superseded originals are in the Navrik workspace under
`Metaforge/logo designs`. Do not reuse them.
