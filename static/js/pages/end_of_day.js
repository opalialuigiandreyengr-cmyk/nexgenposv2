/* ============================================================================
   end_of_day.js — End of Day Wizard Controller (POS_V2, Phase 7)
   ============================================================================ */

import { api } from '../core/api.js';
import modal from '../core/modal.js';
import { notify } from '../core/toast.js';

let recipientsList = [];
let currentPendingCount = 0;
let currentMissingDates = [];
let printerReady = false;
let printerStatusMessage = 'Checking...';

export function mount(rootEl) {
  const eodRoot = rootEl.querySelector('#eodRoot');
  if (!eodRoot) return;

  const rlcEnabled = eodRoot.dataset.rlcEnabled === 'true';

  // 1. Initial Readiness Checks (All 3 Criteria)
  runAllReadinessChecks(rootEl, rlcEnabled);

  // Missing dates button trigger
  const processMissingBtn = rootEl.querySelector('#btnProcessMissingDates');
  if (processMissingBtn) {
    processMissingBtn.addEventListener('click', async () => {
      await modal.eodRequiredModal(currentMissingDates, async () => {
        await runAllReadinessChecks(rootEl, rlcEnabled);
      });
    });
  }

  // 2. Action Buttons
  setupPrintButtons(rootEl);

  // 3. Confirm EOD Modal ('YES' guard)
  setupConfirmEodModal(rootEl, rlcEnabled);

  // 4. Z-Reading Review Modal
  setupZReadingModal(rootEl);

  // 5. Daily Bundle Modal (Recipients & Transmission)
  setupDailyBundleModal(rootEl);
}

export function destroy() {
  recipientsList = [];
  toggleBodyScroll(false);
}

function toggleBodyScroll(disable) {
  if (disable) {
    document.body.classList.add('modal-open');
    document.body.style.overflow = 'hidden';
  } else {
    const openModals = document.querySelectorAll('.lib-modal-overlay:not(.d-none), .modal-backdrop:not(.d-none)');
    if (openModals.length === 0) {
      document.body.classList.remove('modal-open');
      document.body.style.overflow = '';
    }
  }
}

async function showPrinterRequiredError(errOrRes) {
  let msg = 'Please ensure your printer is connected and ready.';
  if (errOrRes) {
    let textCandidate = '';
    if (typeof errOrRes === 'string') textCandidate = errOrRes;
    else if (errOrRes.message && typeof errOrRes.message === 'string') textCandidate = errOrRes.message;
    else if (errOrRes.error && typeof errOrRes.error === 'string') textCandidate = errOrRes.error;
    else if (errOrRes.detail && typeof errOrRes.detail === 'string') textCandidate = errOrRes.detail;

    if (textCandidate && !textCandidate.trim().startsWith('<')) {
      msg = textCandidate;
    }
  }
  await modal.error(
    'Printer Connection Required',
    msg,
    { confirmLabel: 'I understand' }
  );
}

function setupPrintButtons(rootEl) {
  const printXBtn = rootEl.querySelector('#print-xreading-btn');
  if (printXBtn) {
    printXBtn.addEventListener('click', async () => {
      printXBtn.disabled = true;
      try {
        const res = await api.get('/print_xreading');
        if (res && (res.success || res.status === 'success')) {
          notify.success('X-Reading report printed successfully.');
        } else {
          await showPrinterRequiredError(res);
        }
      } catch (err) {
        await showPrinterRequiredError(err);
      } finally {
        printXBtn.disabled = false;
      }
    });
  }

  const printAccountabilityBtn = rootEl.querySelector('#print-cashier-accountability-btn');
  if (printAccountabilityBtn) {
    printAccountabilityBtn.addEventListener('click', async () => {
      printAccountabilityBtn.disabled = true;
      try {
        const res = await api.post('/print_cashier_accountability', {});
        if (res && (res.success || res.status === 'success')) {
          notify.success('Cashier accountability report printed.');
        } else {
          await showPrinterRequiredError(res);
        }
      } catch (err) {
        await showPrinterRequiredError(err);
      } finally {
        printAccountabilityBtn.disabled = false;
      }
    });
  }

  const printItemSalesBtn = rootEl.querySelector('#print-today-item-sales-btn');
  if (printItemSalesBtn) {
    printItemSalesBtn.addEventListener('click', async () => {
      printItemSalesBtn.disabled = true;
      try {
        const res = await api.post('/print_today_item_sales_report', {});
        if (res && (res.success || res.status === 'success')) {
          notify.success('Today item sales report printed.');
        } else {
          await showPrinterRequiredError(res);
        }
      } catch (err) {
        await showPrinterRequiredError(err);
      } finally {
        printItemSalesBtn.disabled = false;
      }
    });
  }

  const printServiceBtn = rootEl.querySelector('#print-today-service-sales-btn');
  if (printServiceBtn) {
    printServiceBtn.addEventListener('click', async () => {
      printServiceBtn.disabled = true;
      try {
        const res = await api.post('/print_today_takeout_pickup_delivery_report', {});
        if (res && (res.success || res.status === 'success')) {
          notify.success('Takeout / Pickup / Delivery sales report printed.');
        } else {
          await showPrinterRequiredError(res);
        }
      } catch (err) {
        await showPrinterRequiredError(err);
      } finally {
        printServiceBtn.disabled = false;
      }
    });
  }
}

function setupConfirmEodModal(rootEl, rlcEnabled) {
  const openBtn = rootEl.querySelector('#btnOpenConfirmEodModal');
  if (openBtn) {
    openBtn.addEventListener('click', async () => {
      // 1. HARD BLOCK: Cashier receipt printer must be connected
      if (!printerReady) {
        await modal.error(
          'Printer Connection Required',
          `End of Day is BLOCKED: The cashier receipt printer is not connected or is offline.\n\nStatus: ${printerStatusMessage}\n\nPlease plug in, turn on, and verify paper in the printer before closing shift.`,
          { confirmLabel: 'I understand' }
        );
        return;
      }

      // Note: RLC integration check is not a blocker for Z-Reading.
      // If RLC is disabled in Settings, SFTP transmission is bypassed during closeout.

      // 3. HARD BLOCK: No pending orders allowed
      if (currentPendingCount > 0) {
        const confirmed = await modal.warning(
          'Pending Orders Block End of Day',
          `End of Day is BLOCKED: There are ${currentPendingCount} pending order(s) that must be settled or cancelled first.`,
          {
            confirmLabel: 'Review Pending Orders',
            cancelLabel: 'Close'
          }
        );
        if (confirmed) {
          window.location.href = '/orders';
        }
        return;
      }

      // 4. HARD BLOCK: Prior unclosed business dates must be closed in sequence
      if (currentMissingDates && currentMissingDates.length > 0) {
        const confirmed = await modal.warning(
          'Unclosed Prior Business Dates',
          `End of Day is BLOCKED: There are ${currentMissingDates.length} unclosed earlier business date(s). Earlier dates must have End of Day completed first.`,
          {
            confirmLabel: 'Process Prior Dates',
            cancelLabel: 'Cancel'
          }
        );
        if (confirmed) {
          await modal.eodRequiredModal(currentMissingDates, async () => {
            await runAllReadinessChecks(rootEl, rlcEnabled);
          });
        }
        return;
      }

      const confirmed = await modal.confirmEodModal({ rlcEnabled });
      if (confirmed) {
        await runEodProgressFlow(rootEl, rlcEnabled);
      }
    });
  }
}

async function runEodProgressFlow(rootEl, rlcEnabled) {
  const progress = modal.progressEodModal({ rlcEnabled });
  try {
    progress.setStep('1', 'var(--color-primary)');
    const res = await api.post('/perform_end_of_day_ajax', {});

    if (res && (res.success || res.status === 'success')) {
      if (rlcEnabled) {
        progress.setStep('2', 'var(--color-primary)');
        progress.setStep('3', 'var(--color-primary)');
        progress.setStep('4', '#10b981');
      } else {
        progress.setStep('4', '#10b981');
      }

      progress.setStatus(`<span style="color: #10b981; font-weight: 700;">✅ ${res.message || 'End of Day completed successfully!'}</span>`);

      progress.showPrintClose(async () => {
        try {
          const todayStr = new Date().toISOString().split('T')[0];
          const pRes = await api.post('/print_zreading_and_item_sales', { date: todayStr });
          if (pRes && (pRes.success || pRes.status === 'success')) {
            notify.success('Z-Reading and item sales printed.');
          } else {
            await showPrinterRequiredError(pRes);
          }
        } catch (e) {
          await showPrinterRequiredError(e);
        } finally {
          progress.close();
          window.location.reload();
        }
      });
      progress.showClose(() => {
        progress.close();
        window.location.reload();
      });

      checkPendingOrders(rootEl);
      checkMissingDates(rootEl);
    } else {
      progress.setStatus(`<span style="color: #ef4444; font-weight: 700;">❌ ${res.message || res.error || 'End of Day failed.'}</span>`);
      progress.showClose(() => {
        progress.close();
      });
    }
  } catch (err) {
    let errorMsg = 'Error executing End of Day.';
    if (err) {
      if (typeof err === 'string') errorMsg = err;
      else if (err.message && typeof err.message === 'string') errorMsg = err.message;
      else if (err.detail && typeof err.detail === 'string') errorMsg = err.detail;
      else if (err.error && typeof err.error === 'string') errorMsg = err.error;
    }
    progress.setStatus(`<span style="color: #ef4444; font-weight: 700;">❌ ${errorMsg}</span>`);
    progress.showClose(() => {
      progress.close();
    });
  }
}

function setupZReadingModal(rootEl) {
  const modalHost = rootEl.querySelector('#zreadingModal');
  const openBtn = rootEl.querySelector('#btnOpenZReadingModal');
  const closeBtn = rootEl.querySelector('#btnCloseZReadingModal');
  const cancelBtn = rootEl.querySelector('#btnCancelZReadingModal');
  const loadBtn = rootEl.querySelector('#load-zreading-btn');
  const fromInput = rootEl.querySelector('#zreading-from-date');
  const toInput = rootEl.querySelector('#zreading-to-date');
  const contentEl = rootEl.querySelector('#zreading-report-content');
  const reprintBtn = rootEl.querySelector('#print-zreading-btn');
  const downloadLink = rootEl.querySelector('#download-zreading-btn');

  const todayStr = new Date().toISOString().split('T')[0];
  if (fromInput && !fromInput.value) fromInput.value = todayStr;
  if (toInput && !toInput.value) toInput.value = todayStr;

  const hideModal = () => {
    if (modalHost) modalHost.classList.add('d-none');
    toggleBodyScroll(false);
  };

  if (openBtn && modalHost) {
    openBtn.addEventListener('click', () => {
      modalHost.classList.remove('d-none');
      toggleBodyScroll(true);
      loadZReadingData();
    });
  }

  if (closeBtn) closeBtn.addEventListener('click', hideModal);
  if (cancelBtn) cancelBtn.addEventListener('click', hideModal);
  if (loadBtn) loadBtn.addEventListener('click', () => loadZReadingData());

  async function loadZReadingData() {
    if (!contentEl) return;
    const fromVal = fromInput ? fromInput.value : todayStr;
    const toVal = toInput ? toInput.value : todayStr;

    contentEl.textContent = 'Loading Z-Reading report data...';
    if (downloadLink) downloadLink.href = `/download_zreading_txt?from_date=${fromVal}&to_date=${toVal}`;

    try {
      const res = await api.get(`/get_zreading_data_for_date_range?from_date=${fromVal}&to_date=${toVal}`);
      if (res && res.success && res.zreading_data) {
        contentEl.textContent = formatZReadingText(res.zreading_data, fromVal, toVal);
        if (reprintBtn) {
          reprintBtn.disabled = !res.z_reading_generated;
        }
      } else if (res && res.content) {
        contentEl.textContent = res.content;
      } else {
        contentEl.textContent = 'No Z-Reading records found for selected date range.';
      }
    } catch (err) {
      contentEl.textContent = 'No Z-Reading records found for selected date range.';
    }
  }

  if (downloadLink) {
    downloadLink.addEventListener('click', () => {
      notify.success('Z-Reading TXT file download started.');
    });
  }

  if (reprintBtn) {
    reprintBtn.addEventListener('click', async () => {
      const fromVal = fromInput ? fromInput.value : todayStr;
      const toVal = toInput ? toInput.value : todayStr;
      reprintBtn.disabled = true;
      try {
        const res = await api.get(`/print_zreading?from_date=${fromVal}&to_date=${toVal}&reprint=true`);
        if (res && (res.success || res.status === 'success')) {
          notify.success('Z-Reading reprint sent to cashier printer.');
        } else {
          await showPrinterRequiredError(res);
        }
      } catch (err) {
        await showPrinterRequiredError(err);
      } finally {
        reprintBtn.disabled = false;
      }
    });
  }
}

function formatZReadingText(zdata, fromDate, toDate) {
  const formatNum = (v) => (typeof v === 'number' ? v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : (v || '0.00'));

  const currentDate = new Date();
  const reportDateStr = currentDate.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const reportTimeStr = currentDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true }).toUpperCase();

  const startDateStr = zdata["Start Date"] || "N/A";
  const startTimeStr = zdata["Start Time"] || "N/A";
  const endDateStr = zdata["End Date"] || "N/A";
  const endTimeStr = zdata["End Time"] || "N/A";

  let begSiNum = zdata["Beginning SI No"] || "0000000000";
  let endSiNum = zdata["Ending SI No"] || "0000000000";
  if (typeof begSiNum === 'string' && begSiNum.includes('-')) begSiNum = begSiNum.split('-')[1] || begSiNum;
  if (typeof endSiNum === 'string' && endSiNum.includes('-')) endSiNum = endSiNum.split('-')[1] || endSiNum;
  begSiNum = String(begSiNum).padStart(10, '0');
  endSiNum = String(endSiNum).padStart(10, '0');

  const begVoidNum = zdata["Beginning VOID No"] || "0000000000";
  const endVoidNum = zdata["Ending VOID No"] || "0000000000";
  const begRefundNum = zdata["Beginning REFUND No"] || "0000000000";
  const endRefundNum = zdata["Ending REFUND No"] || "0000000000";

  const grossAmount = zdata["Gross Sales"] || 0;
  const lessDiscount = zdata["Total Discount"] || 0;
  const lessReturn = zdata["Total Refund"] || 0;
  const lessVoid = zdata["Amount Voided"] || 0;

  const scDiscount = zdata["Senior Citizen Discount"] || 0;
  const pwdDiscount = zdata["PWD Discount"] || 0;
  const athleteDiscount = zdata["National Athlete Discount"] || 0;
  const movDiscount = zdata["Medal of Valor Discount"] || 0;
  const soloParentDiscount = zdata["Solo Parent Discount"] || 0;
  const otherDiscount = zdata["Other Discount"] || 0;

  const vatAdjSc = zdata["VAT Adj SC"] || 0;
  const vatAdjPwd = zdata["VAT Adj PWD"] || 0;
  const vatAdjSolo = zdata["VAT Adj Solo"] || 0;
  const totalVatAdj = zdata["Total VAT Adjustment"] || 0;
  const netAmount = zdata["Net Amount"] ?? (grossAmount - lessDiscount - lessVoid - lessReturn - totalVatAdj);

  const cashSales = zdata["Total Cash Sales"] || 0;
  const creditCardSales = zdata["Credit Card"] || 0;
  const mayaSales = zdata["Maya"] || 0;
  const gcashSales = zdata["Gcash"] || 0;
  const giftCheck = zdata["Gift Check"] || 0;
  const cheque = zdata["Cheque"] || 0;
  const debitCard = zdata["Debit Card"] || 0;
  const transactionCount = zdata["# Transactions"] || 0;
  const customerCount = zdata["# Customers( total no_pax/ covers)"] || 0;

  const cashInDrawer = zdata["Cash In Drawer"] || 0;
  const paymentsReceived = zdata["Payments Received"] || 0;
  const openingFund = zdata["Opening Fund"] || zdata["opening_fund"] || 0;
  const shortOver = (cashInDrawer + creditCardSales + gcashSales + mayaSales + giftCheck + cheque + debitCard) - (openingFund + paymentsReceived);

  return `Z-READING REPORT
Report Date: ${reportDateStr}
Report Time: ${reportTimeStr}
Start Date & Time: ${startDateStr} ${startTimeStr}
End Date & Time: ${endDateStr} ${endTimeStr}
Beg. SI #: ${begSiNum}
End. SI #: ${endSiNum}
Beg. VOID #: ${begVoidNum}
End. VOID #: ${endVoidNum}
Beg. RETURN #: ${begRefundNum}
End. RETURN #: ${endRefundNum}
Reset Counter No. ${zdata["Reset Counter"] || 0}
Z Counter No. : ${zdata["Z Counter #"] || 1}
# Transactions: ${transactionCount}
# Customers: ${customerCount}
---------------------------------------------------------
Present Accumulated Sales: ${formatNum(zdata["Present Accumulated Sales"] || 0)}
Previous Accumulated Sales: ${formatNum(zdata["PREVIOUS NGRT"] || 0)}
Sales for the Day: ${formatNum(grossAmount)}
---------------------------------------------------------
BREAKDOWN OF SALES
VATABLE SALES : ${formatNum(zdata["VAT Sales"] || 0)}
VAT AMOUNT: ${formatNum(zdata["VAT Collected"] || 0)}
VAT EXEMPT SALES: ${formatNum(zdata["VAT Exempt Sales"] || 0)}
ZERO RATED SALES: 0.00
---------------------------------------------------------
Gross Amount: ${formatNum(grossAmount)}
Less Discount: ${formatNum(lessDiscount)}
Less Refund: ${formatNum(lessReturn)}
Less Void: ${formatNum(lessVoid)}
Less VAT Adjustment: ${formatNum(totalVatAdj)}
Net Amount: ${formatNum(netAmount)}
---------------------------------------------------------
DISCOUNT SUMMARY
SC Disc. : ${formatNum(scDiscount)}
PWD Disc. : ${formatNum(pwdDiscount)}
NAAC Disc. : ${formatNum(athleteDiscount)}
MOV Disc. : ${formatNum(movDiscount)}
Solo Parent Disc. : ${formatNum(soloParentDiscount)}
Other Disc. : ${formatNum(otherDiscount)}
---------------------------------------------------------
SALES ADJUSTMENT
VOID : ${formatNum(lessVoid)}
REFUND : ${formatNum(lessReturn)}
---------------------------------------------------------
VAT ADJUSTMENT
 SC TRANS. : ${formatNum(vatAdjSc)}
 PWD TRANS : ${formatNum(vatAdjPwd)}
 SOLO TRANS : ${formatNum(vatAdjSolo)}
 ZERO-RATED TRANS.: 0.00
VAT on Return: 0.00
 Other VAT Adjustments 0.00
---------------------------------------------------------
TRANSACTION SUMMARY
Cash In Drawer: ${formatNum(cashInDrawer)}
CHEQUE ${formatNum(cheque)}
CREDIT CARD ${formatNum(creditCardSales)}
MAYA ${formatNum(mayaSales)}
GCASH ${formatNum(gcashSales)}
DEBIT CARD ${formatNum(debitCard)}
GIFT CERTIFICATE ${formatNum(giftCheck)}
Opening Fund: ${formatNum(openingFund)}
Less Withdrawal: 0.00
Payments Received: ${formatNum(paymentsReceived)}
---------------------------------------------------------
SHORT/OVER: ${parseFloat(shortOver.toFixed(2)) === 0 ? '0.00' : Math.abs(shortOver).toFixed(2) + (shortOver > 0 ? '+' : '-')}
---------------------------------------------------------

*** END OF Z-READING REPORT ***`;
}

function setupDailyBundleModal(rootEl) {
  const openBtn = rootEl.querySelector('#openDailyBundleModalBtn');
  if (openBtn) {
    openBtn.addEventListener('click', async () => {
      const todayStr = new Date().toISOString().split('T')[0];
      const result = await modal.dailyBundleModal({
        date: todayStr,
        recipients: recipientsList.length ? recipientsList : ['']
      });

      if (!result) return; // Dismissed or canceled

      recipientsList = result.recipients;

      try {
        const res = await api.post('/generate_and_send_rlc', {
          date: result.date,
          recipients: result.recipients
        });

        if (res && (res.success || res.status === 'success')) {
          await modal.success(
            'Daily Reports Transmitted',
            res.message || 'Daily report bundle generated and sent successfully.',
            { confirmLabel: 'Done' }
          );
        } else {
          await modal.error(
            'Transmission Alert',
            (res && (res.message || res.error)) || 'Error generating or sending daily report bundle.',
            { confirmLabel: 'I understand' }
          );
        }
      } catch (err) {
        await modal.error(
          'Network Connection Error',
          'Could not reach server to transmit daily reports. Please check your network connection.',
          { confirmLabel: 'I understand' }
        );
      }
    });
  }
}

async function runAllReadinessChecks(rootEl, rlcEnabled) {
  await Promise.all([
    checkPendingOrders(rootEl),
    checkPrinterStatus(rootEl),
    checkMissingDates(rootEl)
  ]);
  updateOverallReadiness(rootEl, rlcEnabled);
}

function updateOverallReadiness(rootEl, rlcEnabled) {
  const badge = rootEl.querySelector('#eodStatusBadge');
  if (!badge) return;

  const isBlocked = (
    currentPendingCount > 0 ||
    !printerReady ||
    (currentMissingDates && currentMissingDates.length > 0)
  );

  if (isBlocked) {
    badge.textContent = 'BLOCKED';
    badge.className = 'eod-pill pill-blocked';
  } else {
    badge.textContent = 'READY';
    badge.className = 'eod-pill pill-ready';
  }
}

async function checkPrinterStatus(rootEl) {
  const statusEl = rootEl.querySelector('#printerStatusSummary');
  const textEl = rootEl.querySelector('#printerSummaryText');

  try {
    let res = await api.get('/check_printer_status?refresh=1');
    let retries = 2;
    while (res && res.checking && retries > 0) {
      await new Promise(r => setTimeout(r, 600));
      res = await api.get('/check_printer_status');
      retries--;
    }

    if (res && (res.connected || res.dev_mode || res.printer_required === false)) {
      printerReady = true;
      printerStatusMessage = 'Connected';
      if (statusEl) {
        statusEl.textContent = 'CONNECTED';
        statusEl.style.color = '#10b981';
      }
      if (textEl) {
        textEl.textContent = res.message || 'Cashier receipt printer is online.';
        textEl.style.color = '#94a3b8';
      }
    } else {
      printerReady = false;
      printerStatusMessage = (res && res.message) ? res.message : 'Cashier printer not connected or offline.';
      if (statusEl) {
        statusEl.textContent = 'DISCONNECTED';
        statusEl.style.color = '#ef4444';
      }
      if (textEl) {
        textEl.textContent = printerStatusMessage + ' (BLOCKING)';
        textEl.style.color = '#ef4444';
      }
    }
  } catch (err) {
    printerReady = false;
    printerStatusMessage = 'Connection check failed';
    if (statusEl) {
      statusEl.textContent = 'OFFLINE';
      statusEl.style.color = '#ef4444';
    }
    if (textEl) {
      textEl.textContent = 'Could not verify printer status (BLOCKING)';
      textEl.style.color = '#ef4444';
    }
  }
}

async function checkPendingOrders(rootEl) {
  const countEl = rootEl.querySelector('#pendingOrdersCountSummary');
  const textEl = rootEl.querySelector('#pendingOrdersSummaryText');
  const btnEl = rootEl.querySelector('#pendingOrdersSummaryBtn');

  try {
    const res = await api.get('/get_pending_orders_count');
    const count = (res && typeof res.count === 'number') ? res.count : 0;
    currentPendingCount = count;

    if (countEl) countEl.textContent = count;

    if (count > 0) {
      if (countEl) countEl.style.color = '#ef4444';
      if (textEl) {
        textEl.textContent = `${count} pending order(s) found. All orders must be settled or canceled (BLOCKING).`;
        textEl.style.color = '#ef4444';
      }
      if (btnEl) btnEl.classList.remove('d-none');
    } else {
      if (countEl) countEl.style.color = '';
      if (textEl) {
        textEl.textContent = 'All active orders settled.';
        textEl.style.color = '#94a3b8';
      }
      if (btnEl) btnEl.classList.add('d-none');
    }
  } catch (err) {
    currentPendingCount = 0;
  }
}

async function checkMissingDates(rootEl) {
  const missingCard = rootEl.querySelector('#missingDatesCard');
  const missingCount = rootEl.querySelector('#missingDatesCount');
  const missingText = rootEl.querySelector('#missingDatesText');
  const missingBtn = rootEl.querySelector('#btnProcessMissingDates');

  try {
    const res = await api.get('/check_missing_eod_dates');
    let dates = [];
    if (res && res.missing_eod_dates && res.missing_eod_dates.length > 0) {
      dates = res.missing_eod_dates;
    } else if (res && res.missing_dates && res.missing_dates.length > 0) {
      dates = res.missing_dates;
    }
    currentMissingDates = dates;

    if (dates.length > 0) {
      if (missingCard) missingCard.classList.remove('d-none');
      if (missingCount) missingCount.textContent = dates.length;
      const dateList = dates.map(d => (typeof d === 'string' ? d : (d.display_date || d.date))).join(', ');
      if (missingText) missingText.textContent = `Unclosed prior business dates: ${dateList} (BLOCKING)`;
      if (missingBtn) missingBtn.classList.remove('d-none');
    } else {
      if (missingCard) missingCard.classList.add('d-none');
      if (missingBtn) missingBtn.classList.add('d-none');
    }
  } catch (err) {
    if (missingCard) missingCard.classList.add('d-none');
    if (missingBtn) missingBtn.classList.add('d-none');
  }
}

export default { mount, destroy };
