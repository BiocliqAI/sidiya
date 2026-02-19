const form = document.getElementById('extract-form');
const statusEl = document.getElementById('status');
const runBtn = document.getElementById('run-btn');
const summaryEl = document.getElementById('summary');
const hardEl = document.getElementById('hard-missing');
const softEl = document.getElementById('soft-missing');
const chfEl = document.getElementById('chf');
const detailsEl = document.getElementById('details');
const rawEl = document.getElementById('raw');
const carePlanLinkEl = document.getElementById('care-plan-link');
const summaryLinkEl = document.getElementById('summary-link');

async function checkServerConfig() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    const cfg = data?.config || {};
    const missing = [];
    if (!cfg.gemini_api_key_configured) missing.push('GEMINI_API_KEY');
    if (!cfg.landing_api_key_configured) missing.push('LANDINGAI_API_KEY');
    if (missing.length) {
      runBtn.disabled = true;
      statusEl.textContent = `Server missing env vars: ${missing.join(', ')}. Add them in deployment env and redeploy.`;
    } else {
      runBtn.disabled = false;
      statusEl.textContent = 'Idle';
    }
  } catch (_err) {
    statusEl.textContent = 'Cannot reach backend /health. Check deployment and URL.';
  }
}

function escapeHtml(v) {
  return String(v ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function kvRow(k, v) {
  return `<div class="kv-row"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`;
}

function renderList(el, values, emptyText, className) {
  if (!Array.isArray(values) || values.length === 0) {
    el.innerHTML = `<li class="${className}">${escapeHtml(emptyText)}</li>`;
    return;
  }
  el.innerHTML = values.map(v => `<li class="${className}">${escapeHtml(v)}</li>`).join('');
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const pdfInput = document.getElementById('pdf');

  if (!pdfInput.files || pdfInput.files.length === 0) {
    statusEl.textContent = 'Choose a PDF first.';
    return;
  }

  const fd = new FormData();
  fd.append('pdf', pdfInput.files[0]);

  runBtn.disabled = true;
  statusEl.textContent = 'Running Landing OCR + Gemini extraction...';

  try {
    const res = await fetch('/extract', { method: 'POST', body: fd });
    const rawText = await res.text();
    let data = null;
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch (_err) {
      data = { detail: rawText || 'Non-JSON response from backend' };
    }

    if (!res.ok) {
      const detail = data?.detail || 'Request failed';
      throw new Error(detail);
    }

    const summaryRows = [];
    const firstAppt = (data?.follow_up?.appointments || [])[0] || {};
    summaryRows.push(kvRow('Patient', data?.patient?.full_name || 'NA'));
    summaryRows.push(kvRow('DOB', data?.patient?.dob || 'NA'));
    summaryRows.push(kvRow('MRN', data?.patient?.mrn || 'NA'));
    summaryRows.push(kvRow('Primary Dx', data?.clinical_episode?.primary_diagnosis || 'NA'));
    summaryRows.push(kvRow('Reason for Admission', data?.clinical_episode?.reason_for_hospitalization || 'NA'));
    summaryRows.push(kvRow('Discharge', data?.encounter?.discharge_datetime || 'NA'));
    summaryRows.push(kvRow('Follow-up Date', firstAppt?.scheduled_datetime || 'NA'));
    summaryRows.push(kvRow('Follow-up Doctor', firstAppt?.provider_name || 'NA'));
    summaryRows.push(kvRow('Medications', (data?.medications?.discharge_medications || []).length));
    summaryRows.push(kvRow('Ready for App', data?.validation?.ready_for_patient_app ? 'Yes' : 'No'));
    summaryEl.innerHTML = summaryRows.join('');

    renderList(
      hardEl,
      data?.validation?.hard_stop_missing_fields || [],
      'No hard-stop missing fields.',
      (data?.validation?.hard_stop_missing_fields || []).length ? 'bad' : 'good'
    );

    renderList(
      softEl,
      data?.validation?.soft_stop_missing_fields || [],
      'No soft-stop missing fields.',
      (data?.validation?.soft_stop_missing_fields || []).length ? 'warn' : 'good'
    );

    const chfRows = [];
    chfRows.push(kvRow('Diagnosis Confirmed', String(data?.clinical_modules?.chf?.diagnosis_confirmed ?? 'NA')));
    chfRows.push(kvRow('HF Class', data?.clinical_modules?.chf?.hf_phenotype?.classification || 'NA'));
    chfRows.push(kvRow('LVEF %', data?.clinical_modules?.chf?.hf_phenotype?.latest_lvef_percent ?? 'NA'));
    chfRows.push(kvRow('Euvolemic at Discharge', String(data?.clinical_modules?.chf?.congestion_status?.euvolemic_at_discharge ?? 'NA')));
    chfRows.push(kvRow('CHF F/U Scheduled', String(data?.clinical_modules?.chf?.follow_up?.hf_followup_scheduled ?? 'NA')));
    chfRows.push(kvRow('CHF F/U Datetime', data?.clinical_modules?.chf?.follow_up?.followup_datetime || 'NA'));
    chfEl.innerHTML = chfRows.join('');

    const detailsRows = [];
    const details = data?.extracted_details || {};
    detailsRows.push(kvRow('IP No', details?.patient?.ip_no || 'NA'));
    detailsRows.push(kvRow('Phone', details?.patient?.phone || 'NA'));
    detailsRows.push(kvRow('Address', details?.patient?.address || 'NA'));
    detailsRows.push(kvRow('DOA', details?.encounter?.admission_date || 'NA'));
    detailsRows.push(kvRow('DOD', details?.encounter?.discharge_date || 'NA'));
    detailsRows.push(kvRow('Diet Advice', details?.discharge_advice?.diet || 'NA'));
    detailsRows.push(kvRow('Fluid Advice', details?.discharge_advice?.fluid || 'NA'));
    detailsRows.push(kvRow('Activity Advice', details?.discharge_advice?.activity || 'NA'));
    detailsRows.push(kvRow('Echo Date', details?.clinical_episode?.echo_date || 'NA'));
    detailsRows.push(kvRow('Echo LVEF %', details?.clinical_episode?.lvef_percent ?? 'NA'));
    detailsEl.innerHTML = detailsRows.join('');

    rawEl.textContent = JSON.stringify(data, null, 2);
    localStorage.setItem('oyster_last_extraction', JSON.stringify(data));
    const extractionId = data?.extraction_id;
    if (extractionId) {
      carePlanLinkEl.href = `/care-plan?id=${encodeURIComponent(extractionId)}`;
      summaryLinkEl.href = `/summary/${encodeURIComponent(extractionId)}`;
      localStorage.setItem('oyster_last_extraction_id', String(extractionId));
    } else {
      carePlanLinkEl.href = '/care-plan';
      summaryLinkEl.href = '/summary/0';
    }
    carePlanLinkEl.classList.remove('hidden');
    summaryLinkEl.classList.remove('hidden');
    statusEl.textContent = 'Extraction complete.';
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  } finally {
    runBtn.disabled = false;
  }
});

checkServerConfig();
