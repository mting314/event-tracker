/* Shared client logic. Datetimes are embedded as ISO strings with +09:00 (JST);
   the browser converts to local time on demand. */

const JST_OPTS = { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit',
  day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false };
const LOCAL_OPTS = { year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false };

function fmtJST(iso) {
  return new Date(iso).toLocaleString('sv-SE', JST_OPTS).replace(' ', ' ') + ' JST';
}
function fmtLocal(iso) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
  return new Date(iso).toLocaleString('sv-SE', LOCAL_OPTS) + ' ' + tz;
}

/* --- timezone toggle (applies to every <time class="dt">) --- */
function applyTz(local) {
  document.querySelectorAll('time.dt').forEach((el) => {
    const iso = el.getAttribute('datetime');
    if (iso) el.textContent = local ? fmtLocal(iso) : fmtJST(iso);
  });
}
(function initTz() {
  const box = document.getElementById('tz-local');
  if (!box) return;
  const saved = localStorage.getItem('tzLocal') === '1';
  box.checked = saved;
  applyTz(saved);
  box.addEventListener('change', () => {
    localStorage.setItem('tzLocal', box.checked ? '1' : '0');
    applyTz(box.checked);
  });
})();

/* --- relative countdown text --- */
function relative(iso) {
  const ms = new Date(iso) - new Date();
  const past = ms < 0;
  let s = Math.abs(ms) / 1000;
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  let txt;
  if (d > 0) txt = `${d}d ${h}h`;
  else if (h > 0) txt = `${h}h ${m}m`;
  else txt = `${m}m`;
  return { txt: past ? `${txt} ago` : `in ${txt}`, past, soon: !past && ms < 72 * 3600e3 };
}
function paintCountdowns() {
  document.querySelectorAll('.countdown').forEach((el) => {
    const iso = el.dataset.iso;
    if (!iso) return;
    const r = relative(iso);
    el.textContent = r.txt;
    el.classList.toggle('soon', r.soon);
    el.classList.toggle('past', r.past);
  });
}

/* --- upcoming: one collapsible row per event, summarised by its next deadline --- */
function initGroups() {
  const showPast = document.getElementById('show-past');
  const empty = document.getElementById('feed-empty');
  const container = document.getElementById('groups');
  const groups = [...container.querySelectorAll('.evgroup')];

  const refresh = () => {
    const now = new Date();
    let visible = 0;
    const rows = groups.map((d) => {
      const future = [...d.querySelectorAll('.occ')]
        .map((o) => o.dataset.iso)
        .filter((iso) => new Date(iso) > now)
        .sort();
      return { d, next: future[0] || null };
    });

    for (const { d, next } of rows) {
      const li = d.closest('li');
      li.hidden = !next && !showPast.checked;
      if (li.hidden) continue;
      visible++;
      const badge = d.querySelector('summary .next-badge');
      const cd = d.querySelector('summary .countdown');
      const when = d.querySelector('summary .next-when');
      const occEl = next && [...d.querySelectorAll('.occ')].find((o) => o.dataset.iso === next);
      if (occEl) {
        const src = occEl.querySelector('.badge');
        badge.className = 'next-badge ' + src.className; // copies "badge <css>"
        badge.textContent = src.textContent;
        cd.dataset.iso = next;
        when.setAttribute('datetime', next);
      } else {
        badge.className = 'next-badge'; badge.textContent = 'past';
        delete cd.dataset.iso; cd.textContent = '';
        when.removeAttribute('datetime'); when.textContent = '';
      }
    }

    // soonest-next first; hidden/no-next sink to the bottom
    rows
      .filter((r) => !r.d.closest('li').hidden)
      .sort((a, b) => (a.next || '9999').localeCompare(b.next || '9999'))
      .forEach((r) => container.appendChild(r.d.closest('li')));

    paintCountdowns();
    applyTz(document.getElementById('tz-local')?.checked);
    if (empty) empty.hidden = visible > 0;
  };

  showPast.addEventListener('change', refresh);
  refresh();
  setInterval(paintCountdowns, 60000);
}

/* --- catalog search/filter --- */
function initCatalog() {
  const q = document.getElementById('q');
  const openOnly = document.getElementById('open-only');
  const kind = document.getElementById('kind');
  const empty = document.getElementById('catalog-empty');
  const rows = [...document.querySelectorAll('#catalog .evrow')];
  const apply = () => {
    const term = q.value.trim().toLowerCase();
    const now = new Date();
    let visible = 0;
    rows.forEach((tr) => {
      const matchText = !term || tr.dataset.haystack.includes(term);
      const matchKind = !kind.value || tr.dataset.kind === kind.value;
      let hasOpen = true;
      if (openOnly.checked) {
        hasOpen = tr.dataset.deadlines.split(',').some((d) => d && new Date(d) > now);
      }
      const show = matchText && matchKind && hasOpen;
      tr.hidden = !show;
      if (show) visible++;
    });
    if (empty) empty.hidden = visible > 0;
  };
  q.addEventListener('input', apply);
  openOnly.addEventListener('change', apply);
  kind.addEventListener('change', apply);
  apply();
}

/* --- event detail (countdowns on each date cell + .ics) --- */
function initEventDetail() {
  document.querySelectorAll('.countdown-cell').forEach((el) => {
    const iso = el.getAttribute('datetime');
    const r = relative(iso);
    const span = document.createElement('span');
    span.className = 'countdown' + (r.soon ? ' soon' : '') + (r.past ? ' past' : '');
    span.dataset.iso = iso;
    span.textContent = ' (' + r.txt + ')';
    el.after(span);
  });
  document.querySelectorAll('a.ics').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      downloadICS(a.dataset.title, a.dataset.iso);
    });
  });
}

function downloadICS(title, iso) {
  const dt = new Date(iso);
  const stamp = (d) => d.toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  const ics = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ll-lottery-tracker//EN',
    'BEGIN:VEVENT', `UID:${iso}-${Math.abs(hash(title))}@ll-lottery-tracker`,
    `DTSTAMP:${stamp(new Date(iso))}`, `DTSTART:${stamp(dt)}`,
    `DTEND:${stamp(dt)}`, `SUMMARY:${title.replace(/\n/g, ' ')}`,
    'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n');
  const blob = new Blob([ics], { type: 'text/calendar' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = title.slice(0, 40).replace(/[^\w]+/g, '_') + '.ics';
  link.click();
  URL.revokeObjectURL(url);
}
function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return h; }

/* --- calendar --- */
const CAL_FIELDS = {
  apply_open: 'opens', apply_deadline: 'deadline',
  results_date: 'results', payment_deadline: 'payment',
};
async function initCalendar(url) {
  const res = await fetch(url);
  const { events } = await res.json();
  const items = []; // {date:'YYYY-MM-DD', cls, label, id}
  const jstDay = (iso) => new Date(iso).toLocaleDateString('sv-SE', { timeZone: 'Asia/Tokyo' });
  for (const ev of events) {
    for (const r of ev.rounds || []) {
      for (const [f, cls] of Object.entries(CAL_FIELDS)) {
        if (r[f]) items.push({ date: jstDay(r[f]), cls, label: `${ev.name} ${r.name}`, id: ev.id });
      }
    }
    for (const d of ev.event_dates || []) {
      items.push({ date: d, cls: 'event', label: ev.name, id: ev.id });
    }
  }
  let cur = new Date();
  cur = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const render = () => {
    const y = cur.getFullYear(), mo = cur.getMonth();
    document.getElementById('cal-label').textContent =
      cur.toLocaleString('en-US', { month: 'long', year: 'numeric' });
    const grid = document.getElementById('calendar-grid');
    grid.innerHTML = '';
    ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach((d) => {
      const c = document.createElement('div'); c.className = 'cal-cell dow'; c.textContent = d; grid.append(c);
    });
    const first = new Date(y, mo, 1);
    const offset = (first.getDay() + 6) % 7; // Monday-first
    const start = new Date(y, mo, 1 - offset);
    for (let i = 0; i < 42; i++) {
      const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      const iso = day.toLocaleDateString('sv-SE');
      const cell = document.createElement('div');
      cell.className = 'cal-cell' + (day.getMonth() !== mo ? ' other' : '');
      const num = document.createElement('div'); num.className = 'cal-daynum'; num.textContent = day.getDate();
      cell.append(num);
      items.filter((it) => it.date === iso).forEach((it) => {
        const a = document.createElement('a');
        a.className = `cal-ev ${it.cls}`; a.textContent = it.label;
        a.title = it.label; a.href = `event/${it.id}.html`;
        cell.append(a);
      });
      grid.append(cell);
    }
  };
  document.getElementById('cal-prev').onclick = () => { cur.setMonth(cur.getMonth() - 1); render(); };
  document.getElementById('cal-next').onclick = () => { cur.setMonth(cur.getMonth() + 1); render(); };
  render();
}

/* --- add/edit form (milestone 3): build YAML in-browser -> GitHub PR --- */
const ROUND_DATE_KEYS = ['apply_open', 'apply_deadline', 'results_date', 'payment_deadline'];

function yamlScalar(v) {
  if (v == null || v === '') return '';
  const s = String(v);
  if (/^[\s]|[\s]$|[:#\[\]{}",&*!|>%@`]/.test(s) || /^(true|false|null|~|\d)/i.test(s)) {
    return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }
  return s;
}
function dtLocalToYaml(v) { return v ? (v.length === 16 ? v + ':00' : v) : null; }

function collectEvent(form) {
  const g = (n) => form.querySelector(`[name="${n}"]`).value.trim();
  const lines = (n) => g(n).split('\n').map((s) => s.trim()).filter(Boolean);
  const csv = (n) => g(n).split(',').map((s) => s.trim()).filter(Boolean);
  const performances = [...form.querySelectorAll('#performances fieldset')].map((fs) => {
    const pg = (n) => fs.querySelector(`[name="${n}"]`).value.trim();
    return { date: pg('p_date'), city: pg('p_city') || null, label: pg('p_label') || null,
      venue: pg('p_venue') || null, venue_address: pg('p_venue_address') || null,
      doors: pg('p_doors') || null, starts: pg('p_starts') || null };
  });
  const rounds = [...form.querySelectorAll('#rounds fieldset')].map((fs) => {
    const rg = (n) => fs.querySelector(`[name="${n}"]`).value.trim();
    return { name: rg('r_name'), type: rg('r_type') || null, leg: rg('r_leg') || null,
      apply_open: dtLocalToYaml(rg('r_apply_open')),
      apply_deadline: dtLocalToYaml(rg('r_apply_deadline')),
      results_date: dtLocalToYaml(rg('r_results_date')),
      payment_deadline: dtLocalToYaml(rg('r_payment_deadline')),
      apply_url: rg('r_apply_url') || null, notes: rg('r_notes') || null };
  });
  return { id: g('id'), name: g('name'), name_en: g('name_en') || null,
    artist: g('artist') || null, kind: g('kind') || null,
    series: csv('series'), categories: csv('categories'), performers: lines('performers'),
    performances,
    eventernote_url: g('eventernote_url') || null, official_url: g('official_url') || null,
    source_url: g('source_url') || null,
    notes: g('notes') || null, rounds };
}

function validateEvent(ev) {
  const errs = [];
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(ev.id)) errs.push('slug/id must be lowercase letters, digits and hyphens');
  if (!ev.name) errs.push('name is required');
  ev.performances.forEach((p, i) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(p.date)) errs.push(`performance ${i + 1}: date must be YYYY-MM-DD`);
  });
  ev.rounds.forEach((r, i) => {
    if (!r.name) errs.push(`round ${i + 1}: name required`);
    if (!ROUND_DATE_KEYS.some((k) => r[k])) errs.push(`round ${i + 1} ("${r.name || '?'}"): needs at least one date`);
  });
  return errs;
}

function buildYaml(ev) {
  const L = [];
  L.push(`name: ${yamlScalar(ev.name)}`);
  if (ev.name_en) L.push(`name_en: ${yamlScalar(ev.name_en)}`);
  if (ev.artist) L.push(`artist: ${yamlScalar(ev.artist)}`);
  if (ev.kind) L.push(`kind: ${yamlScalar(ev.kind)}`);
  if (ev.series.length) { L.push('series:'); ev.series.forEach((s) => L.push(`  - ${yamlScalar(s)}`)); }
  if (ev.categories.length) { L.push('categories:'); ev.categories.forEach((s) => L.push(`  - ${yamlScalar(s)}`)); }
  if (ev.performers.length) { L.push('performers:'); ev.performers.forEach((s) => L.push(`  - ${yamlScalar(s)}`)); }
  if (ev.performances.length) {
    L.push('performances:');
    ev.performances.forEach((p) => {
      L.push(`  - date: ${p.date}`);
      if (p.city) L.push(`    city: ${yamlScalar(p.city)}`);
      if (p.label) L.push(`    label: ${yamlScalar(p.label)}`);
      if (p.venue) L.push(`    venue: ${yamlScalar(p.venue)}`);
      if (p.venue_address) L.push(`    venue_address: ${yamlScalar(p.venue_address)}`);
      if (p.doors) L.push(`    doors: ${yamlScalar(p.doors)}`);
      if (p.starts) L.push(`    starts: ${yamlScalar(p.starts)}`);
    });
  }
  if (ev.eventernote_url) L.push(`eventernote_url: ${ev.eventernote_url}`);
  if (ev.official_url) L.push(`official_url: ${ev.official_url}`);
  if (ev.source_url) L.push(`source_url: ${ev.source_url}`);
  if (ev.notes) L.push(`notes: ${yamlScalar(ev.notes)}`);
  if (ev.rounds.length) {
    L.push('rounds:');
    ev.rounds.forEach((r) => {
      L.push(`  - name: ${yamlScalar(r.name)}`);
      if (r.type) L.push(`    type: ${yamlScalar(r.type)}`);
      if (r.leg) L.push(`    leg: ${yamlScalar(r.leg)}`);
      ROUND_DATE_KEYS.forEach((k) => { if (r[k]) L.push(`    ${k}: ${r[k]}`); });
      if (r.apply_url) L.push(`    apply_url: ${r.apply_url}`);
      if (r.notes) L.push(`    notes: ${yamlScalar(r.notes)}`);
    });
  }
  return L.join('\n') + '\n';
}

function isoToJstInput(iso) {
  if (!iso) return '';
  const p = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo', year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
    .formatToParts(new Date(iso)).reduce((a, x) => (a[x.type] = x.value, a), {});
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

function initAddForm(eventsUrl) {
  const form = document.getElementById('event-form');
  const out = document.getElementById('yaml-out');
  const errBox = document.getElementById('form-error');
  const repo = document.getElementById('gh-repo');
  const branch = document.getElementById('gh-branch');
  const editApi = document.getElementById('edit-api');
  const editSecret = document.getElementById('edit-secret');
  repo.value = localStorage.getItem('ghRepo') || '';
  branch.value = localStorage.getItem('ghBranch') || 'main';
  editApi.value = localStorage.getItem('editApi') || '';
  editSecret.value = localStorage.getItem('editSecret') || '';
  repo.addEventListener('change', () => localStorage.setItem('ghRepo', repo.value.trim()));
  branch.addEventListener('change', () => localStorage.setItem('ghBranch', branch.value.trim()));
  editApi.addEventListener('change', () => localStorage.setItem('editApi', editApi.value.trim()));
  editSecret.addEventListener('change', () => localStorage.setItem('editSecret', editSecret.value.trim()));

  const perfTpl = document.getElementById('perf-tpl');
  const perfBox = document.getElementById('performances');
  const addPerf = (data) => {
    const node = perfTpl.content.cloneNode(true);
    if (data) {
      const set = (n, v) => { if (v) node.querySelector(`[name="${n}"]`).value = v; };
      set('p_date', data.date); set('p_city', data.city); set('p_label', data.label);
      set('p_venue', data.venue); set('p_venue_address', data.venue_address);
      set('p_doors', data.doors); set('p_starts', data.starts);
    }
    perfBox.append(node);
  };
  document.getElementById('add-perf').addEventListener('click', () => addPerf());
  perfBox.addEventListener('click', (e) => {
    if (e.target.classList.contains('remove-perf')) e.target.closest('fieldset').remove();
  });

  const tpl = document.getElementById('round-tpl');
  const roundsBox = document.getElementById('rounds');
  const addRound = (data) => {
    const node = tpl.content.cloneNode(true);
    if (data) {
      const set = (n, v) => { if (v) node.querySelector(`[name="${n}"]`).value = v; };
      set('r_name', data.name); set('r_type', data.type); set('r_leg', data.leg);
      set('r_apply_open', isoToJstInput(data.apply_open));
      set('r_apply_deadline', isoToJstInput(data.apply_deadline));
      set('r_results_date', isoToJstInput(data.results_date));
      set('r_payment_deadline', isoToJstInput(data.payment_deadline));
      set('r_apply_url', data.apply_url); set('r_notes', data.notes);
    }
    roundsBox.append(node);
  };
  document.getElementById('add-round').addEventListener('click', () => addRound());
  roundsBox.addEventListener('click', (e) => {
    if (e.target.classList.contains('remove-round')) e.target.closest('fieldset').remove();
  });

  const build = () => {
    const ev = collectEvent(form);
    const errs = validateEvent(ev);
    if (errs.length) {
      errBox.hidden = false; errBox.textContent = '⚠ ' + errs.join(' · ');
      out.hidden = true; return null;
    }
    errBox.hidden = true;
    const yaml = buildYaml(ev);
    out.hidden = false; out.textContent = yaml;
    return { ev, yaml };
  };
  document.getElementById('build').addEventListener('click', build);
  document.getElementById('save').addEventListener('click', async () => {
    const r = build();
    if (!r) return;
    const api = (editApi.value || '').trim();
    const secret = (editSecret.value || '').trim();
    if (!api || !secret) {
      errBox.hidden = false; errBox.style.color = '';
      errBox.textContent = '⚠ set Edit API URL + Admin secret in Config to Save directly';
      return;
    }
    const btn = document.getElementById('save');
    btn.disabled = true; const label = btn.textContent; btn.textContent = 'Saving…';
    errBox.hidden = false; errBox.style.color = ''; errBox.textContent = 'Saving…';
    try {
      const resp = await fetch(api, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Secret': secret },
        body: JSON.stringify({ slug: r.ev.id, yaml: r.yaml }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        errBox.textContent = `✓ ${data.updated ? 'Updated' : 'Created'} events/${r.ev.id}.yaml — live in ~1 min`
          + (data.commit ? ` · ${data.commit}` : '');
      } else {
        errBox.style.color = ''; errBox.textContent = `⚠ Save failed: ${data.error || resp.status}`;
      }
    } catch (e) {
      errBox.textContent = `⚠ Save failed: ${e}`;
    } finally {
      btn.disabled = false; btn.textContent = label;
    }
  });
  document.getElementById('copy').addEventListener('click', async () => {
    const r = build(); if (r) { await navigator.clipboard.writeText(r.yaml); errBox.hidden = false; errBox.style.color = ''; errBox.textContent = '✓ copied'; }
  });
  document.getElementById('download').addEventListener('click', () => {
    const r = build(); if (!r) return;
    const blob = new Blob([r.yaml], { type: 'text/yaml' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `${r.ev.id}.yaml`; a.click(); URL.revokeObjectURL(a.href);
  });
  document.getElementById('pr').addEventListener('click', () => {
    const r = build(); if (!r) return;
    const slug = (repo.value || '').trim();
    if (!slug) { errBox.hidden = false; errBox.textContent = '⚠ set the GitHub repo (owner/repo) first'; return; }
    const url = `https://github.com/${slug}/new/${(branch.value || 'main').trim()}` +
      `?filename=events/${r.ev.id}.yaml&value=${encodeURIComponent(r.yaml)}`;
    if (url.length > 8000) { errBox.hidden = false; errBox.textContent = '⚠ YAML too long for a PR link — use Download instead'; return; }
    window.open(url, '_blank', 'noopener');
  });

  // edit mode: ?edit=<id> prefills from events.json
  const editId = new URLSearchParams(location.search).get('edit');
  if (editId) {
    fetch(eventsUrl).then((r) => r.json()).then(({ events }) => {
      const ev = events.find((e) => e.id === editId);
      if (!ev) return;
      document.getElementById('form-title').textContent = `Edit event: ${ev.id}`;
      const set = (n, v) => { if (v != null) form.querySelector(`[name="${n}"]`).value = v; };
      set('id', ev.id); set('name', ev.name); set('name_en', ev.name_en);
      set('artist', ev.artist); set('kind', ev.kind);
      set('series', (ev.series || []).join(', ')); set('categories', (ev.categories || []).join(', '));
      set('performers', (ev.performers || []).join('\n'));
      set('eventernote_url', ev.eventernote_url); set('official_url', ev.official_url);
      set('source_url', ev.source_url); set('notes', ev.notes);
      // lock the slug so editing updates this file (changing it would fork a new one)
      const idInput = form.querySelector('[name="id"]');
      idInput.readOnly = true;
      idInput.title = 'Locked while editing — this is the file being updated';
      (ev.performances || []).forEach(addPerf);
      (ev.rounds || []).forEach(addRound);
    });
  } else {
    addPerf();
    addRound();
  }
}
