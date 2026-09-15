/* =============================================================================
   js/core/export.js — Global Export & CSV Save As Download Helper
   ============================================================================= */

/**
 * Triggers a direct Save As browser download for any server export endpoint URL.
 * @param {string} url - Target URL (e.g. '/export_products_csv')
 * @param {string} [suggestedFilename] - Optional target filename
 */
export function downloadUrl(url, suggestedFilename) {
  if (!url) return;
  const a = document.createElement('a');
  a.href = url;
  if (suggestedFilename) {
    a.download = suggestedFilename;
  }
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    try {
      document.body.removeChild(a);
    } catch (e) {}
  }, 500);
}

/**
 * Generates and downloads a CSV file directly on the client side with UTF-8 BOM.
 * @param {string} filename - Filename (e.g. 'report.csv')
 * @param {Array<Object>|Array<Array>} data - Array of row objects or 2D array
 * @param {Array<string>} [headers] - Optional header titles
 */
export function exportCSV(filename, data, headers) {
  if (!data || !data.length) {
    console.warn('[exportCSV] No data provided to export.');
    return;
  }

  let csvRows = [];

  // Determine headers if data is array of objects
  if (headers && Array.isArray(headers)) {
    csvRows.push(headers.map(escapeCSVCell).join(','));
  } else if (typeof data[0] === 'object' && !Array.isArray(data[0])) {
    const keys = Object.keys(data[0]);
    csvRows.push(keys.map(escapeCSVCell).join(','));
  }

  data.forEach((row) => {
    if (Array.isArray(row)) {
      csvRows.push(row.map(escapeCSVCell).join(','));
    } else if (row && typeof row === 'object') {
      const keys = headers && Array.isArray(headers) ? headers : Object.keys(row);
      const rowValues = keys.map((k) => escapeCSVCell(row[k]));
      csvRows.push(rowValues.join(','));
    }
  });

  const csvContent = '\uFEFF' + csvRows.join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });

  const name = filename.toLowerCase().endsWith('.csv') ? filename : `${filename}.csv`;

  if (navigator.msSaveBlob) {
    // IE 10+
    navigator.msSaveBlob(blob, name);
  } else {
    const link = document.createElement('a');
    if (link.download !== undefined) {
      const url = URL.createObjectURL(blob);
      link.setAttribute('href', url);
      link.setAttribute('download', name);
      link.style.visibility = 'hidden';
      document.body.appendChild(link);
      link.click();
      setTimeout(() => {
        try {
          document.body.removeChild(link);
          URL.revokeObjectURL(url);
        } catch (e) {}
      }, 500);
    }
  }
}

function escapeCSVCell(val) {
  if (val == null || val === undefined) return '""';
  let str = String(val);
  if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
    str = str.replace(/"/g, '""');
    return `"${str}"`;
  }
  return str;
}

/* Global window attachments */
window.POS = window.POS || {};
window.POS.exportCSV = exportCSV;
window.POS.downloadUrl = downloadUrl;
window.downloadCSV = exportCSV;
window.downloadUrl = downloadUrl;

export default { exportCSV, downloadUrl };
