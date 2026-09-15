/* =============================================================================
   core/keyboard.js — global on-screen keyboard (V1 static/js/keyboard.js port)
   -----------------------------------------------------------------------------
   API parity with V1 (settlement and legacy flows depend on it):
       window.openKeyboard(initialValue, onConfirm, onLiveUpdate, options)
       window.formatNumberWithCommas(valStr)
   V2 pages import the named exports instead of the window globals.

   options: {
     layout: 'full' | 'numeric' | 'cash',
     cashPresets: ['100', ...],            // quick-cash chips (defaults V1 set)
     presetMode: 'add' | 'set',            // chip behavior (default 'add')
     exactAmount: '1234.00',               // shows the EXACT AMOUNT action
     exactLabel: 'EXACT AMOUNT',
     displayLabel: 'RECEIVED',             // cash layout display caption
     sectionTitle: 'QUICK CASH',
     emptyDisplay: '0.00',
     integerOnly: true,                    // numeric/cash: no decimal point
     users: [{ id, username, role }],      // user-pick mode with live filter
     onUserSelect: fn(user),
     onCancel: fn,
   }

   Layouts: 'full' = 48-key alpha layout (shift latch + auto-release),
   'numeric' = numpad grid, 'cash' = numpad + quick-cash presets + actions.
   Physical keys drive the active keyboard (Backspace/Enter/Escape/chars),
   numeric layouts reject non-numeric input. The modal DOM is built once into
   #modal-host (or <body>), so pages need no keyboard markup; CSS lives in
   css/core/keyboard.css (loaded by the pages that use it).
   ========================================================================== */
'use strict';

const CASH_PRESETS_DEFAULT = ['100', '500', '1000', '1500', '2000', '5000'];

/* ---- DOM (built lazily on first open) ------------------------------------- */
let keyboardModal = null;
let keyboardDisplay = null;
let keyboardKeys = null;
let keyboardCancel = null;
let keyboardOk = null;
let keyboardUserResults = null;

/* ---- State ---------------------------------------------------------------- */
let currentInput = '';
let onConfirmCallback = null;
let onLiveUpdateCallback = null;
let onCancelCallback = null;
let onUserSelectCallback = null;
let allUsers = [];
let isInitialized = false;
let shiftActive = false;
let currentLayout = 'full';
let cashPresets = CASH_PRESETS_DEFAULT.slice();
let currentOptions = null;
let focusReturnTarget = null;

const keysLower = [
  '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '+',
  'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '(', ')',
  'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', '=', '%', '⌫',
  '⇧', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '↵', 'SPACE'
];

const keysUpper = [
  '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '_', '+',
  'Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', '{', '}',
  'A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', ':', '"', '⌫',
  '⇧', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', '!', '?', '↵', 'SPACE'
];

export function formatNumberWithCommas(valStr) {
  if (valStr === null || valStr === undefined || valStr === '') return '';
  const str = String(valStr);
  const parts = str.split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return parts.join('.');
}

/* ---- DOM construction ----------------------------------------------------- */
function host() {
  let el = document.getElementById('modal-host');
  if (!el) {
    el = document.createElement('div');
    el.id = 'modal-host';
    document.body.appendChild(el);
  }
  return el;
}

function buildDom() {
  const modal = document.createElement('div');
  modal.className = 'keyboard-modal';
  modal.id = 'keyboardModal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'On-screen keyboard');

  const content = document.createElement('div');
  content.className = 'keyboard-content';

  const display = document.createElement('div');
  display.className = 'keyboard-display';
  display.id = 'keyboardDisplay';
  display.tabIndex = -1;

  const keys = document.createElement('div');
  keys.className = 'keyboard-keys';
  keys.id = 'keyboardKeys';

  const userResults = document.createElement('div');
  userResults.className = 'keyboard-user-results';
  userResults.id = 'keyboardUserResults';

  const actions = document.createElement('div');
  actions.className = 'keyboard-actions';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'keyboard-btn keyboard-cancel';
  cancelBtn.id = 'keyboardCancel';
  cancelBtn.textContent = 'Cancel';
  const okBtn = document.createElement('button');
  okBtn.type = 'button';
  okBtn.className = 'keyboard-btn keyboard-ok';
  okBtn.id = 'keyboardOk';
  okBtn.textContent = 'OK';
  actions.appendChild(cancelBtn);
  actions.appendChild(okBtn);

  content.appendChild(display);
  content.appendChild(keys);
  content.appendChild(userResults);
  content.appendChild(actions);
  modal.appendChild(content);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      if (currentLayout === 'cash' || currentLayout === 'numeric') {
        closeCashLayout();
      }
      closeKeyboard(true);
    }
  });
  host().appendChild(modal);

  keyboardModal = modal;
  keyboardDisplay = display;
  keyboardKeys = keys;
  keyboardUserResults = userResults;
  keyboardCancel = cancelBtn;
  keyboardOk = okBtn;
}

function init() {
  if (isInitialized) return;
  buildDom();
  generateKeys();

  if (keyboardCancel) {
    keyboardCancel.addEventListener('click', () => closeKeyboard(true));
  }
  if (keyboardOk) {
    keyboardOk.addEventListener('click', () => {
      if (onConfirmCallback) onConfirmCallback(currentInput.trim());
      closeKeyboard();
    });
  }
  isInitialized = true;
}

/* ---- Key generation ------------------------------------------------------- */
function generateKeys() {
  if (!keyboardKeys) return;

  if (currentLayout === 'cash' || currentLayout === 'numeric') {
    buildCashLayout();
    return;
  }

  const layout = shiftActive ? keysUpper : keysLower;
  keyboardKeys.innerHTML = '';
  layout.forEach((key) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'keyboard-key';

    if (key === 'SPACE') {
      btn.textContent = 'SPACE';
      btn.classList.add('space');
    } else if (key === '⇧') {
      btn.textContent = '⇧ Shift';
      btn.classList.add('shift-key');
      if (shiftActive) btn.classList.add('active');
    } else if (key === '⌫') {
      btn.textContent = '⌫';
      btn.classList.add('backspace-key', 'wide');
    } else if (key === '↵') {
      btn.textContent = '↵';
      btn.classList.add('enter-key', 'wide');
    } else {
      btn.textContent = key;
    }

    btn.addEventListener('click', () => handleKey(key));
    keyboardKeys.appendChild(btn);
  });
}

/* ---- Cash / numeric layout ------------------------------------------------ */
function buildCashLayout() {
  const content = keyboardModal.querySelector('.keyboard-content');
  if (!content) return;

  if (keyboardDisplay) keyboardDisplay.style.display = 'none';
  if (keyboardKeys) keyboardKeys.style.display = 'none';
  const actionsEl = content.querySelector('.keyboard-actions');
  if (actionsEl) actionsEl.style.display = 'none';
  if (keyboardUserResults) keyboardUserResults.style.display = 'none';

  const existingCash = content.querySelector('.keyboard-cash-layout');
  if (existingCash) existingCash.remove();

  const exactAmount = currentOptions && currentOptions.exactAmount != null
    ? String(currentOptions.exactAmount)
    : null;
  const displayLabel = currentOptions && currentOptions.displayLabel
    ? String(currentOptions.displayLabel)
    : 'RECEIVED';
  const sectionTitleText = currentOptions && currentOptions.sectionTitle
    ? String(currentOptions.sectionTitle)
    : 'QUICK CASH';
  const emptyText = currentOptions && currentOptions.emptyDisplay != null
    ? String(currentOptions.emptyDisplay)
    : '0.00';

  const wrapper = document.createElement('div');
  wrapper.className = 'keyboard-cash-layout';

  /* Left column: display + numpad. */
  const leftCol = document.createElement('div');
  leftCol.className = 'keyboard-cash-col keyboard-cash-col-left';

  const displayBox = document.createElement('div');
  displayBox.className = 'keyboard-cash-display-box';
  const labelEl = document.createElement('div');
  labelEl.className = 'keyboard-cash-display-label';
  labelEl.textContent = displayLabel;
  const valueWrap = document.createElement('div');
  valueWrap.className = 'keyboard-cash-display-value-wrap';
  const valueEl = document.createElement('span');
  valueEl.className = 'keyboard-cash-display-value';
  valueEl.id = 'cashKbDisplayVal';
  valueEl.textContent = emptyText;
  const cursorEl = document.createElement('span');
  cursorEl.className = 'keyboard-cursor';
  valueWrap.appendChild(valueEl);
  valueWrap.appendChild(cursorEl);
  displayBox.appendChild(labelEl);
  displayBox.appendChild(valueWrap);
  leftCol.appendChild(displayBox);

  const numpadGrid = document.createElement('div');
  numpadGrid.className = 'keyboard-cash-numpad-grid';
  const numKeys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'CLEAR', '0', '⌫'];
  numKeys.forEach((key) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'keyboard-cash-numpad-key';
    if (key === '⌫') {
      btn.textContent = '⌫';
      btn.classList.add('cash-backspace-key');
    } else if (key === 'CLEAR') {
      btn.textContent = 'C';
      btn.classList.add('cash-clear-key');
    } else {
      btn.textContent = key;
    }
    btn.addEventListener('click', () => handleCashNumpadKey(key));
    numpadGrid.appendChild(btn);
  });
  leftCol.appendChild(numpadGrid);

  /* Right column: presets + actions. */
  const rightCol = document.createElement('div');
  rightCol.className = 'keyboard-cash-col keyboard-cash-col-right';

  const sectionTitle = document.createElement('div');
  sectionTitle.className = 'keyboard-cash-section-title';
  sectionTitle.textContent = sectionTitleText;
  rightCol.appendChild(sectionTitle);

  const presetsGrid = document.createElement('div');
  presetsGrid.className = 'keyboard-cash-presets-grid';
  cashPresets.forEach((amount) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'keyboard-cash-preset-btn';
    btn.textContent = formatNumberWithCommas(amount);
    btn.addEventListener('click', () => {
      if (currentOptions && currentOptions.presetMode === 'set') {
        currentInput = amount;
      } else {
        const current = parseFloat(currentInput) || 0;
        const sum = current + parseFloat(amount);
        currentInput = Number.isInteger(sum) ? String(sum) : sum.toFixed(2);
      }
      updateCashDisplay();
      notifyLiveUpdate();
    });
    presetsGrid.appendChild(btn);
  });
  rightCol.appendChild(presetsGrid);

  /* Bottom group: exact amount (full-width) + OK/CANCEL row. */
  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'keyboard-cash-action-btns';

  /* Exact amount: full-width block above OK/CANCEL row. */
  if (exactAmount) {
    const exactBtn = document.createElement('button');
    exactBtn.type = 'button';
    exactBtn.className = 'keyboard-cash-exact-btn';
    exactBtn.textContent = currentOptions && currentOptions.exactLabel
      ? String(currentOptions.exactLabel)
      : 'EXACT AMOUNT';
    exactBtn.addEventListener('click', () => {
      currentInput = exactAmount;
      updateCashDisplay();
      notifyLiveUpdate();
    });
    actionsDiv.appendChild(exactBtn);
  }

  const btnRow = document.createElement('div');
  btnRow.className = 'keyboard-cash-ok-cancel-row';

  const okBtn = document.createElement('button');
  okBtn.type = 'button';
  okBtn.className = 'keyboard-cash-action-btn btn-cash-ok';
  okBtn.textContent = 'OK';
  okBtn.addEventListener('click', () => {
    if (onConfirmCallback) onConfirmCallback(currentInput.trim());
    closeCashLayout();
    closeKeyboard();
  });
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'keyboard-cash-action-btn btn-cash-cancel';
  cancelBtn.textContent = 'CANCEL';
  cancelBtn.addEventListener('click', () => {
    closeCashLayout();
    closeKeyboard(true);
  });
  btnRow.appendChild(okBtn);
  btnRow.appendChild(cancelBtn);
  actionsDiv.appendChild(btnRow);

  rightCol.appendChild(actionsDiv);

  wrapper.appendChild(leftCol);
  wrapper.appendChild(rightCol);
  content.appendChild(wrapper);

  updateCashDisplay();
}

function closeCashLayout() {
  const content = keyboardModal && keyboardModal.querySelector('.keyboard-content');
  if (!content) return;
  const cashLayout = content.querySelector('.keyboard-cash-layout');
  if (cashLayout) cashLayout.remove();
  if (keyboardDisplay) keyboardDisplay.style.display = '';
  if (keyboardKeys) keyboardKeys.style.display = '';
  const actionsEl = content.querySelector('.keyboard-actions');
  if (actionsEl) actionsEl.style.display = '';
}

function updateCashDisplay() {
  const el = document.getElementById('cashKbDisplayVal');
  if (!el) return;
  if (!currentInput) {
    el.textContent = currentOptions && currentOptions.emptyDisplay != null
      ? String(currentOptions.emptyDisplay)
      : '0.00';
    return;
  }
  el.textContent = formatNumberWithCommas(currentInput);
}

function handleCashNumpadKey(key) {
  if (key === '⌫') {
    currentInput = currentInput.slice(0, -1);
  } else if (key === 'CLEAR') {
    currentInput = '';
  } else if (/^\d$/.test(key)) {
    if (currentInput.includes('.')) {
      const decimalPart = currentInput.split('.')[1];
      if (decimalPart && decimalPart.length >= 2) return;
    }
    currentInput += key;
  }
  updateCashDisplay();
  notifyLiveUpdate();
}

/* ---- Full layout input ---------------------------------------------------- */
function handleKey(key) {
  if (key === '⇧') {
    shiftActive = !shiftActive;
    generateKeys();
    return;
  } else if (key === '⌫') {
    currentInput = currentInput.slice(0, -1);
  } else if (key === '↵') {
    currentInput += '\n';
  } else if (key === 'SPACE') {
    currentInput += ' ';
  } else {
    currentInput += key;
    if (shiftActive) {
      shiftActive = false;
      generateKeys();
    }
  }
  updateDisplay();

  if (allUsers.length > 0) updateUserResults(currentInput);
  if (onLiveUpdateCallback) onLiveUpdateCallback(currentInput);
}

function refreshLayoutDisplay() {
  if (currentLayout === 'cash' || currentLayout === 'numeric') {
    updateCashDisplay();
  } else {
    updateDisplay();
  }
}

function notifyLiveUpdate() {
  if (allUsers.length > 0) updateUserResults(currentInput);
  if (onLiveUpdateCallback) onLiveUpdateCallback(currentInput);
}

function updateDisplay() {
  if (!keyboardDisplay) return;
  const text = currentInput || '';
  keyboardDisplay.innerHTML = '';
  const value = document.createElement('span');
  value.className = 'keyboard-display-value';
  value.textContent = text;
  const cursor = document.createElement('span');
  cursor.className = 'keyboard-cursor';
  keyboardDisplay.appendChild(value);
  keyboardDisplay.appendChild(cursor);
}

/* ---- Public API ----------------------------------------------------------- */
export function openKeyboard(initialValue, onConfirm, onLiveUpdate, options) {
  init();

  if (!keyboardModal) {
    console.error('Keyboard modal could not be created.');
    return;
  }

  currentOptions = options || null;
  currentInput = initialValue || '';
  onConfirmCallback = onConfirm;
  onLiveUpdateCallback = onLiveUpdate;
  onCancelCallback = options && typeof options.onCancel === 'function' ? options.onCancel : null;
  shiftActive = false;
  currentLayout = options && options.layout ? options.layout : 'full';
  cashPresets = options && Array.isArray(options.cashPresets) && options.cashPresets.length
    ? options.cashPresets.map(String)
    : CASH_PRESETS_DEFAULT.slice();

  if (options && options.users) {
    allUsers = options.users;
    onUserSelectCallback = options.onUserSelect || null;
    if (keyboardUserResults) {
      keyboardUserResults.style.display = 'block';
      updateUserResults(currentInput);
    }
  } else {
    allUsers = [];
    onUserSelectCallback = null;
    if (keyboardUserResults) {
      keyboardUserResults.style.display = 'none';
      keyboardUserResults.innerHTML = '';
    }
  }

  updateDisplay();
  generateKeys();
  keyboardModal.classList.remove('closing');
  keyboardModal.classList.add('active');

  focusReturnTarget = document.activeElement;
  if (keyboardDisplay) keyboardDisplay.focus({ preventScroll: true });
}

function updateUserResults(searchValue) {
  if (!keyboardUserResults || allUsers.length === 0) return;

  const searchTerm = (searchValue || '').toLowerCase().trim();
  const filteredUsers = searchTerm === ''
    ? allUsers
    : allUsers.filter((u) =>
        u.username.toLowerCase().includes(searchTerm) ||
        (u.role || '').toLowerCase().includes(searchTerm)
      );

  keyboardUserResults.innerHTML = '';
  if (filteredUsers.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'keyboard-user-empty';
    empty.textContent = 'No users found';
    keyboardUserResults.appendChild(empty);
    return;
  }

  filteredUsers.forEach((user) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'keyboard-user-btn';
    btn.dataset.userId = String(user.id);
    btn.dataset.username = user.username;

    const avatar = document.createElement('span');
    avatar.className = 'keyboard-user-avatar';
    avatar.textContent = (user.username || '?').charAt(0).toUpperCase();

    const copy = document.createElement('span');
    copy.className = 'keyboard-user-copy';
    const nameEl = document.createElement('span');
    nameEl.className = 'keyboard-user-name';
    nameEl.textContent = user.username;
    const roleEl = document.createElement('span');
    roleEl.className = 'keyboard-user-role';
    roleEl.textContent = user.role || '';
    copy.appendChild(nameEl);
    copy.appendChild(roleEl);

    btn.appendChild(avatar);
    btn.appendChild(copy);
    btn.addEventListener('click', () => {
      const user = allUsers.find((u) => String(u.id) === String(btn.dataset.userId));
      if (user && onUserSelectCallback) onUserSelectCallback(user);
    });
    keyboardUserResults.appendChild(btn);
  });
}

function closeKeyboard(wasCancelled = false) {
  if (!keyboardModal) return;

  if (wasCancelled && onCancelCallback) onCancelCallback();

  if (currentLayout === 'cash' || currentLayout === 'numeric') {
    closeCashLayout();
  }

  keyboardModal.classList.add('closing');
  keyboardModal.classList.remove('active');

  window.setTimeout(() => {
    keyboardModal.classList.remove('closing');
    currentInput = '';
    onConfirmCallback = null;
    onLiveUpdateCallback = null;
    onCancelCallback = null;
    onUserSelectCallback = null;
    allUsers = [];
    currentLayout = 'full';
    currentOptions = null;
    cashPresets = CASH_PRESETS_DEFAULT.slice();
    if (keyboardKeys) keyboardKeys.classList.remove('keyboard-keys-numpad');
    if (keyboardUserResults) {
      keyboardUserResults.style.display = 'none';
      keyboardUserResults.innerHTML = '';
    }
  }, 220);

  if (focusReturnTarget && typeof focusReturnTarget.focus === 'function') {
    if (focusReturnTarget.id !== 'cashReceived' && focusReturnTarget.id !== 'totalNoPax') {
      focusReturnTarget.focus({ preventScroll: true });
    }
  }
  focusReturnTarget = null;
}

/* ---- Physical keyboard while the modal is active -------------------------- */
document.addEventListener('keydown', (e) => {
  if (!keyboardModal || !keyboardModal.classList.contains('active')) return;

  e.preventDefault();

  if (e.key === 'Backspace') {
    currentInput = currentInput.slice(0, -1);
    refreshLayoutDisplay();
    notifyLiveUpdate();
  } else if (e.key === 'Enter') {
    if (onConfirmCallback) onConfirmCallback(currentInput.trim());
    if (currentLayout === 'cash') closeCashLayout();
    closeKeyboard();
    return;
  } else if (e.key === 'Escape') {
    if (currentLayout === 'cash') closeCashLayout();
    closeKeyboard(true);
    return;
  } else if (e.key.length === 1) {
    if ((currentLayout === 'numeric' || currentLayout === 'cash') && !/[\d.]/.test(e.key)) {
      return;
    }
    const integerOnly = currentOptions && currentOptions.integerOnly === true;
    if ((currentLayout === 'numeric' || currentLayout === 'cash') && e.key === '.' && (currentInput.includes('.') || integerOnly)) {
      return;
    }
    currentInput += e.key;
    refreshLayoutDisplay();
    notifyLiveUpdate();
  }
});

/* V1 window-global parity: legacy inline flows call these directly. */
window.formatNumberWithCommas = formatNumberWithCommas;
window.openKeyboard = openKeyboard;

export default { openKeyboard, formatNumberWithCommas };
