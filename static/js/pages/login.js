/* =============================================================================
   pages/login.js — login behavior (spinner, password toggle, card-swipe login)
   -----------------------------------------------------------------------------
   Loaded by login.html only — a standalone pre-auth page, so there is no
   spa.js mount/destroy contract here; the module runs once at import.

   Card-swipe login keeps the V1 protocol: keystrokes typed outside the
   credential fields are buffered; a run of >= 8 digits within 350ms is
   treated as a card read, the credentials are cleared (the server prefers
   card_number when username/password are empty) and the form submits.
   ========================================================================== */
(function () {
  'use strict';

  var form = document.getElementById('login-form');
  var submitBtn = document.querySelector('.login-submit');
  var usernameInput = document.getElementById('username');
  var passwordInput = document.getElementById('password');
  var toggleBtn = document.querySelector('[data-password-toggle]');
  var cardNumberInput = document.getElementById('cardNumber');
  var swipeStatus = document.getElementById('cardSwipeStatus');

  /* Submit — block double-submits with the loading state on the CTA. */
  if (form && submitBtn) {
    form.addEventListener('submit', function () {
      // A stale hidden card_number must not hijack a manual login.
      var manual = (usernameInput && usernameInput.value.trim()) ||
                   (passwordInput && passwordInput.value);
      if (manual && cardNumberInput) {
        cardNumberInput.value = '';
      }
      submitBtn.classList.add('is-loading');
      submitBtn.disabled = true;
      submitBtn.setAttribute('aria-busy', 'true');
    });
  }

  /* Password visibility toggle (eye / eye-off icons swap via aria-pressed). */
  if (toggleBtn && passwordInput) {
    toggleBtn.addEventListener('click', function () {
      var show = passwordInput.type === 'password';
      passwordInput.type = show ? 'text' : 'password';
      toggleBtn.setAttribute('aria-pressed', String(show));
      toggleBtn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
  }

  /* Card-swipe login detection for admin/manager cards. */
  (function initCardSwipeLogin() {
    if (!form || !cardNumberInput) return;

    var keyBuffer = '';
    var lastKeyTime = 0;
    var swipeTimer = null;
    var SWIPE_TIMEOUT_MS = 350;
    var SWIPE_MIN_LENGTH = 8;

    function extractCardNumber(buffer) {
      var digits = buffer.replace(/\D/g, '');
      return digits.length >= SWIPE_MIN_LENGTH ? digits : null;
    }

    function onSwipeDetected(raw) {
      var cardNum = extractCardNumber(raw);
      if (!cardNum) return;

      cardNumberInput.value = cardNum;
      // Clear username/password so the server knows this is a card login.
      if (usernameInput) {
        usernameInput.value = '';
        usernameInput.removeAttribute('required');
      }
      if (passwordInput) {
        passwordInput.value = '';
        passwordInput.removeAttribute('required');
      }
      if (swipeStatus) swipeStatus.style.display = 'block';

      form.submit();
    }

    document.addEventListener('keydown', function (e) {
      var active = document.activeElement;
      var inCredentialField = active === usernameInput || active === passwordInput;
      var hasCredentials = Boolean(
        (usernameInput && usernameInput.value) || (passwordInput && passwordInput.value)
      );
      // Typing credentials never feeds the swipe buffer.
      if (inCredentialField && hasCredentials) {
        keyBuffer = '';
        if (swipeTimer) {
          clearTimeout(swipeTimer);
          swipeTimer = null;
        }
        return;
      }

      var now = Date.now();
      if (now - lastKeyTime > SWIPE_TIMEOUT_MS) {
        keyBuffer = '';
      }
      lastKeyTime = now;

      if (e.key.length === 1) {
        keyBuffer += e.key;
      }

      if (swipeTimer) {
        clearTimeout(swipeTimer);
        swipeTimer = null;
      }

      // Enter with a buffered card -> immediate submit.
      if (e.key === 'Enter' && keyBuffer.length >= SWIPE_MIN_LENGTH) {
        if (extractCardNumber(keyBuffer)) {
          onSwipeDetected(keyBuffer);
        }
        keyBuffer = '';
        e.preventDefault();
        return;
      }

      // Oversized buffer (track data with prefix/suffix garbage) -> submit.
      if (keyBuffer.length > 120) {
        if (extractCardNumber(keyBuffer)) {
          onSwipeDetected(keyBuffer);
        }
        keyBuffer = '';
        e.preventDefault();
        return;
      }

      // Quiet swipe: flush the buffer once keystrokes stop.
      swipeTimer = setTimeout(function () {
        if (keyBuffer.length >= SWIPE_MIN_LENGTH && extractCardNumber(keyBuffer)) {
          onSwipeDetected(keyBuffer);
        }
        keyBuffer = '';
        swipeTimer = null;
      }, SWIPE_TIMEOUT_MS + 80);

      // Safety cap for pathological input.
      if (keyBuffer.length > 500) {
        keyBuffer = keyBuffer.slice(-200);
      }
    });
  })();
})();
