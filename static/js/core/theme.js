/* =============================================================================
   core/theme.js — data-theme hook (runs synchronously in <head>, no defer)
   -----------------------------------------------------------------------------
   Applies the saved theme before first paint (no flash of wrong theme) and
   exposes window.POS.theme for the navbar toggle and SSE theme sync.

   Persistence is the V1 key localStorage['posTheme'] (Settings > Theme), so
   the operator's choice carries over between the legacy UI and this one:
       'light' -> data-theme="light"     'dark' -> data-theme="dark"
       unset   -> "light"  (the classic default: light POS screens)
   The attribute lives on <html>, which htmx child-view swaps never touch,
   so the theme survives every in-app navigation; deep-link full loads
   re-run this boot script.
   ========================================================================== */
(function () {
  'use strict';

  var KEY = 'posTheme';
  var LEGACY_TO_THEME = { light: 'light', dark: 'dark' };
  var DEFAULT_THEME = 'light';
  var root = document.documentElement;

  function readSaved() {
    try {
      return localStorage.getItem(KEY);
    } catch (_) {
      return null; /* storage disabled/blocked -> default theme */
    }
  }

  function current() {
    var saved = readSaved();
    return LEGACY_TO_THEME[saved] || DEFAULT_THEME;
  }

  function apply(mode) {
    root.setAttribute('data-theme', mode);
    return mode;
  }

  /* Boot: apply before first paint. */
  apply(current());

  /* Public API — consumed by the navbar toggle (Step 5) and sse.js (Step 4). */
  window.POS = window.POS || {};
  window.POS.theme = {
    get: function () {
      return root.getAttribute('data-theme');
    },
    set: function (mode) {
      var next = mode === 'dark' ? 'dark' : 'light';
      apply(next);
      try {
        localStorage.setItem(KEY, next);
      } catch (_) {}
      return next;
    },
    toggle: function () {
      return this.set(this.get() === 'dark' ? 'light' : 'dark');
    }
  };
})();

/* Navbar toggle (Step 5 shell): any [data-theme-toggle] button flips the theme. */
document.addEventListener('DOMContentLoaded', function () {
  var toggles = document.querySelectorAll('[data-theme-toggle]');
  for (var i = 0; i < toggles.length; i++) {
    toggles[i].addEventListener('click', function () {
      window.POS.theme.toggle();
    });
  }
});
