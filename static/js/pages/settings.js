/* =============================================================================
   js/pages/settings.js — Store & Hardware Settings (Phase 5 Overhaul)
   =============================================================================
   SPA contract: default export { mount, destroy }.
   ========================================================================== */
'use strict';

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';

let rootEl = null;
let destroyFns = [];

const SETTINGS_TAB_KEY = 'pos_settings_active_tab';

/* Settings Sidebar Navigation */
function initSettingsNavigation(el) {
  const switchSection = (targetTab, updateHash = true) => {
    if (!targetTab) return;

    const allPanes = document.querySelectorAll('.settings-pane');
    allPanes.forEach((p) => p.classList.remove('active'));

    const targetPane = document.querySelector(`#pane-${targetTab}`);
    if (targetPane) {
      targetPane.classList.add('active');
    }

    let activeSubItem = null;
    document.querySelectorAll('.side-dropdown-item').forEach((subItem) => {
      const isActive = subItem.dataset.tab === targetTab;
      subItem.classList.toggle('is-active', isActive);
      if (isActive) {
        subItem.setAttribute('aria-current', 'page');
        activeSubItem = subItem;
      } else {
        subItem.removeAttribute('aria-current');
      }
    });

    const dropdown = document.querySelector('.side-dropdown[data-side-dropdown="settings"]');
    if (dropdown) {
      dropdown.classList.add('is-open', 'is-active');
      const toggle = dropdown.querySelector('.side-dropdown-toggle');
      if (toggle) {
        toggle.classList.add('is-active');
        toggle.setAttribute('aria-expanded', 'true');
      }
    }

    if (activeSubItem) {
      activeSubItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      setTimeout(() => {
        try {
          activeSubItem.focus({ preventScroll: true });
        } catch (e) {}
      }, 50);
    }

    try {
      localStorage.setItem(SETTINGS_TAB_KEY, targetTab);
    } catch (e) {}

    if (updateHash && window.history && window.history.replaceState) {
      window.history.replaceState(null, '', `#${targetTab}`);
    }
  };

  window.switchSettingsSection = switchSection;

  const determineTab = () => {
    let hash = window.location.hash ? window.location.hash.replace('#', '').replace('pane-', '') : '';
    if (!hash && window.location.href.includes('#')) {
      hash = (window.location.href.split('#')[1] || '').replace('pane-', '');
    }
    if (hash && document.querySelector(`#pane-${hash}`)) {
      return hash;
    }
    try {
      const saved = localStorage.getItem(SETTINGS_TAB_KEY);
      if (saved && document.querySelector(`#pane-${saved}`)) {
        return saved;
      }
    } catch (e) {}
    return 'receipt';
  };

  const initialTab = determineTab();
  switchSection(initialTab, false);

  const onHashChange = () => {
    const hashTab = (window.location.hash || '').replace('#', '').replace('pane-', '');
    if (hashTab && document.querySelector(`#pane-${hashTab}`)) {
      switchSection(hashTab, false);
    }
  };
  window.addEventListener('hashchange', onHashChange);
  destroyFns.push(() => window.removeEventListener('hashchange', onHashChange));
}

/* Interactive Chip Toggling */
function initChips(container) {
  if (!container) return;
  const chips = container.querySelectorAll('.settings-chip');
  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      chip.classList.toggle('active');
    });
  });
}

function getChipValues(container) {
  if (!container) return [];
  const activeChips = container.querySelectorAll('.settings-chip.active');
  return Array.from(activeChips).map((c) => c.dataset.value);
}

function setChipValues(container, values) {
  if (!container || !Array.isArray(values)) return;
  const chips = container.querySelectorAll('.settings-chip');
  chips.forEach((chip) => {
    if (values.includes(chip.dataset.value)) {
      chip.classList.add('active');
    } else {
      chip.classList.remove('active');
    }
  });
}

/* Theme Selection Cards */
function initThemeCards(el) {
  const lightCard = el.querySelector('#themeCardLight');
  const darkCard = el.querySelector('#themeCardDark');

  const getThemeMode = () => {
    if (window.POS && window.POS.theme) return window.POS.theme.get();
    return document.documentElement.getAttribute('data-theme') || 'dark';
  };

  const setThemeMode = (mode) => {
    if (window.POS && window.POS.theme) {
      window.POS.theme.set(mode);
    } else {
      document.documentElement.setAttribute('data-theme', mode);
    }
  };

  const updateActiveThemeUI = () => {
    const currentTheme = getThemeMode() || 'dark';
    if (lightCard) {
      if (currentTheme === 'light') lightCard.classList.add('active');
      else lightCard.classList.remove('active');
    }
    if (darkCard) {
      if (currentTheme === 'dark') darkCard.classList.add('active');
      else darkCard.classList.remove('active');
    }
  };

  updateActiveThemeUI();

  if (lightCard) {
    lightCard.addEventListener('click', () => {
      setThemeMode('light');
      updateActiveThemeUI();
      notify.info('Theme set to Light mode.');
    });
  }

  if (darkCard) {
    darkCard.addEventListener('click', () => {
      setThemeMode('dark');
      updateActiveThemeUI();
      notify.info('Theme set to Dark mode.');
    });
  }
}

/* Live Receipt Paper Preview */
function initReceiptPreview(el) {
  const storeInput = el.querySelector('#giStoreName') || el.querySelector('#cfgReceiptStoreName');
  const tinInput = el.querySelector('#cfgTin');
  const ptuInput = el.querySelector('#cfgPtu');
  const ptuIssuedInput = el.querySelector('#cfgPtuIssued');
  const branchInput = el.querySelector('#cfgBranchName');
  const addrInput = el.querySelector('#cfgStoreAddress') || el.querySelector('#giStoreLocation');
  const minInput = el.querySelector('#cfgMin');
  const serialInput = el.querySelector('#cfgSerial');
  const footerInput = el.querySelector('#cfgFooterLines');

  const prevStore = el.querySelector('#prevStoreName');
  const prevTin = el.querySelector('#prevTin');
  const prevPtu = el.querySelector('#prevPtu');
  const prevPtuIssued = el.querySelector('#prevPtuIssued');
  const prevBranch = el.querySelector('#prevBranchName');
  const prevAddr = el.querySelector('#prevAddress');
  const prevMin = el.querySelector('#prevMin');
  const prevSerial = el.querySelector('#prevSerial');
  const prevFooter = el.querySelector('#prevFooterMsg');

  const sync = () => {
    const storeVal = (el.querySelector('#giStoreName')?.value || el.querySelector('#cfgReceiptStoreName')?.value || '').trim();
    if (prevStore) prevStore.textContent = storeVal || 'SAVORE KITCHEN CORP';
    if (prevTin && tinInput) prevTin.textContent = tinInput.value.trim() ? (tinInput.value.trim().toUpperCase().includes('TIN') ? tinInput.value.trim() : `VAT REG. TIN ${tinInput.value.trim()}`) : 'VAT REG. TIN 681-242-294-00000';
    if (prevPtu && ptuInput) prevPtu.textContent = ptuInput.value.trim() ? (ptuInput.value.trim().toUpperCase().includes('PTU') ? ptuInput.value.trim() : `PTU NO: ${ptuInput.value.trim()}`) : 'PTU NO: FP032026-088-0596274-000000';
    if (prevPtuIssued && ptuIssuedInput) prevPtuIssued.textContent = ptuIssuedInput.value.trim() ? (ptuIssuedInput.value.trim().toUpperCase().includes('ISSUED') ? ptuIssuedInput.value.trim() : `DATE ISSUED: ${ptuIssuedInput.value.trim()}`) : 'DATE ISSUED: 03/26/2026';
    if (prevBranch && branchInput) prevBranch.textContent = branchInput.value.trim() || 'LEVEL 1 STALL #126 ROBINSONS NORTH';
    if (prevAddr && addrInput) prevAddr.textContent = addrInput.value.trim() || 'BRGY 91 ABUCAY TACLOBAN CITY';
    if (prevMin && minInput) prevMin.textContent = minInput.value.trim() ? (minInput.value.trim().toUpperCase().includes('MIN') ? minInput.value.trim() : `MIN: ${minInput.value.trim()}`) : 'MIN: 2603271052400600';
    if (prevSerial && serialInput) prevSerial.textContent = serialInput.value.trim() ? (serialInput.value.trim().toUpperCase().includes('SN') ? serialInput.value.trim() : `SN: ${serialInput.value.trim()}`) : 'SN: 1F250421014035';
    if (prevFooter && footerInput) prevFooter.textContent = footerInput.value.trim() || 'Thank you for dining with us!';
  };

  [
    el.querySelector('#giStoreName'),
    el.querySelector('#cfgReceiptStoreName'),
    tinInput,
    ptuInput,
    ptuIssuedInput,
    branchInput,
    addrInput,
    minInput,
    serialInput,
    footerInput,
  ].forEach((inp) => {
    if (inp) inp.addEventListener('input', sync);
  });

  const testPrintBtn = el.querySelector('#btnTestPrintReceipt');
  if (testPrintBtn) {
    testPrintBtn.addEventListener('click', async () => {
      testPrintBtn.disabled = true;
      const originalHTML = testPrintBtn.innerHTML;
      testPrintBtn.innerHTML = '<span>Printing...</span>';

      const payload = {
        store_name: (el.querySelector('#cfgReceiptStoreName') || {}).value || '',
        tin: (el.querySelector('#cfgTin') || {}).value || '',
        ptu_no: (el.querySelector('#cfgPtu') || {}).value || '',
        ptu_date_issued: (el.querySelector('#cfgPtuIssued') || {}).value || '',
        branch_name: (el.querySelector('#cfgBranchName') || {}).value || '',
        store_location: (el.querySelector('#cfgStoreAddress') || {}).value || '',
        min: (el.querySelector('#cfgMin') || {}).value || '',
        serial_number: (el.querySelector('#cfgSerial') || {}).value || '',
        thank_you_message: (el.querySelector('#cfgFooterLines') || {}).value || '',
      };

      try {
        const res = await api.post('/api/test_print_receipt', payload);
        if (res && res.success) {
          notify.success(res.message || 'Receipt preview test slip printed successfully.');
        } else {
          notify.error((res && res.message) || 'Printer offline or unavailable.');
        }
      } catch (err) {
        notify.error('Network error sending test print.');
      } finally {
        testPrintBtn.disabled = false;
        testPrintBtn.innerHTML = originalHTML;
      }
    });
  }

  return sync;
}

/* Printer Connectivity Status Check */
async function checkPrinterStatus(el) {
  const cashierChip = el.querySelector('#cashierStatusChip');
  const kitchenChip = el.querySelector('#kitchenStatusChip');
  const cashierText = el.querySelector('#cashierStatusText');
  const kitchenText = el.querySelector('#kitchenStatusText');

  const cardCashierHealth = el.querySelector('#cardCashierHealth');
  const cardKitchenHealth = el.querySelector('#cardKitchenHealth');
  const cashierActiveTarget = el.querySelector('#cashierActiveTarget');
  const kitchenActiveTarget = el.querySelector('#kitchenActiveTarget');

  if (cashierChip) cashierChip.dataset.state = 'checking';
  if (kitchenChip) kitchenChip.dataset.state = 'checking';
  if (cardCashierHealth) cardCashierHealth.dataset.state = 'checking';
  if (cardKitchenHealth) cardKitchenHealth.dataset.state = 'checking';

  if (cashierText) cashierText.textContent = 'Cashier: Checking...';
  if (kitchenText) kitchenText.textContent = 'Kitchen: Checking...';

  try {
    const [cashierRes, kitchenRes] = await Promise.all([
      api.get('/check_printer_status?target=cashier&refresh=1').catch(() => null),
      api.get('/check_printer_status?target=kitchen&refresh=1').catch(() => null),
    ]);

    if (cashierRes) {
      const isConnected = Boolean(cashierRes.connected);
      const state = isConnected ? 'connected' : 'disconnected';
      if (cashierChip) cashierChip.dataset.state = state;
      if (cardCashierHealth) cardCashierHealth.dataset.state = state;
      if (cashierText) cashierText.textContent = isConnected ? 'Cashier: Online' : `Cashier: Offline`;
      if (cashierActiveTarget) {
        cashierActiveTarget.textContent = cashierRes.printer_name || cashierRes.printer_host || (isConnected ? 'Online ESC/POS' : 'Not Configured');
      }
    }

    if (kitchenRes) {
      const isConnected = Boolean(kitchenRes.connected);
      const state = isConnected ? 'connected' : 'disconnected';
      if (kitchenChip) kitchenChip.dataset.state = state;
      if (cardKitchenHealth) cardKitchenHealth.dataset.state = state;
      if (kitchenText) kitchenText.textContent = isConnected ? 'Kitchen: Online' : `Kitchen: Offline`;
      if (kitchenActiveTarget) {
        kitchenActiveTarget.textContent = kitchenRes.printer_name || kitchenRes.printer_host || (isConnected ? 'Online ESC/POS' : 'Not Configured');
      }
    }
  } catch (err) {
    if (cashierChip) cashierChip.dataset.state = 'disconnected';
    if (kitchenChip) kitchenChip.dataset.state = 'disconnected';
  }
}

/* Paper Choice Cards Interactive Selector */
function initPaperChoiceCards(el) {
  const cards = el.querySelectorAll('.hw-paper-card');
  const selectEl = el.querySelector('#cfgPrinterPrintSize');
  if (!cards.length) return null;

  const setChoice = (choice) => {
    const target = choice === 'compact' ? 'compact' : 'normal';
    cards.forEach((c) => {
      if (c.dataset.paperChoice === target) c.classList.add('active');
      else c.classList.remove('active');
    });
    if (selectEl) selectEl.value = target;
  };

  cards.forEach((c) => {
    c.addEventListener('click', () => {
      setChoice(c.dataset.paperChoice);
    });
  });

  return setChoice;
}

/* Load Settings from Server */
async function loadServerSettings(el, syncPreview) {
  try {
    const res = await api.get('/receipt_settings');
    if (res && res.success) {
      // General info
      const giStoreName = el.querySelector('#giStoreName');
      const giContactPerson = el.querySelector('#giContactPerson');
      const giStoreLocation = el.querySelector('#giStoreLocation');
      const giContactPhone = el.querySelector('#giContactPhone');
      const giContactEmail = el.querySelector('#giContactEmail');
      const giStoreDescription = el.querySelector('#giStoreDescription');

      if (giStoreName) giStoreName.value = res.store_name || '';
      if (giContactPerson) giContactPerson.value = res.contact_person || '';
      if (giStoreLocation) giStoreLocation.value = res.store_location || '';
      if (giContactPhone) giContactPhone.value = res.contact_phone || '';
      if (giContactEmail) giContactEmail.value = res.contact_email || '';
      if (giStoreDescription) giStoreDescription.value = res.store_description || '';

      // Store settings & chips
      const ssBusinessType = el.querySelector('#ssBusinessType');
      const ssOpenTime = el.querySelector('#ssOpenTime');
      const ssCloseTime = el.querySelector('#ssCloseTime');

      if (ssBusinessType) ssBusinessType.value = res.business_type || '';
      if (ssOpenTime) ssOpenTime.value = res.open_time || '';
      if (ssCloseTime) ssCloseTime.value = res.close_time || '';

      setChipValues(el.querySelector('#ssBusinessDays'), res.business_days || []);
      setChipValues(el.querySelector('#ssServiceTypes'), res.service_types || []);
      setChipValues(el.querySelector('#ssPaymentTypes'), res.payment_types || []);

      // Receipt Header - 8 Structured BIR Fields
      const cfgReceiptStoreName = el.querySelector('#cfgReceiptStoreName');
      const cfgTin = el.querySelector('#cfgTin');
      const cfgPtu = el.querySelector('#cfgPtu');
      const cfgPtuIssued = el.querySelector('#cfgPtuIssued');
      const cfgBranchName = el.querySelector('#cfgBranchName');
      const cfgStoreAddress = el.querySelector('#cfgStoreAddress');
      const cfgMin = el.querySelector('#cfgMin');
      const cfgSerial = el.querySelector('#cfgSerial');
      const cfgPrintSize = el.querySelector('#cfgPrintSize');

      if (cfgReceiptStoreName) cfgReceiptStoreName.value = res.store_name || (res.header_lines && res.header_lines[0]) || '';
      if (cfgBranchName) cfgBranchName.value = res.branch_name || '';
      if (cfgStoreAddress) cfgStoreAddress.value = res.store_location || '';
      if (cfgTin) cfgTin.value = res.tin || '';
      if (cfgMin) cfgMin.value = res.min || '';
      if (cfgSerial) cfgSerial.value = res.serial_number || '';
      if (cfgPtu) cfgPtu.value = res.ptu_no || '';
      if (cfgPtuIssued) cfgPtuIssued.value = res.ptu_date_issued || '';
      if (cfgPrintSize) cfgPrintSize.value = res.print_size || 'normal';

      // Parse legacy header_lines array in exact BIR sequence if specific fields are empty
      if (Array.isArray(res.header_lines) && res.header_lines.length > 0) {
        res.header_lines.forEach((line, idx) => {
          const lUpper = line.toUpperCase();
          if (cfgTin && !cfgTin.value && lUpper.includes('TIN')) {
            cfgTin.value = line;
          } else if (cfgPtu && !cfgPtu.value && lUpper.includes('PTU')) {
            cfgPtu.value = line;
          } else if (cfgPtuIssued && !cfgPtuIssued.value && lUpper.includes('ISSUED')) {
            cfgPtuIssued.value = line;
          } else if (cfgMin && !cfgMin.value && lUpper.includes('MIN')) {
            cfgMin.value = line;
          } else if (cfgSerial && !cfgSerial.value && (lUpper.includes('SN') || lUpper.includes('SERIAL'))) {
            cfgSerial.value = line;
          } else if (cfgBranchName && !cfgBranchName.value && (idx === 4 || lUpper.includes('STALL') || lUpper.includes('BRANCH') || lUpper.includes('LEVEL') || lUpper.includes('FLOOR'))) {
            cfgBranchName.value = line;
          } else if (cfgStoreAddress && !cfgStoreAddress.value && (idx === 5 || lUpper.includes('BRGY') || lUpper.includes('CITY') || lUpper.includes('AVE') || lUpper.includes('STREET') || lUpper.includes('ROAD'))) {
            cfgStoreAddress.value = line;
          }
        });
      }

      // Printers & Hardware
      const cfgCashierPrinterHost = el.querySelector('#cfgCashierPrinterHost');
      const cfgCashierPrinterPort = el.querySelector('#cfgCashierPrinterPort');
      const cfgCashierWindowsPrinter = el.querySelector('#cfgCashierWindowsPrinter');
      const cfgKitchenPrinterHost = el.querySelector('#cfgKitchenPrinterHost');
      const cfgKitchenPrinterPort = el.querySelector('#cfgKitchenPrinterPort');
      const cfgKitchenWindowsPrinter = el.querySelector('#cfgKitchenWindowsPrinter');
      const cfgPrinterRequired = el.querySelector('#cfgPrinterRequired');
      const cfgAutoDiscoverPrinters = el.querySelector('#cfgAutoDiscoverPrinters');
      const cfgPrinterPrintSize = el.querySelector('#cfgPrinterPrintSize');

      if (cfgCashierPrinterHost) cfgCashierPrinterHost.value = res.cashier_printer_host || '';
      if (cfgCashierPrinterPort) cfgCashierPrinterPort.value = res.cashier_printer_port || 9100;
      if (cfgCashierWindowsPrinter) cfgCashierWindowsPrinter.value = res.cashier_windows_printer_name || '';
      if (cfgKitchenPrinterHost) cfgKitchenPrinterHost.value = res.kitchen_printer_host || '';
      if (cfgKitchenPrinterPort) cfgKitchenPrinterPort.value = res.kitchen_printer_port || 9100;
      if (cfgKitchenWindowsPrinter) cfgKitchenWindowsPrinter.value = res.kitchen_windows_printer_name || '';
      if (cfgPrinterRequired) cfgPrinterRequired.checked = res.printer_required !== false;
      if (cfgAutoDiscoverPrinters) cfgAutoDiscoverPrinters.checked = res.auto_discover_network_printers !== false;
      
      const setPaperChoice = initPaperChoiceCards(el);
      if (setPaperChoice) {
        setPaperChoice(res.print_size || 'normal');
      } else if (cfgPrinterPrintSize) {
        cfgPrinterPrintSize.value = res.print_size || 'normal';
      }

      // Thank You & Footer
      const cfgFooterLines = el.querySelector('#cfgFooterLines');
      if (cfgFooterLines) {
        if (Array.isArray(res.footer_lines)) {
          cfgFooterLines.value = res.footer_lines.join('\n');
        } else {
          cfgFooterLines.value = res.thank_you_message || res.footer_lines || '';
        }
      }

      if (syncPreview) syncPreview();
    }
  } catch (err) {
    console.warn('Could not load receipt settings:', err);
  }

  try {
    const rlcRes = await api.get('/rlc_settings');
    if (rlcRes && rlcRes.success) {
      const s = rlcRes.settings || rlcRes;
      const srv = el.querySelector('#rlcServer');
      const port = el.querySelector('#rlcPort');
      const user = el.querySelector('#rlcUsername');
      const pwd = el.querySelector('#rlcPassword');
      const dir = el.querySelector('#rlcRemoteDir');
      const enabled = el.querySelector('#cfgRlcEnabled');

      if (srv) srv.value = (s.server === 'none' || s.server === 'disabled.local') ? '' : (s.server || '');
      if (port) port.value = s.port || 22;
      if (user) user.value = (s.username === 'none' || s.username === 'disabled') ? '' : (s.username || '');
      if (pwd) pwd.value = (s.password === 'none' || s.password === 'disabled') ? '' : (s.password || '');
      if (dir) dir.value = ((s.remote_path === '/IT_Tenants/' || s.remote_path === 'none') && !s.rlc_enabled) ? '' : (s.remote_path || s.remote_directory || '');
      if (enabled) enabled.checked = Boolean(s.rlc_enabled);
    }
  } catch (err) {
    console.warn('Could not load RLC settings:', err);
  }
}

/* Printer Network & Spooler Discovery Scanner */
function initPrinterScanner(el) {
  const btnScan = el.querySelector('#btnScanPrinters');
  const statusEl = el.querySelector('#printerScanStatus');
  const resultsEl = el.querySelector('#printerScanResults');

  if (!btnScan) return;

  btnScan.addEventListener('click', async () => {
    btnScan.disabled = true;
    const origHtml = btnScan.innerHTML;
    btnScan.innerHTML = `<span class="spinner-sm"></span> Scanning...`;

    if (statusEl) {
      statusEl.innerHTML = `<span class="status-scanning">Scanning Windows spoolers, USB devices, and LAN subnet printers...</span>`;
      statusEl.className = 'printer-scan-status scanning';
    }
    if (resultsEl) {
      resultsEl.style.display = 'none';
      resultsEl.innerHTML = '';
    }

    try {
      const res = await api.post('/discover_network_printers', {});
      if (res && res.success && Array.isArray(res.printers)) {
        const printers = res.printers;
        if (statusEl) {
          statusEl.innerHTML = `Found <strong>${printers.length}</strong> printer option(s).`;
          statusEl.className = 'printer-scan-status success';
        }
        renderScanResults(el, resultsEl, printers);
      } else {
        if (statusEl) {
          statusEl.textContent = (res && res.message) || 'Printer scan failed.';
          statusEl.className = 'printer-scan-status error';
        }
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = 'Error scanning network or local printers.';
        statusEl.className = 'printer-scan-status error';
      }
    } finally {
      btnScan.disabled = false;
      btnScan.innerHTML = origHtml;
    }
  });
}

function renderScanResults(root, resultsEl, printers) {
  if (!resultsEl) return;
  resultsEl.innerHTML = '';

  if (printers.length === 0) {
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = `<div class="printer-scan-empty">No Windows queue spoolers, direct USB, or LAN network printers were detected. You can manually enter your printer's IP address or Windows printer name below.</div>`;
    return;
  }

  resultsEl.style.display = 'grid';

  printers.forEach((p) => {
    const item = document.createElement('div');
    item.className = 'scan-result-card';

    let connBadge = '';
    if (p.connection_type === 'spooler') {
      connBadge = `<span class="scan-badge badge-spooler">Windows Spooler</span>`;
    } else if (p.connection_type === 'usb') {
      connBadge = `<span class="scan-badge badge-usb">Direct USB</span>`;
    } else {
      connBadge = `<span class="scan-badge badge-lan">Network LAN</span>`;
    }

    item.innerHTML = `
      <div class="scan-result-info">
        <div class="scan-result-top">
          ${connBadge}
          <strong class="scan-result-label">${p.label || p.windows_printer_name || p.host}</strong>
        </div>
        <div class="scan-result-details">
          ${p.host ? `<span>IP: ${p.host}:${p.port}</span>` : ''}
          ${p.windows_printer_name ? `<span>Queue: ${p.windows_printer_name}</span>` : ''}
          ${p.vendor_id ? `<span>VID: 0x${p.vendor_id.toString(16)} PID: 0x${p.product_id.toString(16)}</span>` : ''}
        </div>
      </div>
      <div class="scan-result-actions">
        <button type="button" class="scan-assign-btn btn-cashier" title="Assign as Cashier Printer">Assign Cashier</button>
        <button type="button" class="scan-assign-btn btn-kitchen" title="Assign as Kitchen Printer">Assign Kitchen</button>
      </div>
    `;

    const cashierBtn = item.querySelector('.btn-cashier');
    if (cashierBtn) {
      cashierBtn.addEventListener('click', () => {
        if (p.connection_type === 'spooler') {
          const inp = root.querySelector('#cfgCashierWindowsPrinter');
          if (inp) inp.value = p.windows_printer_name || '';
        } else if (p.connection_type === 'lan') {
          const hostInp = root.querySelector('#cfgCashierPrinterHost');
          const portInp = root.querySelector('#cfgCashierPrinterPort');
          if (hostInp) hostInp.value = p.host || '';
          if (portInp) portInp.value = p.port || 9100;
        } else if (p.connection_type === 'usb') {
          const winInp = root.querySelector('#cfgCashierWindowsPrinter');
          if (winInp) winInp.value = p.label || 'USB Thermal Printer';
        }
        notify.success(`Assigned "${p.label || p.windows_printer_name || p.host}" to Cashier Printer.`);
      });
    }

    const kitchenBtn = item.querySelector('.btn-kitchen');
    if (kitchenBtn) {
      kitchenBtn.addEventListener('click', () => {
        if (p.connection_type === 'spooler') {
          const inp = root.querySelector('#cfgKitchenWindowsPrinter');
          if (inp) inp.value = p.windows_printer_name || '';
        } else if (p.connection_type === 'lan') {
          const hostInp = root.querySelector('#cfgKitchenPrinterHost');
          const portInp = root.querySelector('#cfgKitchenPrinterPort');
          if (hostInp) hostInp.value = p.host || '';
          if (portInp) portInp.value = p.port || 9100;
        } else if (p.connection_type === 'usb') {
          const winInp = root.querySelector('#cfgKitchenWindowsPrinter');
          if (winInp) winInp.value = p.label || 'USB Thermal Printer';
        }
        notify.success(`Assigned "${p.label || p.windows_printer_name || p.host}" to Kitchen Printer.`);
      });
    }

    resultsEl.appendChild(item);
  });
}

export function mount(el) {
  rootEl = el || document.querySelector('#view-root') || document;
  destroyFns = [];

  initSettingsNavigation(rootEl);
  initChips(rootEl.querySelector('#ssBusinessDays'));
  initChips(rootEl.querySelector('#ssServiceTypes'));
  initChips(rootEl.querySelector('#ssPaymentTypes'));
  initThemeCards(rootEl);
  initPaperChoiceCards(rootEl);
  initPrinterScanner(rootEl);

  const syncPreview = initReceiptPreview(rootEl);
  loadServerSettings(rootEl, syncPreview);
  checkPrinterStatus(rootEl);

  /* Reload Printer Status button */
  const btnReloadPrinter = rootEl.querySelector('#btnReloadPrinterStatus');
  if (btnReloadPrinter) {
    btnReloadPrinter.addEventListener('click', () => {
      checkPrinterStatus(rootEl);
      notify.info('Checking cashier & kitchen printer status...');
    });
  }

  /* 1) General Info form submit */
  const generalForm = rootEl.querySelector('#generalInfoForm');
  if (generalForm) {
    generalForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSaveGeneralInfo');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const payload = {
        store_name: (rootEl.querySelector('#giStoreName') || {}).value || '',
        contact_person: (rootEl.querySelector('#giContactPerson') || {}).value || '',
        store_location: (rootEl.querySelector('#giStoreLocation') || {}).value || '',
        contact_phone: (rootEl.querySelector('#giContactPhone') || {}).value || '',
        contact_email: (rootEl.querySelector('#giContactEmail') || {}).value || '',
        store_description: (rootEl.querySelector('#giStoreDescription') || {}).value || '',
      };

      try {
        const res = await api.post('/receipt_settings', payload);
        if (res && res.success) {
          delete generalForm.dataset.dirty;
          notify.success('General store information updated successfully.');
          syncPreview();
        } else {
          notify.error((res && res.message) || 'Error saving general info.');
        }
      } catch (err) {
        notify.error('Network error saving settings.');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* 2) Store Settings form submit */
  const storeForm = rootEl.querySelector('#storeSettingsForm');
  if (storeForm) {
    storeForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSaveStoreSettings');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const payload = {
        business_type: (rootEl.querySelector('#ssBusinessType') || {}).value || '',
        open_time: (rootEl.querySelector('#ssOpenTime') || {}).value || '',
        close_time: (rootEl.querySelector('#ssCloseTime') || {}).value || '',
        business_days: getChipValues(rootEl.querySelector('#ssBusinessDays')),
        service_types: getChipValues(rootEl.querySelector('#ssServiceTypes')),
        payment_types: getChipValues(rootEl.querySelector('#ssPaymentTypes')),
      };

      try {
        const res = await api.post('/receipt_settings', payload);
        if (res && res.success) {
          delete storeForm.dataset.dirty;
          notify.success('Store operating profile updated successfully.');
        } else {
          notify.error((res && res.message) || 'Error saving store settings.');
        }
      } catch (err) {
        notify.error('Network error saving store settings.');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* 3) Receipt Header form submit */
  const receiptForm = rootEl.querySelector('#receiptSettingsForm');
  if (receiptForm) {
    receiptForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSaveReceiptSettings');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const storeName = (rootEl.querySelector('#cfgReceiptStoreName') || {}).value || '';
      const tinVal = (rootEl.querySelector('#cfgTin') || {}).value || '';
      const ptuVal = (rootEl.querySelector('#cfgPtu') || {}).value || '';
      const ptuIssuedVal = (rootEl.querySelector('#cfgPtuIssued') || {}).value || '';
      const branchName = (rootEl.querySelector('#cfgBranchName') || {}).value || '';
      const addressVal = (rootEl.querySelector('#cfgStoreAddress') || rootEl.querySelector('#giStoreLocation') || {}).value || '';
      const minVal = (rootEl.querySelector('#cfgMin') || {}).value || '';
      const serialVal = (rootEl.querySelector('#cfgSerial') || {}).value || '';

      const footerText = (rootEl.querySelector('#cfgFooterLines') || {}).value || '';
      const rawFooterLines = footerText.split('\n').map((l) => l.trim()).filter(Boolean);
      const thankYouMsg = rawFooterLines[0] || footerText.trim();
      const extraFooterLines = rawFooterLines.filter((line, idx) => idx > 0 && line.toLowerCase() !== thankYouMsg.toLowerCase());

      // Mandatory BIR 8-field header line sequence
      const headerLinesArray = [
        storeName,
        tinVal ? (tinVal.toUpperCase().includes('TIN') ? tinVal : `VAT REG. TIN ${tinVal}`) : '',
        ptuVal ? (ptuVal.toUpperCase().includes('PTU') ? ptuVal : `PTU NO: ${ptuVal}`) : '',
        ptuIssuedVal ? (ptuIssuedVal.toUpperCase().includes('ISSUED') ? ptuIssuedVal : `DATE ISSUED: ${ptuIssuedVal}`) : '',
        branchName,
        addressVal,
        minVal ? (minVal.toUpperCase().includes('MIN') ? minVal : `MIN: ${minVal}`) : '',
        serialVal ? (serialVal.toUpperCase().includes('SN') ? serialVal : `SN: ${serialVal}`) : '',
      ].map((l) => l.trim()).filter(Boolean);

      const payload = {
        store_name: storeName,
        branch_name: branchName,
        store_location: addressVal,
        tin: tinVal,
        ptu_no: ptuVal,
        ptu_date_issued: ptuIssuedVal,
        min: minVal,
        serial_number: serialVal,
        print_size: (rootEl.querySelector('#cfgPrinterPrintSize') || rootEl.querySelector('#cfgPrintSize') || {}).value || 'normal',
        header_lines: headerLinesArray,
        footer_lines: extraFooterLines,
        thank_you_message: thankYouMsg,
      };

      try {
        const res = await api.post('/receipt_settings', payload);
        if (res && res.success) {
          delete receiptForm.dataset.dirty;
          notify.success('Receipt header, BIR credentials & thank-you footer saved successfully.');
          syncPreview();
        } else {
          notify.error((res && res.message) || 'Error saving receipt settings.');
        }
      } catch (err) {
        notify.error('Network error saving receipt settings.');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* 4) Printer Settings form submit */
  const printerForm = rootEl.querySelector('#printerSettingsForm');
  if (printerForm) {
    printerForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSavePrinterSettings');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const payload = {
        printer_required: Boolean((rootEl.querySelector('#cfgPrinterRequired') || {}).checked),
        auto_discover_network_printers: Boolean((rootEl.querySelector('#cfgAutoDiscoverPrinters') || {}).checked),
        print_size: (rootEl.querySelector('#cfgPrinterPrintSize') || {}).value || 'compact',
        cashier_printer_host: (rootEl.querySelector('#cfgCashierPrinterHost') || {}).value || '',
        cashier_printer_port: parseInt((rootEl.querySelector('#cfgCashierPrinterPort') || {}).value) || 9100,
        cashier_windows_printer_name: (rootEl.querySelector('#cfgCashierWindowsPrinter') || {}).value || '',
        kitchen_printer_host: (rootEl.querySelector('#cfgKitchenPrinterHost') || {}).value || '',
        kitchen_printer_port: parseInt((rootEl.querySelector('#cfgKitchenPrinterPort') || {}).value) || 9100,
        kitchen_windows_printer_name: (rootEl.querySelector('#cfgKitchenWindowsPrinter') || {}).value || '',
      };

      try {
        const res = await api.post('/receipt_settings', payload);
        if (res && res.success) {
          delete printerForm.dataset.dirty;
          notify.success('Hardware printer settings updated.');
          checkPrinterStatus(rootEl);
        } else {
          notify.error((res && res.message) || 'Error saving printer settings.');
        }
      } catch (err) {
        notify.error('Network error saving printer settings.');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* 5) RLC SFTP form submit */
  const rlcForm = rootEl.querySelector('#rlcSettingsForm');
  if (rlcForm) {
    rlcForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSaveRlcSettings');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const isEnabled = Boolean((rootEl.querySelector('#cfgRlcEnabled') || {}).checked);
      const rawServer = ((rootEl.querySelector('#rlcServer') || {}).value || '').trim();
      const rawUser = ((rootEl.querySelector('#rlcUsername') || {}).value || '').trim();
      const rawPass = (rootEl.querySelector('#rlcPassword') || {}).value || '';
      const rawPath = ((rootEl.querySelector('#rlcRemoteDir') || {}).value || '').trim();
      const rawPort = parseInt((rootEl.querySelector('#rlcPort') || {}).value) || 22;

      // When RLC is enabled, validate required fields before submitting
      if (isEnabled) {
        if (!rawServer) {
          notify.error('RLC server hostname is required when RLC is enabled.');
          btn.disabled = false;
          btn.innerHTML = originalText;
          return;
        }
        if (!rawUser) {
          notify.error('RLC username is required when RLC is enabled.');
          btn.disabled = false;
          btn.innerHTML = originalText;
          return;
        }
        if (!rawPass) {
          notify.error('RLC password is required when RLC is enabled.');
          btn.disabled = false;
          btn.innerHTML = originalText;
          return;
        }
      }

      // If RLC is disabled and fields are cleared, provide safe fallback values
      // so even a still-running older backend process accepts the save without rejecting
      const payload = {
        rlc_enabled: isEnabled,
        server: isEnabled ? rawServer : (rawServer || 'none'),
        port: rawPort,
        username: isEnabled ? rawUser : (rawUser || 'none'),
        password: isEnabled ? rawPass : (rawPass || 'none'),
        remote_path: isEnabled ? (rawPath || '/IT_Tenants/') : (rawPath || 'none'),
      };

      try {
        const res = await api.post('/rlc_settings', payload);
        if (res && res.success) {
          delete rlcForm.dataset.dirty;
          notify.success('RLC SFTP configuration saved successfully.');
        } else {
          notify.error((res && (res.message || res.error)) || 'Error saving RLC settings.');
        }
      } catch (err) {
        const msg = (err && (err.message || err.detail || err.error)) || 'Error saving RLC settings.';
        notify.error(msg);
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* 5b) RLC SFTP Test Connection */
  const testRlcBtn = rootEl.querySelector('#btnTestRlcConnection');
  if (testRlcBtn) {
    testRlcBtn.addEventListener('click', async () => {
      testRlcBtn.disabled = true;
      const originalHTML = testRlcBtn.innerHTML;
      testRlcBtn.innerHTML = '<span>Testing SFTP...</span>';

      const payload = {
        server: (rootEl.querySelector('#rlcServer') || {}).value || '',
        port: parseInt((rootEl.querySelector('#rlcPort') || {}).value) || 22,
        username: (rootEl.querySelector('#rlcUsername') || {}).value || '',
        password: (rootEl.querySelector('#rlcPassword') || {}).value || '',
        remote_path: (rootEl.querySelector('#rlcRemoteDir') || {}).value || '',
      };

      try {
        const res = await api.post('/api/test_rlc_connection', payload);
        if (res && res.success) {
          notify.success(res.message || 'RLC SFTP Connection Successful!');
        } else {
          notify.error((res && res.message) || 'RLC SFTP Connection Failed.');
        }
      } catch (err) {
        const msg = (err && (err.message || err.detail || err.error)) || 'Network error testing RLC SFTP connection.';
        notify.error(msg);
      } finally {
        testRlcBtn.disabled = false;
        testRlcBtn.innerHTML = originalHTML;
      }
    });
  }

  /* 5c) Clear All RLC Settings */
  const clearRlcBtn = rootEl.querySelector('#btnClearRlcSettings');
  if (clearRlcBtn) {
    clearRlcBtn.addEventListener('click', () => {
      const enabledCb = rootEl.querySelector('#cfgRlcEnabled');
      const serverInp = rootEl.querySelector('#rlcServer');
      const portInp = rootEl.querySelector('#rlcPort');
      const userInp = rootEl.querySelector('#rlcUsername');
      const passInp = rootEl.querySelector('#rlcPassword');
      const remoteInp = rootEl.querySelector('#rlcRemoteDir');

      if (enabledCb) enabledCb.checked = false;
      if (serverInp) serverInp.value = '';
      if (portInp) portInp.value = 22;
      if (userInp) userInp.value = '';
      if (passInp) passInp.value = '';
      if (remoteInp) remoteInp.value = '';

      if (rlcForm) rlcForm.dataset.dirty = 'true';
      notify.info('All RLC fields cleared. Click "Save RLC Settings" to apply.');
    });
  }

  /* 5c) SFTP Password Eye Toggle */
  const toggleRlcPassBtn = rootEl.querySelector('#btnToggleRlcPassword');
  const rlcPassInput = rootEl.querySelector('#rlcPassword');
  if (toggleRlcPassBtn && rlcPassInput) {
    toggleRlcPassBtn.addEventListener('click', () => {
      const isPass = rlcPassInput.type === 'password';
      rlcPassInput.type = isPass ? 'text' : 'password';
      const label = isPass ? 'Hide password' : 'Show password';
      toggleRlcPassBtn.setAttribute('aria-label', label);
      toggleRlcPassBtn.title = label;
      toggleRlcPassBtn.style.color = isPass ? 'var(--color-primary)' : 'var(--text-muted)';
    });
  }

  /* 6) Thank You form submit */
  const thankYouForm = rootEl.querySelector('#thankYouForm');
  if (thankYouForm) {
    thankYouForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = rootEl.querySelector('#btnSaveThankYou');
      btn.disabled = true;
      const originalText = btn.innerHTML;
      btn.textContent = 'Saving...';

      const footerVal = (rootEl.querySelector('#cfgFooterLines') || {}).value || '';
      const payload = {
        thank_you_message: footerVal,
        footer_lines: footerVal.split('\n').map((l) => l.trim()).filter(Boolean),
      };

      try {
        const res = await api.post('/receipt_settings', payload);
        if (res && res.success) {
          notify.success('Receipt footer message updated.');
          syncPreview();
        } else {
          notify.error((res && res.message) || 'Error saving thank you message.');
        }
      } catch (err) {
        notify.error('Network error saving footer message.');
      } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
      }
    });
  }

  /* Test Print Cashier */
  const btnTestCashier = rootEl.querySelector('#btnTestCashierPrinter');
  if (btnTestCashier) {
    btnTestCashier.addEventListener('click', async () => {
      notify.info('Sending test page to Cashier thermal receipt printer...');
      try {
        const res = await api.post('/test_print', { target: 'cashier' });
        if (res && res.success) {
          notify.success(res.message || 'Cashier test print sent.');
        } else {
          await modal.error('Cashier Printer Diagnostic', (res && res.message) || 'Printer test failed. Check connection.');
        }
      } catch (err) {
        notify.warning('Printer test simulated or unreachable.');
      }
    });
  }

  /* Test Print Kitchen */
  const btnTestKitchen = rootEl.querySelector('#btnTestKitchenPrinter');
  if (btnTestKitchen) {
    btnTestKitchen.addEventListener('click', async () => {
      notify.info('Sending test page to Kitchen slip printer...');
      try {
        const res = await api.post('/test_print', { target: 'kitchen' });
        if (res && res.success) {
          notify.success(res.message || 'Kitchen test print sent.');
        } else {
          await modal.error('Kitchen Printer Diagnostic', (res && res.message) || 'Printer test failed. Check connection.');
        }
      } catch (err) {
        notify.warning('Printer test simulated or unreachable.');
      }
    });
  }
}

export function destroy() {
  destroyFns.forEach((fn) => {
    try { fn(); } catch (e) {}
  });
  destroyFns = [];
  window.switchSettingsSection = null;
  rootEl = null;
}

export default { mount, destroy };
