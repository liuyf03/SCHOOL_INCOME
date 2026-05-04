const SEARCH_INDEX_URL = '../data/processed/search_index.json';
const SCHOOLS_URL = '../data/processed/schools_wa.json';

const REASON_LABELS = {
  missing_sabs:
    "This school's attendance boundary was not available, so an income estimate could not be computed.",
  low_household_count:
    'Very few households fall in this attendance zone — small-sample noise dominates the estimate.',
};

const el = {
  input: document.getElementById('search-input'),
  results: document.getElementById('search-results'),
  panel: document.getElementById('result-panel'),
  placeholder: document.getElementById('placeholder'),
  banner: document.getElementById('low-confidence-banner'),
  name: document.getElementById('school-name'),
  district: document.getElementById('school-district'),
  grades: document.getElementById('school-grades'),
  address: document.getElementById('school-address'),
  statMedian: document.getElementById('stat-median'),
  statUnder35: document.getElementById('stat-under35'),
  statOver150: document.getElementById('stat-over150'),
  statTotal: document.getElementById('stat-total'),
  canvas: document.getElementById('histogram'),
  toggleCount: document.getElementById('toggle-count'),
  togglePercent: document.getElementById('toggle-percent'),
};

let miniSearch = null;
let schoolsCache = null;
let chart = null;
let mode = 'count';
let currentRecord = null;

async function loadSearchIndex() {
  const res = await fetch(SEARCH_INDEX_URL);
  if (!res.ok) throw new Error(`Failed to load search index: ${res.status}`);
  const docs = await res.json();
  miniSearch = new MiniSearch({
    fields: ['name', 'district', 'city'],
    storeFields: ['nces_id', 'name', 'district', 'city'],
    idField: 'nces_id',
  });
  miniSearch.addAll(docs);
}

async function loadSchools() {
  if (schoolsCache) return schoolsCache;
  const res = await fetch(SCHOOLS_URL);
  if (!res.ok) throw new Error(`Failed to load schools: ${res.status}`);
  schoolsCache = await res.json();
  return schoolsCache;
}

function fmtCurrency(n) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

function fmtPercent(n) {
  if (n == null) return '—';
  return (n * 100).toFixed(1) + '%';
}

function fmtCount(n) {
  if (n == null) return '—';
  return Math.round(n).toLocaleString('en-US');
}

function renderResults(matches) {
  el.results.innerHTML = '';
  if (matches.length === 0) {
    el.results.hidden = true;
    return;
  }
  for (const match of matches.slice(0, 8)) {
    const li = document.createElement('li');
    li.textContent = `${match.name} — ${match.district}, ${match.city}`;
    li.dataset.ncesId = match.nces_id;
    li.addEventListener('click', () => selectSchool(match.nces_id));
    el.results.appendChild(li);
  }
  el.results.hidden = false;
}

async function selectSchool(ncesId) {
  const schools = await loadSchools();
  const record = schools[ncesId];
  if (!record) return;

  el.results.hidden = true;
  el.input.value = record.name;
  el.placeholder.hidden = true;
  el.panel.hidden = false;

  el.name.textContent = record.name;
  el.district.textContent = record.district;
  el.grades.textContent = `Grades ${record.grades}`;
  el.address.textContent = record.address;
  el.statMedian.textContent = fmtCurrency(record.median_family_income);
  el.statUnder35.textContent = fmtPercent(record.share_under_35k);
  el.statOver150.textContent = fmtPercent(record.share_over_150k);
  el.statTotal.textContent = fmtCount(record.total_families_with_children);

  if (record.low_confidence) {
    const reasons = record.low_confidence_reasons
      .map((r) => REASON_LABELS[r] || r)
      .join(' ');
    el.banner.textContent = `⚠ Low-confidence estimate. ${reasons}`;
    el.banner.hidden = false;
  } else {
    el.banner.hidden = true;
  }

  currentRecord = record;
  renderHistogram(record);
}

function renderHistogram(record) {
  if (chart) chart.destroy();
  const buckets = record.bracket_histogram;
  const total = record.total_families_with_children;
  const isPercent = mode === 'percent';
  const data = isPercent
    ? buckets.map((b) => (total > 0 ? Math.round((b.count / total) * 1000) / 10 : 0))
    : buckets.map((b) => b.count);
  const yTitle = isPercent ? '% of families' : 'Families';

  chart = new Chart(el.canvas, {
    type: 'bar',
    data: {
      labels: buckets.map((b) => b.label),
      datasets: [
        {
          label: isPercent ? '% of families' : 'Estimated families',
          data,
          backgroundColor: '#3b6fa3',
        },
      ],
    },
    options: {
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) =>
              isPercent
                ? `${ctx.parsed.y.toFixed(1)}%`
                : `${Math.round(ctx.parsed.y).toLocaleString('en-US')} families`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: yTitle },
          ticks: isPercent ? { callback: (v) => v + '%' } : {},
        },
      },
    },
  });
}

function setMode(newMode) {
  if (mode === newMode) return;
  mode = newMode;
  el.toggleCount.classList.toggle('active', mode === 'count');
  el.toggleCount.setAttribute('aria-pressed', String(mode === 'count'));
  el.togglePercent.classList.toggle('active', mode === 'percent');
  el.togglePercent.setAttribute('aria-pressed', String(mode === 'percent'));
  if (currentRecord) renderHistogram(currentRecord);
}

el.toggleCount.addEventListener('click', () => setMode('count'));
el.togglePercent.addEventListener('click', () => setMode('percent'));

el.input.addEventListener('input', (event) => {
  if (!miniSearch) return;
  const q = event.target.value.trim();
  if (q.length < 2) {
    el.results.hidden = true;
    return;
  }
  const matches = miniSearch.search(q, { prefix: true, fuzzy: 0.2 });
  renderResults(matches);
});

document.addEventListener('click', (event) => {
  if (!el.results.contains(event.target) && event.target !== el.input) {
    el.results.hidden = true;
  }
});

loadSearchIndex().catch((err) => {
  console.error('Failed to load search index', err);
  el.placeholder.textContent = 'Failed to load school data — check the browser console.';
});
