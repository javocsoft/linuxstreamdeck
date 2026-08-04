/* Assert that the catalogue baked into index.html is exactly what app.js
 * builds at run time.
 *
 * generate.py writes the 65 action cards into the served HTML so crawlers and
 * non-JavaScript readers see them, and app.js replaces that block on its first
 * render. If the two ever disagree the list silently reshuffles or reflows the
 * moment the script runs, and nothing else notices. Both halves of that have
 * already happened once: the templates differed only in indentation, and the
 * two sides sorted the catalogue differently, so "Open URL" and "Open
 * application" swapped places on load.
 *
 *   node landing/check.mjs
 *
 * Run it after touching card() in app.js or _card_html() in generate.py.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(join(HERE, name), "utf8");

const html = read("index.html");
const data = JSON.parse(read("assets/actions.json"));

const region = html.match(
  /<!-- generated:cards -->([\s\S]*?)<!-- \/generated:cards -->/
);
if (!region) {
  console.error("FAIL  index.html has no generated:cards region");
  process.exit(1);
}

/* A DOM stub with exactly the surface app.js touches. It deliberately does not
 * grow beyond that: a stub that pretends to be a browser invites tests that
 * pass against the stub and fail against Chrome. */
const node = () => ({
  value: "", textContent: "", innerHTML: "", dataset: {},
  addEventListener() {}, focus() {}, select() {}, setAttribute() {},
  getAttribute: () => null, classList: { toggle() {} },
});

const parts = {
  "#q": node(), "#cards": node(), "#count": node(),
  "#filters": node(), "#year": node(),
};
const lists = {
  "[data-version]": [node(), node()],
  "[data-action-count]": [node()],
  ".guide aside a": [],
  ".guide .step": [],
};

const document = {
  querySelector: (sel) => parts[sel] ?? null,
  querySelectorAll: (sel) => lists[sel] ?? [],
  getElementById: () => null,
  addEventListener() {},
  activeElement: null,
};
// No IntersectionObserver, so app.js skips the guide-navigation block.
const window = { scrollY: 0 };
const location = { hash: "" };
const fetch = async () => ({ json: async () => data });

const run = new Function(
  "document", "window", "location", "fetch", read("assets/app.js")
);
run(document, window, location, fetch);

// The fetch resolves on a microtask; let it settle before reading the result.
await new Promise((resolve) => setImmediate(resolve));

const built = parts["#cards"].innerHTML;
const baked = region[1];

if (built !== baked) {
  let at = 0;
  while (at < built.length && built[at] === baked[at]) at += 1;
  // Clamp the start: a difference in the first 60 characters is the likeliest
  // case of all, and a negative slice index counts from the end of the string,
  // so the excerpt came out empty exactly when it was needed.
  const around = (s) => s.slice(Math.max(0, at - 60), at + 60);
  console.error("FAIL  index.html and app.js disagree about the card markup");
  console.error(`      first difference at character ${at}`);
  console.error(`      index.html: ${JSON.stringify(around(baked))}`);
  console.error(`      app.js:     ${JSON.stringify(around(built))}`);
  console.error("      Regenerate with landing/generate.py, or reconcile the");
  console.error("      two templates. Order counts as a difference.");
  process.exit(1);
}

const bakedCount = (baked.match(/<article/g) || []).length;
if (bakedCount !== data.actions.length) {
  console.error(
    `FAIL  index.html holds ${bakedCount} cards, actions.json has ` +
    `${data.actions.length}. Regenerate.`
  );
  process.exit(1);
}
if (parts["#count"].textContent !== `${data.actions.length} actions`) {
  console.error(`FAIL  the count line reads "${parts["#count"].textContent}"`);
  process.exit(1);
}

console.log(`ok  ${bakedCount} cards identical in index.html and app.js`);
