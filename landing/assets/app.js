/* Action explorer, guide navigation, and nothing else.
 *
 * No search library on purpose. The catalogue is a small set with five short
 * fields each, so a dependency would be more code than the feature and would
 * add an external request to a page meant to be dropped on any server. Fuzzy
 * matching is also the wrong tool at this size: on a small set it invents
 * matches instead of admitting there are none.
 */

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  /* ------------------------------------------------------------ explorer */

  const box = $("#q");
  const grid = $("#cards");
  const tally = $("#count");
  const filterBar = $("#filters");
  if (!box || !grid) return;

  let ACTIONS = [];
  let category = "";

  /** Everything one action can be found by, lowercased once at load. */
  const haystack = (a) =>
    [
      a.name,
      a.id,
      a.category,
      a.description,
      ...a.params.map((p) => p.label),
      ...a.params.flatMap((p) => p.choices),
      ...a.needs,
    ]
      .join(" ")
      .toLowerCase();

  /** Every term must appear somewhere. A missing word means it is not this
   *  action, which on a catalogue this small is far more useful than a
   *  ranked list of near misses. */
  function matches(entry, terms) {
    return terms.every((t) => entry.hay.includes(t));
  }

  /** Ranked so the obvious answer is first: a name that starts with what was
   *  typed beats one that merely contains it, which beats a description hit. */
  function score(entry, terms) {
    const name = entry.action.name.toLowerCase();
    const id = entry.action.id.toLowerCase();
    let points = 0;
    for (const t of terms) {
      if (name.startsWith(t) || id.startsWith(t)) points += 100;
      else if (name.includes(t)) points += 60;
      else if (id.includes(t)) points += 40;
      else if (entry.action.category.toLowerCase().includes(t)) points += 20;
      else points += 5;
    }
    return points;
  }

  const escape = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  /** Highlight the typed terms in already-escaped text. */
  function mark(text, terms) {
    let out = escape(text);
    for (const t of terms) {
      if (t.length < 2) continue;
      const safe = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      out = out.replace(new RegExp(`(${safe})`, "gi"), "<mark>$1</mark>");
    }
    return out;
  }

  /** A card must explain why it is in the results. An action can match on one
   *  of its choices — "Media action" answers a search for "mute" through its
   *  Action list — and without showing that, the row looks like a stray hit. */
  function hitChoices(a, terms) {
    if (!terms.length) return [];
    const seen = [];
    for (const p of a.params) {
      for (const c of p.choices) {
        const low = c.toLowerCase();
        if (terms.some((t) => low.includes(t)) && !seen.includes(c)) seen.push(c);
      }
    }
    return seen.slice(0, 4);
  }

  function card(a, terms) {
    const hits = hitChoices(a, terms)
      .map((c) => `<li class="hit">${mark(c, terms)}</li>`)
      .join("");
    const params = a.params
      .slice(0, 6)
      .map((p) => `<li>${escape(p.label)}</li>`)
      .join("");
    const needs = a.needs
      .map((n) => `<li class="need">needs ${escape(n)}</li>`)
      .join("");
    // Written on one line, and it has to stay that way: landing/generate.py
    // emits this exact markup into index.html so the catalogue is in the
    // served HTML for crawlers, and a test asserts the two are byte-identical.
    // Indenting this template would break that without changing anything on
    // screen, which is the worst kind of difference to leave lying around.
    return (
      '<article class="card">' +
      `<header><h4>${mark(a.name, terms)}</h4>` +
      `<span class="cat">${escape(a.category)}</span></header>` +
      `<p>${mark(a.description || "", terms)}</p>` +
      `<ul>${needs}${hits}${params}<li class="aid">${escape(a.id)}</li></ul>` +
      "</article>"
    );
  }

  function render() {
    const raw = box.value.trim().toLowerCase();
    const terms = raw ? raw.split(/\s+/) : [];
    let found = ACTIONS.filter(
      (e) => (!category || e.action.category === category) && matches(e, terms)
    );
    if (terms.length) {
      found = found
        .map((e) => ({ e, s: score(e, terms) }))
        .sort((a, b) => b.s - a.s || a.e.action.name.localeCompare(b.e.action.name))
        .map((x) => x.e);
    }
    // Unsearched, this is a catalogue rather than a result list, and it is
    // already grouped by category: generate.py sorts actions.json and writes
    // the same list into index.html for crawlers. Re-sorting here would be a
    // second opinion on the order, and it was — localeCompare disagreed with
    // Python's sort about "Open URL" against "Open application", so the list
    // visibly reshuffled the moment the script ran.
    tally.textContent =
      found.length === ACTIONS.length
        ? `${ACTIONS.length} actions`
        : `${found.length} of ${ACTIONS.length} actions`;
    grid.innerHTML = found.length
      ? found.map((e) => card(e.action, terms)).join("")
      : `<p class="empty">Nothing matches <b>${escape(box.value)}</b>.
           Try <button class="linkish" data-clear>clearing the search</button>.</p>`;
  }

  function buildFilters(cats) {
    filterBar.innerHTML =
      `<button aria-pressed="true" data-cat="">Everything</button>` +
      cats
        .map((c) => `<button aria-pressed="false" data-cat="${escape(c)}">${escape(c)}</button>`)
        .join("");
    filterBar.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-cat]");
      if (!button) return;
      category = button.dataset.cat;
      $$("button", filterBar).forEach((b) =>
        b.setAttribute("aria-pressed", String(b === button))
      );
      render();
    });
  }

  grid.addEventListener("click", (event) => {
    if (event.target.closest("[data-clear]")) {
      box.value = "";
      render();
      box.focus();
    }
  });

  box.addEventListener("input", render);

  // "/" focuses the search from anywhere, the way a documentation site should.
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== box) {
      event.preventDefault();
      box.focus();
      box.select();
    } else if (event.key === "Escape" && document.activeElement === box) {
      box.value = "";
      render();
    }
  });

  /** The browser resolves the address bar's fragment while the card grid is
   *  still empty, and filling it inserts ten thousand pixels above the guide —
   *  so a shared link to a guide step lands nowhere near it. Re-apply the
   *  fragment once the page has its real height, but only if the visitor has
   *  not scrolled in the meantime: yanking the page out from under somebody
   *  who already started reading would be worse than the wrong landing. */
  function restoreFragment(scrollBefore) {
    if (!location.hash || window.scrollY !== scrollBefore) return;
    const target = document.getElementById(location.hash.slice(1));
    if (target) target.scrollIntoView({ behavior: "instant", block: "start" });
  }

  fetch("assets/actions.json")
    .then((r) => r.json())
    .then((data) => {
      ACTIONS = data.actions.map((action) => ({
        action,
        hay: haystack(action),
      }));
      const cats = [...new Set(data.actions.map((a) => a.category))].sort();
      buildFilters(cats);
      $$("[data-version]").forEach((n) => (n.textContent = data.version));
      $$("[data-action-count]").forEach(
        (n) => (n.textContent = String(data.actions.length))
      );
      $$("[data-game-count]").forEach(
        (n) => (n.textContent = String(data.games.length))
      );
      const before = window.scrollY;
      render();
      restoreFragment(before);
    })
    .catch(() => {
      grid.innerHTML = `<p class="empty">The action list could not be loaded.
        If you opened this page straight from the file system, serve the folder
        instead — <code>python3 -m http.server</code> in it is enough.</p>`;
    });

  /* --------------------------------------------------------------- guide */

  const links = $$(".guide aside a");
  const steps = $$(".guide .step");
  if (links.length && steps.length && "IntersectionObserver" in window) {
    const seen = new Map();
    const watcher = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => seen.set(e.target.id, e.intersectionRatio));
        let best = "";
        let ratio = 0;
        seen.forEach((value, id) => {
          if (value > ratio) {
            ratio = value;
            best = id;
          }
        });
        links.forEach((a) =>
          a.classList.toggle("on", a.getAttribute("href") === `#${best}`)
        );
      },
      { rootMargin: "-84px 0px -55% 0px", threshold: [0, 0.25, 0.5, 1] }
    );
    steps.forEach((s) => watcher.observe(s));
  }

  $("#year") && ($("#year").textContent = new Date().getFullYear());
})();
