function escapeHtml(v) {
  return String(v ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatDateTime(value) {
  if (!value) return 'NA';
  try {
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return value;
    return dt.toLocaleString();
  } catch {
    return value;
  }
}

(function init() {
  const statusEl = document.getElementById('history-status');
  const tbody = document.querySelector('#history-table tbody');

  fetch('/api/extractions?limit=200')
    .then((res) => {
      if (!res.ok) throw new Error('Failed to load extraction history');
      return res.json();
    })
    .then((data) => {
      const items = Array.isArray(data?.items) ? data.items : [];
      if (!items.length) {
        statusEl.textContent = 'No extraction history yet.';
        tbody.innerHTML = '<tr><td colspan="7">No records found</td></tr>';
        return;
      }

      statusEl.textContent = `Loaded ${items.length} extraction record(s).`;
      tbody.innerHTML = items.map((item) => `
        <tr>
          <td>${escapeHtml(item.id)}</td>
          <td>${escapeHtml(formatDateTime(item.created_at))}</td>
          <td>${escapeHtml(item.patient_name || 'NA')}</td>
          <td>${escapeHtml(item.primary_diagnosis || 'NA')}</td>
          <td>${escapeHtml(formatDateTime(item.followup_datetime))}</td>
          <td>${escapeHtml(item.source_file_name || 'NA')}</td>
          <td>
            <a href="/care-plan?id=${encodeURIComponent(item.id)}" target="_blank" rel="noopener">Care Plan</a>
            |
            <a href="/summary/${encodeURIComponent(item.id)}" target="_blank" rel="noopener">Summary</a>
          </td>
        </tr>
      `).join('');
    })
    .catch((err) => {
      statusEl.textContent = `Error: ${err.message}`;
      tbody.innerHTML = '<tr><td colspan="7">Failed to load history</td></tr>';
    });
})();

