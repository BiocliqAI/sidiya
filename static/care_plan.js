const STORAGE_KEY = 'oyster_last_extraction';

function escapeHtml(v) {
  return String(v ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderList(el, values, emptyText, className = '') {
  if (!Array.isArray(values) || values.length === 0) {
    el.innerHTML = `<li class="${className}">${escapeHtml(emptyText)}</li>`;
    return;
  }
  el.innerHTML = values.map((v) => `<li class="${className}">${escapeHtml(v)}</li>`).join('');
}

function formatDateTime(value) {
  if (!value) return 'Not scheduled';
  try {
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return value;
    return dt.toLocaleString();
  } catch {
    return value;
  }
}

function fillTableBody(tableId, rows, renderRow) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = '';
  rows.forEach((row) => {
    const tr = document.createElement('tr');
    tr.innerHTML = renderRow(row);
    tbody.appendChild(tr);
  });
  if (rows.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="8">No data available</td>';
    tbody.appendChild(tr);
  }
}

function kvRow(k, v) {
  return `<div class="kv-row"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`;
}

function buildFaq(doctorName) {
  return [
    {
      q: 'What should I do if I miss a medication dose?',
      a: `Take it as soon as remembered unless it is close to next dose. Do not double dose. Confirm with Dr. ${doctorName || 'your care team'} for high-risk meds (blood thinners, insulin, heart meds).`,
    },
    {
      q: 'When should I call the care team?',
      a: 'Call same day for worsening swelling, breathlessness, mild chest discomfort, dizziness, fever, or poor urine output.',
    },
    {
      q: 'When should I go to ER or call 911?',
      a: 'Severe breathlessness at rest, severe chest pain, fainting, confusion, stroke-like symptoms, or oxygen saturation dropping rapidly.',
    },
    {
      q: 'How often should vitals be checked?',
      a: 'At least once daily for weight, blood pressure, pulse, oxygen saturation, and symptoms in the first 2 weeks unless your doctor sets a different plan.',
    },
    {
      q: 'How strict should fluid and diet compliance be?',
      a: 'Follow the discharge advice exactly. For CHF, fluid and salt compliance directly affects readmission risk.',
    },
  ];
}

(function init() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get('id') || localStorage.getItem('oyster_last_extraction_id');
  const emptyState = document.getElementById('empty-state');
  const content = document.getElementById('careplan-content');
  const showEmpty = () => {
    emptyState.classList.remove('hidden');
    content.classList.add('hidden');
  };
  const showContent = () => {
    emptyState.classList.add('hidden');
    content.classList.remove('hidden');
  };

  const render = (data) => {
    showContent();

    const details = data?.extracted_details || {};
    const chf = data?.clinical_modules?.chf || {};
    const firstVisit = (data?.follow_up?.appointments || [])[0] || {};
    const followDoctor = firstVisit?.provider_name || details?.follow_up?.doctor || 'Assigned doctor';

    const snapshotEl = document.getElementById('snapshot');
    snapshotEl.innerHTML = [
      kvRow('Patient', data?.patient?.full_name || 'NA'),
      kvRow('Age/Sex', `${details?.patient?.age_years || 'NA'} / ${data?.patient?.sex_at_birth || 'NA'}`),
      kvRow('Primary Diagnosis', data?.clinical_episode?.primary_diagnosis || 'NA'),
      kvRow('Admission Date', data?.encounter?.admission_datetime || 'NA'),
      kvRow('Discharge Date', data?.encounter?.discharge_datetime || 'NA'),
      kvRow('Follow-Up Doctor', followDoctor),
      kvRow('Follow-Up Date', formatDateTime(firstVisit?.scheduled_datetime)),
    ].join('');

    renderList(document.getElementById('phase-0-7'), data?.care_plan_90d?.phase_0_7 || [], 'No tasks');
    renderList(document.getElementById('phase-8-30'), data?.care_plan_90d?.phase_8_30 || [], 'No tasks');
    renderList(document.getElementById('phase-31-90'), data?.care_plan_90d?.phase_31_90 || [], 'No tasks');

    const meds = data?.medications?.discharge_medications || [];
    fillTableBody('med-table', meds, (m) => {
      const unknown = ['medication_name', 'dose', 'route', 'frequency'].some((k) => (m?.[k] || '').toLowerCase() === 'unknown');
      const rowClass = unknown ? ' class="warn-row"' : '';
      return `<td${rowClass}>${escapeHtml(m?.medication_name || 'NA')}</td>
      <td${rowClass}>${escapeHtml(m?.dose || 'NA')}</td>
      <td${rowClass}>${escapeHtml(m?.route || 'NA')}</td>
      <td${rowClass}>${escapeHtml(m?.frequency || 'NA')}</td>
      <td>${escapeHtml(m?.indication || 'As prescribed')}</td>`;
    });

    const yellow = [
      ...(chf?.red_flags?.yellow_zone || []),
      ...(details?.emergency_signs || []).slice(0, 3),
    ];
    const red = [
      ...(chf?.red_flags?.red_zone || []),
      ...(details?.emergency_signs || []).slice(3),
    ];
    renderList(document.getElementById('yellow-signs'), [...new Set(yellow)], 'No warning signs configured', 'warn');
    renderList(document.getElementById('red-signs'), [...new Set(red)], 'No emergency signs configured', 'bad');

    const weight24 = chf?.red_flags?.weight_gain_trigger_24h_kg ?? 1.0;
    const weight7d = chf?.red_flags?.weight_gain_trigger_7d_kg ?? 2.0;
    const monitorRows = [
    {
      parameter: 'Weight (kg)',
      frequency: chf?.monitoring?.daily_weight_required ? 'Daily' : 'Per clinician plan',
      target: 'Stable around discharge baseline',
      alert: `Gain > ${weight24} kg in 24h OR > ${weight7d} kg in 7 days`,
    },
    {
      parameter: 'Blood Pressure',
      frequency: chf?.monitoring?.bp_required ? 'Daily' : 'Per clinician plan',
      target: 'Typically around 100-140/60-90 (individualized)',
      alert: 'SBP < 90 or > 160, or symptomatic dizziness',
    },
    {
      parameter: 'Heart Rate',
      frequency: chf?.monitoring?.heart_rate_required ? 'Daily' : 'Per clinician plan',
      target: 'Usually 60-100 bpm (individualized)',
      alert: '< 50 or > 110 bpm with symptoms',
    },
    {
      parameter: 'SpO2',
      frequency: 'Daily or with symptoms',
      target: '>= 94% unless clinician target differs',
      alert: '< 92% or persistent drop',
    },
    {
      parameter: 'Temperature',
      frequency: 'Daily if unwell',
      target: '< 100.4 F',
      alert: '>= 100.4 F or chills',
    },
    {
      parameter: 'Symptoms',
      frequency: chf?.monitoring?.symptom_check_required ? 'Daily' : 'Per clinician plan',
      target: 'No worsening breathlessness/edema/chest pain',
      alert: 'Any worsening trend over 24-48 hours',
    },
    ];

    fillTableBody('monitor-table', monitorRows, (r) => `<td>${escapeHtml(r.parameter)}</td>
      <td>${escapeHtml(r.frequency)}</td>
      <td>${escapeHtml(r.target)}</td>
      <td>${escapeHtml(r.alert)}</td>`);

    const visits = data?.follow_up?.appointments || [];
    fillTableBody('visit-table', visits, (v) => `<td>${escapeHtml(v?.appointment_type || 'NA')}</td>
      <td>${escapeHtml(v?.provider_name || 'NA')}</td>
      <td>${escapeHtml(formatDateTime(v?.scheduled_datetime))}</td>
      <td>${escapeHtml(v?.status || 'NA')}</td>`);

    const tests = details?.follow_up?.required_tests || [];
    renderList(document.getElementById('test-list'), tests, 'No tests extracted. Verify with discharge instructions.');

    const dietItems = [];
    if (details?.discharge_advice?.diet) {
      dietItems.push(`Diet: ${details.discharge_advice.diet}`);
    }
    if (details?.discharge_advice?.fluid) {
      dietItems.push(`Fluid limit: ${details.discharge_advice.fluid}`);
    }
    if (details?.discharge_advice?.activity) {
      dietItems.push(`Activity: ${details.discharge_advice.activity}`);
    }
    dietItems.push('Take medications at prescribed times with meal relation as instructed.');
    dietItems.push('Track daily compliance in app and report missed doses.');
    renderList(document.getElementById('diet-checklist'), dietItems, 'No diet guidance extracted.');

    const faqEl = document.getElementById('faq');
    faqEl.innerHTML = buildFaq(followDoctor)
      .map((item) => `<details class="faq-item"><summary>${escapeHtml(item.q)}</summary><p>${escapeHtml(item.a)}</p></details>`)
      .join('');
  };

  if (id) {
    fetch(`/api/extractions/${encodeURIComponent(id)}`)
      .then((res) => {
        if (!res.ok) throw new Error('not found');
        return res.json();
      })
      .then((record) => {
        if (!record?.extraction_json) throw new Error('missing extraction payload');
        localStorage.setItem(STORAGE_KEY, JSON.stringify(record.extraction_json));
        localStorage.setItem('oyster_last_extraction_id', String(record.id));
        render(record.extraction_json);
      })
      .catch(() => {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) {
          showEmpty();
          return;
        }
        try {
          render(JSON.parse(raw));
        } catch {
          showEmpty();
        }
      });
    return;
  }

  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    showEmpty();
    return;
  }
  try {
    render(JSON.parse(raw));
  } catch {
    showEmpty();
  }
})();
