const STORAGE_KEY = 'oyster_last_extraction';

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

function toDateOnly(value) {
  if (!value) return null;
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return null;
  dt.setHours(0, 0, 0, 0);
  return dt;
}

function toIsoDate(value) {
  const dt = toDateOnly(value);
  if (!dt) return null;
  return dt.toISOString().slice(0, 10);
}

function plusDays(dateObj, days) {
  const d = new Date(dateObj);
  d.setDate(d.getDate() + days);
  return d;
}

function weekdaysHeader() {
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    .map((d) => `<div class="cal-weekday">${d}</div>`)
    .join('');
}

function buildDailyEvents(data, dayDate, dayIndex) {
  const items = [];
  const meds = data?.medications?.discharge_medications || [];
  const details = data?.extracted_details || {};
  const chf = data?.clinical_modules?.chf || {};
  const followups = data?.follow_up?.appointments || [];
  const tests = details?.follow_up?.required_tests || [];

  if (Array.isArray(meds) && meds.length) {
    const medPreview = meds.slice(0, 4).map((m) => {
      const name = m?.medication_name || 'Medication';
      const freq = m?.frequency || 'as prescribed';
      return `${name} (${freq})`;
    });
    items.push({ kind: 'med', text: `Medications: ${medPreview.join(', ')}` });
  }

  const monitor = [];
  if (chf?.monitoring?.daily_weight_required) monitor.push('weight');
  if (chf?.monitoring?.bp_required) monitor.push('BP');
  if (chf?.monitoring?.heart_rate_required) monitor.push('pulse');
  if (chf?.monitoring?.symptom_check_required) monitor.push('symptoms');
  monitor.push('SpO2');
  if (monitor.length) {
    items.push({ kind: 'measure', text: `Measurements: ${monitor.join(', ')}` });
  }

  const advice = details?.discharge_advice || {};
  if (advice?.diet || advice?.fluid) {
    items.push({
      kind: 'diet',
      text: `Diet/Fluid: ${advice?.diet || 'follow advised diet'}${advice?.fluid ? ` | ${advice.fluid}` : ''}`,
    });
  }

  if (dayIndex === 0 || dayIndex === 2 || dayIndex === 6 || (dayIndex > 6 && dayIndex % 7 === 0)) {
    items.push({ kind: 'checkin', text: 'Nurse check-in and symptom review' });
  }

  if (dayIndex === 30 || dayIndex === 60 || dayIndex === 90) {
    items.push({ kind: 'checkin', text: 'Milestone review and care-plan adjustment' });
  }

  const dayIso = toIsoDate(dayDate);
  if (Array.isArray(followups)) {
    followups.forEach((appt) => {
      if (!appt?.scheduled_datetime) return;
      if (toIsoDate(appt.scheduled_datetime) === dayIso) {
        items.push({
          kind: 'followup',
          text: `Follow-up: ${appt?.appointment_type || 'visit'} with ${appt?.provider_name || 'doctor'}`,
        });
      }
    });
  }

  if (Array.isArray(tests) && tests.length) {
    if (dayIndex === 14 || dayIndex === 45) {
      items.push({ kind: 'test', text: `Tests: ${tests.join(', ')}` });
    }
  }

  return items;
}

function renderCalendar(data) {
  const empty = document.getElementById('calendar-empty');
  const content = document.getElementById('calendar-content');
  const snapshot = document.getElementById('calendar-snapshot');
  const grid = document.getElementById('calendar-grid');

  empty.classList.add('hidden');
  content.classList.remove('hidden');

  const startDateStr = data?.care_plan_90d?.start_date || (data?.encounter?.discharge_datetime || '').slice(0, 10);
  const startDate = toDateOnly(startDateStr) || toDateOnly(new Date());
  const endDate = plusDays(startDate, 89);
  const extractionId = data?.extraction_id || localStorage.getItem('oyster_last_extraction_id') || 'NA';

  snapshot.innerHTML = [
    kvRow('Extraction ID', extractionId),
    kvRow('Patient', data?.patient?.full_name || 'NA'),
    kvRow('Primary Diagnosis', data?.clinical_episode?.primary_diagnosis || 'NA'),
    kvRow('Start Date', startDate.toISOString().slice(0, 10)),
    kvRow('End Date', endDate.toISOString().slice(0, 10)),
  ].join('');

  const cells = [weekdaysHeader()];
  const startWeekday = startDate.getDay();
  for (let i = 0; i < startWeekday; i += 1) {
    cells.push('<div class="cal-day cal-day-empty"></div>');
  }

  for (let i = 0; i < 90; i += 1) {
    const dayDate = plusDays(startDate, i);
    const dayEvents = buildDailyEvents(data, dayDate, i);
    const eventHtml = dayEvents
      .map((ev) => `<li class="cal-item ${escapeHtml(`cal-${ev.kind}`)}">${escapeHtml(ev.text)}</li>`)
      .join('');

    cells.push(`
      <article class="cal-day">
        <div class="cal-day-head">
          <span class="cal-day-num">Day ${i + 1}</span>
          <span class="cal-date">${escapeHtml(dayDate.toISOString().slice(0, 10))}</span>
        </div>
        <ul class="cal-list">${eventHtml || '<li class="cal-item">No specific activity</li>'}</ul>
      </article>
    `);
  }

  grid.innerHTML = cells.join('');
}

(function init() {
  const empty = document.getElementById('calendar-empty');
  const params = new URLSearchParams(window.location.search);
  const id = params.get('id') || localStorage.getItem('oyster_last_extraction_id');

  const renderFallback = () => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      empty.classList.remove('hidden');
      return;
    }
    try {
      renderCalendar(JSON.parse(raw));
    } catch {
      empty.classList.remove('hidden');
    }
  };

  if (!id) {
    renderFallback();
    return;
  }

  fetch(`/api/extractions/${encodeURIComponent(id)}`)
    .then((res) => {
      if (!res.ok) throw new Error('Failed to load extraction');
      return res.json();
    })
    .then((record) => {
      const payload = record?.extraction_json;
      if (!payload) throw new Error('No extraction payload');
      payload.extraction_id = record.id;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
      localStorage.setItem('oyster_last_extraction_id', String(record.id));
      renderCalendar(payload);
    })
    .catch(() => {
      renderFallback();
    });
})();

