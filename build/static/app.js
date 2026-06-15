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

/* --- i18n: JP/EN localization (same shape as the-sorter: locale resource
   bundles + a persisted language toggle + per-record localized fields).
   UI chrome uses [data-i18n] keys into I18N; per-record data (event/round names,
   badges) uses .i18n-field elements carrying data-ja / data-en. --- */
const I18N = {
  en: {
    nav_events: 'Events', nav_calendar: 'Calendar', nav_past: 'Past', nav_add: '+ Add',
    tz_local: 'Show local time',
    idx_title: 'Upcoming',
    idx_hint: 'One row per event, showing its next deadline. Click to expand rounds & shows.',
    active_title: 'Open now',
    fr_lovelive: 'Love Live!', fr_project_sekai: 'Project Sekai', fr_other: 'Other',
    open_event: 'open event page →',
    no_rounds: 'No lottery rounds recorded yet.',
    feed_empty: 'Nothing upcoming. 🎉',
    apply: 'Apply ↗',
    past_badge: 'past',
    expand_all: 'Expand all', collapse_all: 'Collapse all',
    cat_search: 'Search name, artist, series, venue, performer…',
    cat_all_kinds: 'All kinds',
    cat_all_series: 'All series',
    cat_empty: 'No matching events.',
    open_round: 'has open round',
    th_event: 'Event', th_dates: 'Dates', th_series: 'Series',
    cal_title: 'Calendar',
    past_title: 'Past events',
    past_search: 'Search name or series…',
    detail_back: '← Events', detail_edit: '✎ Edit event',
    detail_artist: 'Artist', detail_series: 'Series', detail_dates: 'Dates', detail_cast: 'Cast',
    detail_perfs: 'Performances & deadlines', th_round: 'Round',
    no_rounds_show: 'No lottery rounds recorded for this show yet.',
    footer_a: 'Tracking', footer_b: 'events · all times JST unless toggled · curated from',
    footer_c: 'official sources',
    past_hint_a: 'Archive of past Love Live! events from',
    past_hint_b: '— reference only (no lottery rounds).', past_hint_c: 'events.',
    // add / edit form
    add_title: 'Add an event (tour)', edit_title_prefix: 'Edit event: ',
    add_hint: 'An event is a whole tour. Add each show as a performance, and lottery rounds '
      + "(tag a round's leg if it only applies to part of the tour). Preview the YAML, then "
      + 'open a prefilled GitHub PR or download the file.',
    cfg_summary: 'Config (set the admin secret once — stored in your browser)',
    cfg_secret: 'Admin secret',
    f_name_ph: 'Tour name (JP)…',
    f_slug: 'Slug / id', f_name_en: 'Tour name (EN)', f_artist: 'Artist / organizer',
    f_kind: 'Kind', f_series: 'Series (comma-sep)', f_categories: 'Categories (comma-sep)',
    f_eventernote: 'eventernote URL', f_official: 'Official URL', f_source: 'Source URL',
    f_source_ph: 'where this was ingested from',
    f_performers: 'Performers (one per line)', f_notes: 'Notes',
    sec_perfs: 'Performances', sec_perfs_note: '(one per show / day)', btn_add_perf: '+ Add performance',
    sec_rounds: 'Lottery rounds', sec_rounds_note: '(times are JST)', btn_add_round: '+ Add round',
    btn_save: '💾 Save', btn_preview: 'Preview YAML', btn_copy: 'Copy',
    btn_download: 'Download .yaml', btn_delete: '🗑 Delete event',
    f_date: 'Date (YYYY-MM-DD)', f_leg_city: 'Leg / city', f_label: 'Label', f_venue: 'Venue',
    f_venue_addr: 'Venue address', f_doors: 'Doors', f_starts: 'Starts',
    btn_remove_perf: '✕ remove performance',
    f_round_name: 'Round name', f_round_name_en: 'Round name (EN)', f_type: 'Type', f_round_leg: 'Leg',
    f_apply_open: 'Apply opens (JST)', f_apply_deadline: 'Apply deadline (JST)',
    f_results: 'Results (JST)', f_payment: 'Payment deadline (JST)', f_apply_url: 'Apply URL',
    btn_remove_round: '✕ remove round',
  },
  ja: {
    nav_events: 'イベント', nav_calendar: 'カレンダー', nav_past: '過去', nav_add: '＋追加',
    tz_local: '現地時間で表示',
    idx_title: '開催予定',
    idx_hint: 'イベントごとに次の締切を表示。クリックで申込回・公演を展開。',
    active_title: '受付中',
    fr_lovelive: 'ラブライブ！', fr_project_sekai: 'プロジェクトセカイ', fr_other: 'その他',
    open_event: 'イベントページを開く →',
    no_rounds: '抽選回はまだ登録されていません。',
    feed_empty: '予定はありません。🎉',
    apply: '申込 ↗',
    past_badge: '終了',
    expand_all: 'すべて展開', collapse_all: 'すべて折りたたむ',
    cat_search: '名前・アーティスト・シリーズ・会場・出演者で検索…',
    cat_all_kinds: 'すべての種別',
    cat_all_series: 'すべてのシリーズ',
    cat_empty: '該当するイベントはありません。',
    open_round: '受付中あり',
    th_event: 'イベント', th_dates: '日程', th_series: 'シリーズ',
    cal_title: 'カレンダー',
    past_title: '過去のイベント',
    past_search: '名前・シリーズで検索…',
    detail_back: '← イベント', detail_edit: '✎ 編集',
    detail_artist: 'アーティスト', detail_series: 'シリーズ', detail_dates: '日程', detail_cast: '出演',
    detail_perfs: '公演・締切', th_round: '抽選回',
    no_rounds_show: 'この公演の抽選回はまだ登録されていません。',
    footer_a: '追跡中:', footer_b: '件 · 時刻は特記なき限りJST · 出典:',
    footer_c: '公式情報',
    past_hint_a: '過去のラブライブ！イベント（出典:',
    past_hint_b: '）参考用・抽選情報なし。', past_hint_c: '件',
    // add / edit form
    add_title: 'イベントを追加（ツアー）', edit_title_prefix: '編集: ',
    add_hint: 'イベントはツアー全体です。各公演を performance として追加し、抽選回を登録します'
      + '（ツアーの一部のみに適用される回には leg を設定）。YAMLをプレビューしてから、'
      + 'プリフィル済みのGitHub PRを開くかファイルをダウンロードします。',
    cfg_summary: '設定（管理シークレットを一度だけ設定 — ブラウザに保存）',
    cfg_secret: '管理シークレット',
    f_name_ph: 'ツアー名（日本語）…',
    f_slug: 'スラッグ / ID', f_name_en: 'ツアー名（英語）', f_artist: 'アーティスト / 主催',
    f_kind: '種別', f_series: 'シリーズ（カンマ区切り）', f_categories: 'カテゴリ（カンマ区切り）',
    f_eventernote: 'eventernote URL', f_official: '公式URL', f_source: 'ソースURL',
    f_source_ph: '取得元',
    f_performers: '出演者（1行に1人）', f_notes: '備考',
    sec_perfs: '公演', sec_perfs_note: '（公演・日ごとに1つ）', btn_add_perf: '＋公演を追加',
    sec_rounds: '抽選回', sec_rounds_note: '（時刻はJST）', btn_add_round: '＋抽選回を追加',
    btn_save: '💾 保存', btn_preview: 'YAMLプレビュー', btn_copy: 'コピー',
    btn_download: 'YAMLをダウンロード', btn_delete: '🗑 イベントを削除',
    f_date: '日付（YYYY-MM-DD）', f_leg_city: 'レグ / 都市', f_label: 'ラベル', f_venue: '会場',
    f_venue_addr: '会場住所', f_doors: '開場', f_starts: '開演',
    btn_remove_perf: '✕ 公演を削除',
    f_round_name: '抽選回名', f_round_name_en: '抽選回名（英語）', f_type: '種別', f_round_leg: 'レグ',
    f_apply_open: '受付開始（JST）', f_apply_deadline: '受付締切（JST）',
    f_results: '結果発表（JST）', f_payment: '入金締切（JST）', f_apply_url: '申込URL',
    btn_remove_round: '✕ 抽選回を削除',
  },
};

let _lang = (() => {
  const saved = localStorage.getItem('lang');
  if (saved === 'en' || saved === 'ja') return saved;
  return (navigator.language || '').toLowerCase().startsWith('ja') ? 'ja' : 'en';
})();

/** Pick the active-language string, falling back to whichever is present. */
function pick(ja, en) { return _lang === 'en' ? (en || ja || '') : (ja || en || ''); }
function t(key) { return (I18N[_lang] && I18N[_lang][key]) || I18N.en[key] || key; }

function applyLang(lang) {
  _lang = lang;
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
  });
  document.querySelectorAll('.i18n-field').forEach((el) => {
    el.textContent = _lang === 'en' ? (el.dataset.en || el.dataset.ja || '')
      : (el.dataset.ja || el.dataset.en || '');
  });
  // active-language link button (bold), the-sorter style
  document.querySelectorAll('.lang-btn').forEach((b) => {
    const on = b.dataset.lang === lang;
    if (on) b.setAttribute('data-active', 'true');
    else b.removeAttribute('data-active');
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function setLang(lang) {
  localStorage.setItem('lang', lang);
  applyLang(lang);
  window.dispatchEvent(new Event('langchange')); // re-render JS-built views
}

(function initLang() {
  applyLang(_lang);
  document.querySelectorAll('.lang-btn').forEach((b) => {
    b.addEventListener('click', () => setLang(b.dataset.lang));
  });
})();

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

/* --- shared event filters (search / kind / series / franchise / has-open-round),
   applied across EVERY section: Open now, Upcoming, Past. Each event <li> carries
   data-haystack / data-kind / data-series / data-franchise / data-deadlines. --- */
function readFilters() {
  const frFilters = [...document.querySelectorAll('.fr-filter')];
  return {
    term: (document.getElementById('q')?.value || '').trim().toLowerCase(),
    kind: document.getElementById('kind')?.value || '',
    series: document.getElementById('series-filter')?.value || '',
    openOnly: !!document.getElementById('open-only')?.checked,
    frOn: new Set(frFilters.filter((c) => c.checked).map((c) => c.dataset.fr)),
    frCount: frFilters.length,
  };
}
function anyFilterActive(f) {
  return !!(f.term || f.kind || f.series || f.openOnly || (f.frCount && f.frOn.size < f.frCount));
}
function passesFilters(li, f, now) {
  if (f.term && !(li.dataset.haystack || '').includes(f.term)) return false;
  if (f.kind && li.dataset.kind !== f.kind) return false;
  if (f.series && !(li.dataset.series || '').split('|').includes(f.series)) return false;
  if (f.frCount && !f.frOn.has(li.dataset.franchise)) return false;
  if (f.openOnly
    && !(li.dataset.deadlines || '').split(',').some((dl) => dl && new Date(dl) > now)) return false;
  return true;
}
/** Wire all filter controls to a single 'filterchange' event the sections listen to. */
function initFilters() {
  const fire = () => window.dispatchEvent(new Event('filterchange'));
  document.getElementById('q')?.addEventListener('input', fire);
  ['kind', 'series-filter', 'open-only'].forEach((id) =>
    document.getElementById(id)?.addEventListener('change', fire));
  document.querySelectorAll('.fr-filter').forEach((c) => c.addEventListener('change', fire));
}

/* --- upcoming: one collapsible row per event, summarised by its next deadline --- */
function initGroups() {
  const empty = document.getElementById('feed-empty');
  const container = document.getElementById('groups');
  const pastHead = document.getElementById('past-head');
  const pastContainer = document.getElementById('past-groups');
  const groups = [...container.querySelectorAll('.evgroup')];

  // The event-name link lives inside <summary>; a plain click should navigate,
  // not toggle the row. preventDefault cancels BOTH the native nav and the
  // <summary> toggle (one event), so navigate manually. Modified clicks
  // (cmd/ctrl/shift → new tab/window) fall through to native behaviour.
  // Bound on document so it also covers the Past and "Open now" sections.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('.evname');
    if (!a) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    window.location.href = a.href;
  });

  const refresh = () => {
    const now = new Date();
    const f = readFilters();
    const filtering = anyFilterActive(f);
    let visible = 0;
    const rows = groups.map((d) => {
      // Per performance+round, keep only the next upcoming date (so a round isn't
      // repeated once per Opens/Deadline/Results/Payment, but a leg-wide round
      // still shows under each of its shows).
      const byKey = {};
      for (const o of d.querySelectorAll('.occ')) {
        const k = (o.dataset.perf || '') + '|' + o.dataset.round;
        (byKey[k] ||= []).push(o);
      }
      let next = null;
      const chosenByKey = {};
      for (const key in byKey) {
        const future = byKey[key]
          .filter((o) => new Date(o.dataset.iso) > now)
          .sort((a, b) => a.dataset.iso.localeCompare(b.dataset.iso));
        const chosen = future[0] || null;
        chosenByKey[key] = chosen;
        if (chosen && (!next || chosen.dataset.iso < next)) next = chosen.dataset.iso;
      }
      // A fully-past event (no upcoming deadline) reveals all its past occurrences
      // when expanded; an upcoming event collapses each round to its next date.
      const revealAll = !next;
      for (const key in byKey) {
        byKey[key].forEach((o) => { o.hidden = revealAll ? false : o !== chosenByKey[key]; });
      }
      d.querySelectorAll('.perf-block').forEach((pb) => {
        const occ = [...pb.querySelectorAll('.occ')];
        if (!occ.length) return;
        pb.hidden = revealAll ? false : !occ.some((o) => !o.hidden);
      });
      return { d, next };
    });

    for (const { d, next } of rows) {
      const li = d.closest('li');
      // Past events are always shown (in their own section below), so visibility
      // is just whether the row matches the active filters.
      li.hidden = !passesFilters(li, f, now);
      if (li.hidden) continue;
      visible++;
      const badge = d.querySelector('summary .next-badge');
      const round = d.querySelector('summary .next-round');
      const cd = d.querySelector('summary .countdown');
      const when = d.querySelector('summary .next-when');
      const occEl = next && [...d.querySelectorAll('.occ')].find((o) => o.dataset.iso === next);
      if (occEl) {
        const src = occEl.querySelector('.badge');
        // copy "badge <css>" for colour, but NOT i18n-field — next-badge has no
        // data-ja/en of its own and applyLang would blank it.
        badge.className = ('next-badge ' + src.className).replace(/\s*\bi18n-field\b/g, '');
        badge.textContent = src.textContent;
        if (round) round.textContent = pick(occEl.dataset.round, occEl.dataset.roundEn); // which round
        cd.dataset.iso = next;
        when.setAttribute('datetime', next);
      } else {
        badge.className = 'next-badge'; badge.textContent = t('past_badge');
        if (round) round.textContent = '';
        delete cd.dataset.iso; cd.textContent = '';
        when.removeAttribute('datetime'); when.textContent = '';
      }
    }

    // Split into Upcoming (has a next deadline, soonest first) and a separate
    // Past section (no upcoming deadline). Hidden rows stay put.
    const shown = rows.filter((r) => !r.d.closest('li').hidden);
    shown
      .filter((r) => r.next)
      .sort((a, b) => a.next.localeCompare(b.next))
      .forEach((r) => container.appendChild(r.d.closest('li')));
    const past = shown.filter((r) => !r.next);
    past.forEach((r) => pastContainer.appendChild(r.d.closest('li')));
    if (pastHead) pastHead.hidden = past.length === 0;

    paintCountdowns();
    applyTz(document.getElementById('tz-local')?.checked);
    if (empty) {
      empty.textContent = filtering ? t('cat_empty') : t('feed_empty');
      empty.hidden = visible > 0;
    }
  };

  window.addEventListener('filterchange', refresh); // shared across all sections
  window.addEventListener('langchange', refresh); // re-pick next-round label + empty text
  // On back-navigation the browser restores the filter inputs' values but fires
  // no input/change event, so re-apply filters once form state is restored.
  window.addEventListener('pageshow', refresh);
  refresh();
  setInterval(paintCountdowns, 60000);
}

/* --- "Open now": the same expandable event cards as the main list, auto-
   expanded, filtered to events with a currently-open application window and
   showing only those open rounds' deadline rows. --- */
function initActiveLotteries() {
  const section = document.getElementById('active-lotteries');
  const container = document.getElementById('active-groups');
  if (!section || !container) return;
  const cards = [...container.querySelectorAll(':scope > li')];
  const earliest = (li) => [...li.querySelectorAll('.occ:not([hidden])')]
    .map((o) => o.dataset.iso).sort()[0] || '9999';
  const refresh = () => {
    const now = new Date();
    const f = readFilters();
    let shown = 0;
    cards.forEach((li) => {
      let active = 0;
      // the shared filters also constrain "Open now"
      if (passesFilters(li, f, now)) {
        li.querySelectorAll('.occ').forEach((occ) => {
          const ro = occ.dataset.ropen;
          const rd = occ.dataset.rdeadline;
          // show only the deadline row of a round whose application window is open
          const roundOpen = rd && (!ro || new Date(ro) <= now) && new Date(rd) > now;
          const visible = roundOpen && occ.dataset.css === 'deadline';
          occ.hidden = !visible;
          if (visible) active++;
        });
      }
      li.querySelectorAll('.perf-block').forEach((pb) => {
        const occ = [...pb.querySelectorAll('.occ')];
        pb.hidden = !occ.length || !occ.some((o) => !o.hidden);
      });
      li.hidden = active === 0;
      if (active) shown++;
    });
    cards // events with the soonest-closing round first
      .filter((li) => !li.hidden)
      .sort((a, b) => earliest(a).localeCompare(earliest(b)))
      .forEach((li) => container.appendChild(li));
    section.hidden = shown === 0;
    paintCountdowns();
    applyTz(document.getElementById('tz-local')?.checked);
  };
  window.addEventListener('filterchange', refresh); // shared filters constrain Open now too
  window.addEventListener('langchange', refresh);
  window.addEventListener('pageshow', refresh);
  refresh();
  setInterval(refresh, 60000);
}

/* --- per-section "Expand all" / "Collapse all" controls (always both shown).
   data-target=<ul id>, data-act=expand|collapse. Acts on visible cards only. --- */
function initCollapseToggles() {
  document.querySelectorAll('.collapse-ctl').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ul = document.getElementById(btn.dataset.target);
      if (!ul) return;
      const open = btn.dataset.act === 'expand';
      [...ul.querySelectorAll(':scope > li')]
        .filter((li) => !li.hidden)
        .forEach((li) => { const d = li.querySelector('details.evgroup'); if (d) d.open = open; });
    });
  });
}

/* --- past archive: name/series search --- */
function initPast() {
  const q = document.getElementById('q-past');
  const empty = document.getElementById('past-empty');
  const rows = [...document.querySelectorAll('#past .evrow')];
  const apply = () => {
    const term = q.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((tr) => {
      const show = !term || tr.dataset.haystack.includes(term);
      tr.hidden = !show;
      if (show) visible++;
    });
    if (empty) empty.hidden = visible > 0;
  };
  q.addEventListener('input', apply);
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
  // Fade + strike a round whose every date is in the past (fully over).
  const now = new Date();
  document.querySelectorAll('table.rounds tr').forEach((tr) => {
    const cells = [...tr.querySelectorAll('.countdown-cell')];
    if (cells.length && cells.every((el) => new Date(el.getAttribute('datetime')) < now)) {
      tr.classList.add('round-passed');
    }
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
        if (r[f]) items.push({ date: jstDay(r[f]), cls, id: ev.id,
          ja: `${ev.name} ${r.name}`, en: `${ev.name_en || ev.name} ${r.name_en || r.name}` });
      }
    }
    for (const d of ev.event_dates || []) {
      items.push({ date: d, cls: 'event', id: ev.id, ja: ev.name, en: ev.name_en || ev.name });
    }
  }
  let cur = new Date();
  cur = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const render = () => {
    const y = cur.getFullYear(), mo = cur.getMonth();
    document.getElementById('cal-label').textContent =
      cur.toLocaleString(_lang === 'ja' ? 'ja-JP' : 'en-US', { month: 'long', year: 'numeric' });
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
        const label = pick(it.ja, it.en);
        a.className = `cal-ev ${it.cls}`; a.textContent = label;
        a.title = label; a.href = `event/${it.id}.html`;
        cell.append(a);
      });
      grid.append(cell);
    }
  };
  document.getElementById('cal-prev').onclick = () => { cur.setMonth(cur.getMonth() - 1); render(); };
  document.getElementById('cal-next').onclick = () => { cur.setMonth(cur.getMonth() + 1); render(); };
  window.addEventListener('langchange', render); // re-label per language
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
    return { name: rg('r_name'), name_en: rg('r_name_en') || null,
      type: rg('r_type') || null, leg: rg('r_leg') || null,
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
      if (r.name_en) L.push(`    name_en: ${yamlScalar(r.name_en)}`);
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
  // The edit API URL is baked into the build (public, not secret); only the admin
  // secret is entered by the editor and stored once in the browser.
  const cfg = document.getElementById('gh-config');
  const EDIT_API = (cfg && cfg.dataset.editApi) || '';
  const editSecret = document.getElementById('edit-secret');
  editSecret.value = localStorage.getItem('editSecret') || '';
  editSecret.addEventListener('change', () => {
    localStorage.setItem('editSecret', editSecret.value.trim());
  });
  // Once a secret is stored, hide the whole Config block — it's a one-time setup.
  if (cfg && editSecret.value) cfg.hidden = true;

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
    applyLang(_lang); // localize the freshly-cloned labels
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
      set('r_name', data.name); set('r_name_en', data.name_en);
      set('r_type', data.type); set('r_leg', data.leg);
      set('r_apply_open', isoToJstInput(data.apply_open));
      set('r_apply_deadline', isoToJstInput(data.apply_deadline));
      set('r_results_date', isoToJstInput(data.results_date));
      set('r_payment_deadline', isoToJstInput(data.payment_deadline));
      set('r_apply_url', data.apply_url); set('r_notes', data.notes);
    }
    roundsBox.append(node);
    applyLang(_lang); // localize the freshly-cloned labels
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
    const api = EDIT_API;
    const secret = (editSecret.value || '').trim();
    if (!api || !secret) {
      errBox.hidden = false; errBox.style.color = '';
      errBox.textContent = '⚠ set the Admin secret in Config to Save directly';
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
  document.getElementById('delete').addEventListener('click', async () => {
    const slug = (form.querySelector('[name="id"]').value || '').trim();
    const api = EDIT_API;
    const secret = (editSecret.value || '').trim();
    if (!api || !secret) {
      errBox.hidden = false; errBox.style.color = ''; errBox.textContent = '⚠ set the Admin secret in Config first';
      return;
    }
    if (!window.confirm(`Delete events/${slug}.yaml? This removes the event from the site.`)) return;
    const btn = document.getElementById('delete');
    btn.disabled = true; const label = btn.textContent; btn.textContent = 'Deleting…';
    errBox.hidden = false; errBox.style.color = ''; errBox.textContent = 'Deleting…';
    try {
      const resp = await fetch(api, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Secret': secret },
        body: JSON.stringify({ slug, delete: true }),
      });
      const data = await resp.json().catch(() => ({}));
      errBox.textContent = (resp.ok && data.ok)
        ? `✓ Deleted events/${slug}.yaml — gone in ~1 min` + (data.commit ? ` · ${data.commit}` : '')
        : `⚠ Delete failed: ${data.error || resp.status}`;
    } catch (e) {
      errBox.textContent = `⚠ Delete failed: ${e}`;
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

  // edit mode: ?edit=<id> prefills from events.json
  const editId = new URLSearchParams(location.search).get('edit');
  if (editId) {
    fetch(eventsUrl).then((r) => r.json()).then(({ events }) => {
      const ev = events.find((e) => e.id === editId);
      if (!ev) return;
      const titleEl = document.getElementById('form-title');
      titleEl.removeAttribute('data-i18n'); // dynamic edit title (has the id), not a static key
      const setEditTitle = () => { titleEl.textContent = t('edit_title_prefix') + ev.id; };
      setEditTitle();
      window.addEventListener('langchange', setEditTitle); // keep the prefix localized on toggle
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
      document.getElementById('delete').hidden = false; // delete only for existing events
      (ev.performances || []).forEach(addPerf);
      (ev.rounds || []).forEach(addRound);
    });
  } else {
    addPerf();
    addRound();
  }
}
