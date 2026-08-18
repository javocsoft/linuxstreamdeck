# landing/

The presentation and manual site for LinuxStreamDeck. Plain HTML, CSS and one
JavaScript file: no build step, no Node to serve it, no package manager, no
external request at run time. Copy the folder onto any static host and it
works.

```
landing/
├── index.html          the whole page (parts of it generated - see below)
├── generate.py         rebuilds everything derived from the application
├── check.mjs           asserts index.html and app.js agree (needs Node)
├── robots.txt          generated
├── sitemap.xml         generated
└── assets/
    ├── style.css       dark by default, light theme follows the visitor
    ├── app.js          action search and guide navigation
    ├── actions.json    generated: the action and game catalogues
    └── img/
        ├── keys-*.png      generated: composed by the real key/game renderers
        ├── og-card.png     generated: the 1200x630 social sharing card
        ├── favicon-*.png   generated: rasterized from logo.svg
        ├── app-window.png  generated copy of docs/screenshot.png
        └── logo.svg        the application icon
```

## Set the address before you deploy

**This is the one thing you must not skip.** Facebook, WhatsApp, X, LinkedIn,
Slack and Discord all refuse a relative `og:image`, so the sharing card only
appears if the page carries the absolute address it is really served from.
Search engines need the same for the canonical link.

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py \
    --site-url https://your.domain/
```

That rewrites the canonical link, `og:url`, `og:image`, `twitter:image`, the
structured data, `robots.txt` and `sitemap.xml` together - they are all built
from the one `<link rel="canonical">` in `index.html`, so they cannot drift
apart. The default is `https://javocsoft.github.io/linuxstreamdeck/`, which is
a guess at where this repository would publish, not a verified address.

After deploying, force the caches to re-read it, or an old preview sticks
around for days:

- Facebook / WhatsApp: <https://developers.facebook.com/tools/debug/>
- LinkedIn: <https://www.linkedin.com/post-inspector/>
- X: <https://cards-dev.twitter.com/validator>
- Google: Search Console, URL inspection, Request indexing

## Rebuilding the generated parts

`actions.json`, every `keys-*.png`, `og-card.png`, the favicons, the
application window copy, and the action/game catalogues inside `index.html`
all come from the code, so the site cannot drift from what the software does.
Rerun after adding an action or game, changing its name or description,
replacing `docs/screenshot.png`, or changing how a key is drawn:

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py
```

`LSD_CONFIG_DIR` is not optional and the script refuses to start without it:
importing the package reaches configuration code, and this must never touch the
real configuration. See AGENTS.md section 6.

The normal key pictures go through `renderer.compose()` **offscreen**, the game
preview is a deterministic seeded Neon Relay snapshot passed through the real
engine and render dispatcher, and the screen-saver picture uses its real frame
renderer. They are never screenshots of a running window. The application is
single-instance, so launching it to photograph it leaves a stale, cached view
(AGENTS.md section 5.5).
`app-window.png` is the one exception: every generation copies the repository's
hand-taken `docs/screenshot.png`.

The favicons are rasterized from `logo.svg` through GdkPixbuf, which comes with
PyGObject and is therefore already a dependency; no standalone rasterizer
(rsvg-convert, Inkscape, ImageMagick) is assumed to be installed.

## Why part of index.html is generated

The action catalogue is the most useful thing on the page and it arrives over
`fetch`, so to anything that does not run JavaScript the page had no content at
all - which is most social scrapers and every crawler that only reads the
served HTML. `generate.py` writes the unsearched list of cards between
`<!-- generated:cards -->` markers, and `app.js` replaces it with **identical**
markup on its first render.

The same catalogue JSON carries every entry from `games/catalog.py`.
`generate.py` derives the game count, the feature tags and the complete guide
list from those entries, while `check.mjs` verifies all three against
`actions.json`. Adding or renaming a game therefore has one canonical source.

That coupling is silent when it breaks: the list simply reshuffles or reflows
the instant the script runs. It has already broken twice - once on
indentation, once because the two sides sorted the catalogue differently and
"Open URL" swapped places with "Open application". So it is pinned:

```bash
node landing/check.mjs
```

Run that after touching `card()` in `app.js` or `_card_html()` in
`generate.py`. Node is only needed for this check, never to build or serve the
site.

The generated regions are `generated:cards`, `generated:count`,
`generated:games` and `generated:game-list`, plus the `[data-version]`,
`[data-action-count]` and `[data-game-count]` elements. Everything else in
`index.html` is written by hand.

## Checking it locally

```bash
python3 -m http.server 8765 --directory landing
```

Then open <http://127.0.0.1:8765/>. A server is required rather than opening
`index.html` from disk: `app.js` fetches `actions.json`, which `file://`
refuses. The page says so if that happens rather than showing an empty list.

## Releasing

`index.html` and `assets/actions.json` both record the version, so
`check_hardcoded_versions()` in `packaging/build-deb.sh` will **fail the next
release build** until this site is regenerated. That is the scan doing its job,
not a false positive: a site still naming the previous version while the
package ships the new one is exactly the staleness it exists to catch.

Change both authoritative version sources first, then regenerate before any
package build. The generator imports `linuxstreamdeck.VERSION`, while the
AppImage and Flatpak builders read `pyproject.toml`; those values must already
agree so every generated file and package receives the same release version:

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py
./packaging/build-deb.sh X.Y.Z
./packaging/build-appimage.sh
./packaging/build-flatpak.sh --bundle
```

Do not use the generator's `--version` override for a release: it can make the
landing look current while one of the two authoritative sources is still old.
Follow the complete ordered checklist in `AGENTS.md`, including the isolated
tests and site checks, verifying the version and release contents *inside* all
three final artifacts, and calculating checksums only after the last rebuild.

## Deploying

Upload the folder as it is. Every reference except the social and canonical
metadata is relative, so the site works from a subdirectory. For GitHub Pages,
point Pages at this folder on the default branch.

There is no analytics, no font from a CDN and no third-party script. That is
deliberate: the page is meant to be droppable anywhere, and a documentation
page that stops working because someone else's host is down is worse than a
plain one.

## Keeping it honest

Two claims on this page are written out by hand and nothing enforces them:

- The **hardware table** says only the 15-key Original has been run on real
  hardware. Update it when that stops being true, not before.
- The **"29 OBS actions"** figure in the differentiators section, which appears
  twice. Re-count if OBS actions are added:

  ```bash
  python3 -c "import json,collections;d=json.load(open('landing/assets/actions.json'));\
  print(sum(1 for a in d['actions'] if a['category'].startswith('OBS')))"
  ```
