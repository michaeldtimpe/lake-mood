/*
 * zoleb.com canonical theme toggle
 *
 * Two pieces:
 *   1. Pre-paint snippet — place inline in <head> BEFORE any styles to avoid
 *      a flash of the wrong theme on load. Inline so it runs synchronously.
 *   2. Click handler — binds to a button with id="theme-toggle". Flips
 *      data-theme on <html>, persists to localStorage, updates button glyph.
 *
 * Inline pre-paint (paste verbatim into <head>):
 *
 *   <script>
 *     (function () {
 *       try {
 *         var t = localStorage.getItem("zoleb-theme") || "dark";
 *         document.documentElement.setAttribute("data-theme", t);
 *       } catch (e) {
 *         document.documentElement.setAttribute("data-theme", "dark");
 *       }
 *     })();
 *   </script>
 *
 * Button markup (label is set by the script — any placeholder text works):
 *
 *   <button id="theme-toggle" class="theme-toggle" type="button"
 *           aria-label="Toggle color theme">theme</button>
 *
 * Include this file once DOM is ready (or with `defer`).
 */

(function () {
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;

  function sync() {
    const dark = root.getAttribute("data-theme") !== "light";
    btn.textContent = dark ? "light mode" : "dark mode";
  }

  btn.addEventListener("click", function () {
    const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("zoleb-theme", next); } catch (e) {}
    sync();
  });

  sync();
})();
