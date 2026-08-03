# landing/

The presentation and manual site for LinuxStreamDeck. Plain HTML, CSS and one
JavaScript file: no build step, no Node, no package manager, no external
request at run time. Copy the folder onto any static host and it works.

```
landing/
├── index.html          the whole page
├── generate.py         rebuilds everything derived from the application
├── assets/
│   ├── style.css       dark by default, light theme follows the visitor
│   ├── app.js          action search and guide navigation
│   ├── actions.json    generated: the full action catalogue
│   └── img/
│       ├── keys-*.png  generated: composed by the real key renderer
│       ├── app-window.png  the application window (docs/screenshot.png)
│       └── logo.svg    the application icon
```

## Rebuilding the generated parts

`actions.json` and every `keys-*.png` come from the code, so the site cannot
drift from what the software actually does. Rerun after adding an action,
changing an action's name or description, or changing how a key is drawn:

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py
```

`LSD_CONFIG_DIR` is not optional and the script refuses to start without it:
importing the package reaches configuration code, and this must never touch the
real configuration. See AGENTS.md §6.

The key pictures go through `renderer.compose()` **offscreen** — they are never
screenshots of a running window. The application is single-instance, so
launching it to photograph it leaves a stale, cached view (AGENTS.md §5.5).
`app-window.png` is the one exception: it is the repository's own
`docs/screenshot.png`, taken by hand.

## Checking it locally

```bash
python3 -m http.server 8765 --directory landing
```

Then open <http://127.0.0.1:8765/>. A server is required rather than opening
`index.html` from disk: `app.js` fetches `actions.json`, which `file://`
refuses. The page says so if that happens rather than showing an empty list.

## Deploying

Upload the folder as it is. Nothing needs a specific path, every reference is
relative, and the site works from a subdirectory. If you serve it from GitHub
Pages, point Pages at this folder on the default branch.

There is no analytics, no font from a CDN and no third-party script. That is
deliberate: the page is meant to be droppable anywhere, and a documentation
page that stops working because someone else's host is down is worse than a
plain one.

## Editing

- **Copy, layout and the guide** live in `index.html`.
- **Anything about actions** comes from `assets/actions.json` — edit the action
  in the Python source and regenerate; do not edit the JSON.
- `app.js` expects these ids: `#q`, `#cards`, `#count`, `#filters`, `#year`,
  plus `[data-version]` and `[data-action-count]` elements to fill in, and the
  `.guide aside a` / `.guide .step` pairing for the sidebar highlight.

The search is deliberately written by hand rather than pulling in a library.
The catalogue is 65 short entries, so a dependency would be more code than the
feature, and fuzzy matching at this size invents matches instead of admitting
there are none.

## Releasing

`assets/actions.json` records the version it was generated from, so
`check_hardcoded_versions()` in `packaging/build-deb.sh` will **fail the next
release build** until this site is regenerated. That is the scan doing its job,
not a false positive: a site still naming the previous version while the
package ships the new one is exactly the staleness it exists to catch. The
release order is therefore:

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py
./packaging/build-deb.sh X.Y.Z
```

`index.html` deliberately carries **no** version literal — `app.js` fills the
two `[data-version]` elements from the JSON, and a hardcoded fallback would
have failed that same scan for nothing.

## Keeping it honest

Two claims on this page are written out by hand and nothing enforces them:

- The **hardware table** says only the 15-key Original has been run on real
  hardware. Update it when that stops being true, not before.
- The **"29 OBS actions"** figure in the differentiators section, which appears
  twice. Re-count from `actions.json` if OBS actions are added:

  ```bash
  python3 -c "import json,collections;d=json.load(open('landing/assets/actions.json'));\
  print(sum(1 for a in d['actions'] if a['category'].startswith('OBS')))"
  ```
