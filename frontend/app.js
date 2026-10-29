'use strict';
// PitchSight UI. Plain JS, no build step. All user-supplied text is inserted via
// textContent (see h()), never innerHTML.

const PRE = 5, POST = 3; // reel lead-in / follow-through seconds (mirrors server clip padding)

// ---------- helpers ----------
const $ = (sel, el = document) => el.querySelector(sel);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const pretty = (s) => String(s).replace(/_/g, ' ');
const fmtTime = (t) => {
  t = Math.max(0, Math.floor(t));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !path.startsWith('/auth/')) {
    showAuth();
    throw new Error('Not signed in');
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* non-JSON error body */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

// ---------- state ----------
const freshFilters = () => ({ types: new Set(), team: '', period: '', minConf: 0, attrs: {} });
const S = {
  me: null, packs: {}, matches: [], match: null, pack: null,
  events: [], shown: [], shownIds: new Set(), summary: null,
  filters: freshFilters(), reel: null, review: null, poll: null, exp: null,
};
const video = () => $('#video');
const teamName = (team) => (team === 'home' ? S.match.home_team : team === 'away' ? S.match.away_team : 'Unassigned');
const typeDef = (id) => S.pack.event_types.find((t) => t.id === id);

async function getPack(sport) {
  if (!S.packs[sport]) S.packs[sport] = await api('/sports/' + sport);
  return S.packs[sport];
}

// ---------- sidebar ----------
async function refreshMatches() {
  S.matches = await api('/matches');
  renderSidebar();
}

function renderSidebar() {
  const list = $('#match-list');
  list.replaceChildren(
    ...S.matches.map((m) =>
      h('div', { class: 'match-item' + (S.match && S.match.id === m.id ? ' active' : ''), onclick: () => openMatch(m.id) },
        h('div', { class: 't' }, m.title),
        h('div', { class: 's' }, h('span', { class: 'dot ' + m.status }), `${m.home_team} v ${m.away_team}`)),
    ),
    ...(S.matches.length ? [] : [h('div', { class: 's', style: { color: 'var(--muted)', padding: '4px' } }, 'No matches yet')]),
  );
}

// ---------- match view ----------
function renderEmpty() {
  S.match = null;
  $('#main').replaceChildren(
    h('div', { class: 'empty' },
      h('h2', null, 'Analyse a match'),
      h('p', null, 'Download the game from Veo, then upload it here.'),
      h('button', { class: 'btn primary', onclick: openUpload }, '+ New match')),
  );
  renderSidebar();
}

async function openMatch(id) {
  clearInterval(S.poll);
  if (S.exp) clearInterval(S.exp.poll);
  S.exp = null;
  stopReel();
  S.review = null;
  try {
    S.match = await api('/matches/' + id);
    S.pack = await getPack(S.match.sport);
  } catch (e) {
    location.hash = '';
    return renderEmpty();
  }
  S.filters = freshFilters();
  location.hash = String(id);
  await loadEvents();
  renderShell();
  renderSidebar();
  if (busy(S.match)) startPolling();
}

async function loadEvents() {
  const m = S.match;
  [S.events, S.summary] = await Promise.all([api(`/matches/${m.id}/events`), api(`/matches/${m.id}/summary`)]);
  await refreshShown();
}

async function refreshShown() {
  const q = filterQuery();
  S.shown = q ? await api(`/matches/${S.match.id}/events?${q}`) : S.events;
  S.shownIds = new Set(S.shown.map((e) => e.id));
}

function filterQuery() {
  const f = S.filters, p = new URLSearchParams();
  if (f.types.size) p.set('type', [...f.types].join(','));
  if (f.team) p.set('team', f.team);
  if (f.period) p.set('period', f.period);
  if (f.minConf > 0) p.set('min_conf', f.minConf);
  for (const [k, set] of Object.entries(f.attrs)) if (set.size) p.set('attr.' + k, [...set].join(','));
  return p.toString();
}

function renderShell() {
  const m = S.match;
  $('#main').replaceChildren(
    h('div', { class: 'center' },
      h('div', { id: 'banner' }),
      h('div', { class: 'mhead', id: 'mhead' }),
      h('div', { class: 'player' },
        m.has_video
          ? h('video', { id: 'video', controls: true, preload: 'metadata', src: `/api/matches/${m.id}/video` })
          : h('div', { class: 'noval' }, 'No video uploaded')),
      h('div', { class: 'timeline', id: 'timeline', onclick: onTimelineClick }),
      h('div', { id: 'summary' }),
      h('div', { id: 'checks' })),
    h('aside', { class: 'right' },
      h('div', { class: 'filters', id: 'filters' }),
      h('div', { class: 'listbar', id: 'listbar' }),
      h('div', { id: 'exportbox' }),
      h('div', { class: 'events', id: 'events' })),
  );
  const v = video();
  if (v) {
    v.addEventListener('timeupdate', onTimeUpdate);
    v.addEventListener('pause', () => { /* reel keeps its place if the user pauses */ });
  }
  renderBanner(); renderHead(); renderTimeline(); renderSummary(); renderFilters(); renderList();
  renderChecks();
}

function renderBanner() {
  const m = S.match, box = $('#banner');
  if (!box) return;
  const parts = [];
  if (m.status === 'processing' || m.status === 'uploaded') {
    parts.push(h('div', { class: 'banner' }, `Analysing match… ${Math.round(m.progress * 100)}%`,
      h('div', { class: 'bar' }, h('div', { style: { width: `${Math.round(m.progress * 100)}%` } }))));
  }
  if (m.status === 'ready' && m.proxy_status === 'pending') {
    const pct = Math.round(m.proxy_progress * 100);
    parts.push(h('div', { class: 'banner' }, `Preparing smooth playback (${m.proxy_reason}): ${pct}%. Playback may stutter until then.`,
      h('div', { class: 'bar' }, h('div', { style: { width: `${pct}%` } }))));
  }
  if (m.status === 'failed') {
    parts.push(h('div', { class: 'banner bad' }, `Processing failed: ${m.error || 'unknown error'} `,
      m.has_video ? h('button', { class: 'btn small', onclick: reprocess }, 'Retry') : null));
  }
  if (m.detector === 'stub') {
    parts.push(h('div', { class: 'banner warn' },
      'Demo data: events are simulated and do not match what happens in the video. Real detection is not built yet.'));
  }
  box.replaceChildren(...parts);
}

function renderHead() {
  const m = S.match, sc = S.summary && S.summary.score;
  $('#mhead').replaceChildren(
    h('div', null,
      h('h1', null, m.title),
      h('div', { class: 'sub' }, [m.played_on, S.pack.name, `${S.summary.total_events} events, ${S.summary.reviewed_events} reviewed`].filter(Boolean).join(' · '))),
    sc ? h('div', { class: 'scoreline' },
      h('span', { class: 'home-c' }, sc.home.display), h('small', null, `${m.home_team} v ${m.away_team}`), h('span', { class: 'away-c' }, sc.away.display)) : '',
  );
}

function renderSummary() {
  const sum = S.summary, m = S.match;
  $('#summary').replaceChildren(
    h('div', { class: 'ftitle' }, `Counts (${m.home_team} / ${m.away_team})`),
    h('div', { class: 'cards' },
      ...S.pack.event_types.map((t) =>
        h('div', { class: 'card' },
          h('div', { class: 'n' }, h('span', { class: 'sw dot', style: { background: t.color } }), t.label),
          h('div', { class: 'v' }, h('span', { class: 'home-c' }, sum.counts[t.id].home), h('span', { class: 'away-c' }, sum.counts[t.id].away))))),
  );
}

// ---------- timeline ----------
function renderTimeline() {
  const tl = $('#timeline'), dur = S.match.duration_s;
  if (!tl) return;
  if (!dur) return tl.replaceChildren();
  const marks = S.events.map((e) =>
    h('div', {
      class: 'mk' + (S.shownIds.has(e.id) ? '' : ' dim'),
      title: `${fmtTime(e.t_start)} ${typeDef(e.type).label}`,
      style: { left: `${(e.t_start / dur) * 100}%`, width: `${Math.max(0.15, ((e.t_end - e.t_start) / dur) * 100)}%`, background: typeDef(e.type).color },
    }));
  const halves = [];
  for (let i = 1; i < S.pack.periods; i++) halves.push(h('div', { class: 'half', style: { left: `${(i / S.pack.periods) * 100}%` } }));
  tl.replaceChildren(...halves, ...marks, h('div', { class: 'head', id: 'playhead' }));
  updatePlayhead();
}

function onTimelineClick(ev) {
  const v = video(), dur = S.match.duration_s;
  if (!v || !dur) return;
  const rect = ev.currentTarget.getBoundingClientRect();
  stopReel();
  v.currentTime = ((ev.clientX - rect.left) / rect.width) * dur;
}

function updatePlayhead() {
  const v = video(), head = $('#playhead'), dur = S.match && S.match.duration_s;
  if (v && head && dur) head.style.left = `${(v.currentTime / dur) * 100}%`;
}

// ---------- filters ----------
const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));

function chip(label, on, onclick, color) {
  return h('button', { class: 'chip' + (on ? ' on' : ''), onclick },
    color ? h('span', { class: 'sw', style: { background: color } }) : null, label);
}

function selectedAttributes() {
  const out = new Map();
  for (const id of S.filters.types) {
    for (const a of typeDef(id).attributes) {
      if (!out.has(a.id)) out.set(a.id, { id: a.id, label: a.label, values: [] });
      for (const v of a.values) if (!out.get(a.id).values.includes(v)) out.get(a.id).values.push(v);
    }
  }
  return [...out.values()];
}

async function filtersChanged() {
  await refreshShown();
  renderFilters(); renderTimeline();
  if (S.review) await startReview(); else renderList();
}

function renderFilters() {
  const f = S.filters, m = S.match, root = $('#filters');
  const teamOpts = [['', 'Both'], ['home', m.home_team], ['away', m.away_team]];
  const periodOpts = [['', 'All'], ...Array.from({ length: S.pack.periods }, (_, i) => [String(i + 1), `Half ${i + 1}`])];
  const sections = [
    h('div', { class: 'ftitle' }, 'Event type'),
    h('div', { class: 'chips' }, S.pack.event_types.map((t) =>
      chip(t.label, f.types.has(t.id), () => {
        toggle(f.types, t.id);
        const valid = new Set(selectedAttributes().map((a) => a.id));
        for (const k of Object.keys(f.attrs)) if (!valid.has(k)) delete f.attrs[k];
        filtersChanged();
      }, t.color))),
    h('div', { class: 'ftitle' }, 'Team'),
    h('div', { class: 'chips' }, teamOpts.map(([v, l]) => chip(l, f.team === v, () => { f.team = v; filtersChanged(); }))),
    h('div', { class: 'ftitle' }, 'Half'),
    h('div', { class: 'chips' }, periodOpts.map(([v, l]) => chip(l, f.period === v, () => { f.period = v; filtersChanged(); }))),
  ];
  for (const a of selectedAttributes()) {
    const set = f.attrs[a.id] || (f.attrs[a.id] = new Set());
    sections.push(
      h('div', { class: 'ftitle' }, a.label),
      h('div', { class: 'chips' }, a.values.map((v) => chip(pretty(v), set.has(v), () => { toggle(set, v); filtersChanged(); }))));
  }
  sections.push(
    h('div', { class: 'ftitle' }, 'Minimum AI confidence'),
    h('div', { class: 'slider' },
      h('input', { type: 'range', min: 0, max: 100, step: 5, value: Math.round(f.minConf * 100),
        onchange: (e) => { f.minConf = Number(e.target.value) / 100; filtersChanged(); },
        oninput: (e) => { e.target.nextSibling.textContent = `${e.target.value}%`; } }),
      h('span', null, `${Math.round(f.minConf * 100)}%`)),
    h('div', { style: { marginTop: '10px' } },
      h('button', { class: 'btn small', onclick: () => { S.filters = freshFilters(); filtersChanged(); } }, 'Reset filters')));
  root.replaceChildren(...sections);
}

// ---------- event list ----------
function attrSummary(e) {
  return Object.values(e.attributes || {}).map(pretty).join(' · ');
}

function renderList() {
  const bar = $('#listbar'), list = $('#events');
  if (S.review) return renderReview(bar, list);
  bar.replaceChildren(
    h('div', null, `${S.shown.length} of ${S.events.length} events`),
    h('div', { style: { display: 'flex', gap: '6px' } },
      h('button', { class: 'btn small', title: 'Step through unreviewed events, least confident first', disabled: !S.shown.some((e) => !e.reviewed), onclick: startReview },
        `Review (${S.shown.filter((e) => !e.reviewed).length})`),
      h('button', { class: 'btn small', disabled: !S.shown.length || !S.match.has_video, onclick: () => (S.reel ? stopReel() : startReel()) },
        S.reel ? '■ Stop reel' : `▶ Play reel (${S.shown.length})`),
      h('button', { class: 'btn small', title: 'Download the filtered events as one mp4', disabled: !S.shown.length || !S.match.has_video || (S.exp && !S.exp.done), onclick: exportReel }, '⬇ Export'),
      h('button', { class: 'btn small', disabled: !S.match.has_video, onclick: () => openEditor(null) }, '+ Tag')),
  );
  if (!S.shown.length) {
    return list.replaceChildren(h('div', { class: 'empty-list' }, S.events.length ? 'No events match these filters.' : 'No events yet.'));
  }
  list.replaceChildren(...S.shown.map((e) => {
    const t = typeDef(e.type);
    const playing = S.reel && S.reel.list[S.reel.i] && S.reel.list[S.reel.i].id === e.id;
    return h('div', { class: 'ev' + (playing ? ' playing' : ''), id: 'ev-' + e.id, onclick: () => playEvent(e) },
      h('div', { class: 'time' }, fmtTime(e.t_start)),
      h('div', null,
        h('div', { class: 'line1' },
          h('span', { class: 'badge', style: { background: t.color } }, t.label),
          h('span', { class: e.team === 'home' ? 'home-c' : e.team === 'away' ? 'away-c' : '' }, teamName(e.team)),
          e.reviewed ? h('span', { class: 'tag ok' }, '✓ reviewed') : null,
          e.origin === 'stub' && !e.reviewed ? h('span', { class: 'tag demo' }, 'demo') : null,
          e.confidence != null && !e.reviewed ? h('span', { class: 'tag' }, `${Math.round(e.confidence * 100)}%`) : null),
        h('div', { class: 'attrs' }, attrSummary(e) || 'unclassified')),
      h('div', { class: 'acts' },
        h('button', { class: 'btn small', title: 'Edit', onclick: (ev) => { ev.stopPropagation(); openEditor(e); } }, '✎'),
        h('a', { class: 'btn small', title: 'Download clip', href: `/api/events/${e.id}/clip`, download: '', onclick: (ev) => ev.stopPropagation() }, '⬇')));
  }));
}

// ---------- playback + reel ----------
function playEvent(e) {
  const v = video();
  if (!v) return;
  stopReel();
  v.currentTime = Math.max(0, e.t_start - PRE);
  v.play().catch(() => {});
}

function startReel() {
  const v = video();
  if (!v || !S.shown.length) return;
  S.review = null;
  S.reel = { list: [...S.shown], i: 0 };
  playReelItem();
}

function playReelItem() {
  const r = S.reel, v = video();
  if (!r || !v) return;
  if (r.i >= r.list.length) return stopReel();
  v.currentTime = Math.max(0, r.list[r.i].t_start - PRE);
  v.play().catch(() => {});
  renderList();
  const row = $('#ev-' + r.list[r.i].id);
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function stopReel() {
  if (!S.reel) return;
  S.reel = null;
  const v = video();
  if (v) v.pause();
  if ($('#events')) renderList();
}

function onTimeUpdate() {
  updatePlayhead();
  const r = S.reel, v = video();
  if (S.review && v.currentTime >= S.review.end) {
    v.pause();
    S.review.end = Infinity; // pause once at the end of the event; the coach can play on
  }
  if (r && v.currentTime >= r.list[r.i].t_end + POST) {
    r.i += 1;
    playReelItem();
  }
}

// ---------- reel export (server joins the filtered clips into one mp4) ----------
async function exportReel() {
  const q = filterQuery();
  let reel;
  try {
    reel = await api(`/matches/${S.match.id}/reels${q ? '?' + q : ''}`, { method: 'POST' });
  } catch (e) { return alert(e.message); }
  if (S.exp) clearInterval(S.exp.poll);
  S.exp = { reel, done: false, poll: null };
  renderExport(); renderList();
  const exp = S.exp;
  exp.poll = setInterval(async () => {
    try { exp.reel = await api('/reels/' + reel.id); } catch (_) { return; }
    if (['ready', 'failed'].includes(exp.reel.status)) { exp.done = true; clearInterval(exp.poll); }
    if (S.exp === exp) { renderExport(); if (exp.done) renderList(); }
  }, 1500);
}

function renderExport() {
  const box = $('#exportbox');
  if (!box) return;
  if (!S.exp) return box.replaceChildren();
  const r = S.exp.reel, pct = Math.round(r.progress * 100);
  const close = h('button', { class: 'btn small', title: 'Dismiss', onclick: () => { clearInterval(S.exp.poll); S.exp = null; renderExport(); renderList(); } }, '×');
  let body;
  if (r.status === 'ready') {
    body = [h('span', null, `Reel ready: ${r.event_count} events, ${fmtTime(r.duration_s)}`),
      h('a', { class: 'btn small primary', href: `/api/reels/${r.id}/download`, download: '' }, '⬇ Download')];
  } else if (r.status === 'failed') {
    body = [h('span', { class: 'bad' }, `Export failed: ${r.error || 'unknown error'}`)];
  } else {
    body = [h('span', null, `Building reel of ${r.event_count} events… ${pct}%`)];
  }
  box.replaceChildren(h('div', { class: 'exportbox ' + r.status },
    h('div', { class: 'exportrow' }, ...body, close),
    r.status === 'ready' || r.status === 'failed' ? '' : h('div', { class: 'bar' }, h('div', { style: { width: `${pct}%` } }))));
}

// ---------- checked stretches + accuracy ----------
// A coach marks a stretch "fully checked" once every detection in it is reviewed and every miss
// tagged. Inside those stretches the reviewed events are ground truth for scoring the detector.
async function renderChecks() {
  const box = $('#checks'), m = S.match;
  if (!box || m.status !== 'ready') return box && box.replaceChildren();
  let spans, ev = null;
  try {
    spans = await api(`/matches/${m.id}/verified-spans`);
    if (spans.length) ev = await api(`/matches/${m.id}/evaluation`);
  } catch (_) { return; }
  const v = video(), now = v ? v.currentTime : 0;
  const from = Math.max(0, ...spans.filter((sp) => sp.t_end <= now + 0.01).map((sp) => sp.t_end));
  const pct = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`);
  const mark = async () => {
    const t = video() ? video().currentTime : 0;
    try {
      await api(`/matches/${m.id}/verified-spans`, { method: 'POST', body: { t_start: from, t_end: t } });
      renderChecks();
    } catch (e) { alert(e.message); }
  };
  box.replaceChildren(
    h('div', { class: 'ftitle' }, 'Checked stretches'),
    h('p', { class: 'muted small' }, 'Watch a stretch, review every event in it and tag anything missed, then mark it checked. Checked stretches are used to measure the detector.'),
    h('div', { class: 'spans' },
      ...spans.map((sp) => h('span', { class: 'tag span' }, `${fmtTime(sp.t_start)}–${fmtTime(sp.t_end)}`,
        h('button', { class: 'x', title: 'Remove', onclick: async () => { await api(`/matches/${m.id}/verified-spans/${sp.id}`, { method: 'DELETE' }); renderChecks(); } }, '×'))),
      h('button', { class: 'btn small', id: 'mark-checked', onclick: mark }, `Mark ${fmtTime(from)}–playhead as checked`)),
    ev ? h('div', { class: 'accuracy' },
      ev.demo ? h('div', { class: 'banner warn' }, 'Demo data: these detections are simulated, so the scores below mean nothing.') : '',
      h('table', null,
        h('tr', null, ...['Event', 'Found', 'Missed', 'False', 'Precision', 'Recall', 'Team ok'].map((c) => h('th', null, c))),
        ...Object.entries(ev.types).map(([t, r]) => h('tr', null,
          h('td', null, (typeDef(t) || { label: t }).label), h('td', null, r.tp), h('td', null, r.fn), h('td', null, r.fp),
          h('td', null, pct(r.precision)), h('td', null, pct(r.recall)), h('td', null, pct(r.team_agreement))))),
      h('p', { class: 'muted small' }, `${(ev.checked_seconds / 60).toFixed(1)} min checked · match within ±${ev.tolerance_s}s · run #${ev.run_id}`)) : '',
  );
}

// ---------- review queue ----------
// Unreviewed events matching the current filters, least confident first. Same filter query as
// the list, plus reviewed=false&order=confidence.
async function startReview() {
  stopReel();
  const q = filterQuery();
  const queue = await api(`/matches/${S.match.id}/events?${q ? q + '&' : ''}reviewed=false&order=confidence`);
  S.review = { queue, i: 0, end: Infinity, armDelete: null, busy: false };
  reviewShow();
}

function exitReview() {
  if (!S.review) return;
  clearTimeout(S.review.armDelete);
  S.review = null;
  const v = video();
  if (v) v.pause();
  renderList();
}

const reviewCurrent = () => S.review && S.review.queue[S.review.i];

function reviewShow() {
  const e = reviewCurrent(), v = video();
  renderList();
  if (!e || !v) return;
  S.review.end = e.t_end + POST;
  v.currentTime = Math.max(0, e.t_start - PRE);
  v.play().catch(() => {});
}

function reviewMove(delta) {
  const r = S.review;
  if (!r.queue.length) return;
  r.i = Math.min(r.queue.length - 1, Math.max(0, r.i + delta));
  clearTimeout(r.armDelete); r.armDelete = null;
  reviewShow();
}

// Runs a confirm/edit/delete on the current event; afterwards the event has left the queue.
async function reviewAct(fn) {
  const r = S.review, e = reviewCurrent();
  if (!e || r.busy) return;
  r.busy = true;
  try {
    await fn(e);
  } catch (err) {
    r.busy = false;
    return alert(err.message);
  }
  r.busy = false;
  clearTimeout(r.armDelete); r.armDelete = null;
  r.queue = r.queue.filter((x) => x.id !== e.id);
  r.i = Math.min(r.i, Math.max(0, r.queue.length - 1));
  await loadEvents();
  renderHead(); renderTimeline(); renderSummary();
  reviewShow();
}

const reviewConfirm = () => reviewAct((e) => api(`/events/${e.id}`, { method: 'PATCH', body: { reviewed: true } }));

function reviewEdit() {
  const e = reviewCurrent();
  if (!e) return;
  // Saving or deleting in the editor both take the event out of the queue.
  openEditor(e, () => reviewAct(async () => {}));
}

function reviewDelete() {
  const r = S.review;
  if (!reviewCurrent()) return;
  if (!r.armDelete) { // deleting can't be undone: ask for a second press
    r.armDelete = setTimeout(() => { r.armDelete = null; renderList(); }, 3000);
    return renderList();
  }
  reviewAct((e) => api(`/events/${e.id}`, { method: 'DELETE' }));
}

function keyBtn(label, key, onclick, cls = '') {
  return h('button', { class: `btn ${cls}`, onclick }, label, h('kbd', null, key));
}

function renderReview(bar, list) {
  const r = S.review, e = reviewCurrent();
  bar.replaceChildren(
    h('div', null, e ? `Reviewing ${r.i + 1} of ${r.queue.length}` : 'Review'),
    h('button', { class: 'btn small', onclick: exitReview }, 'Exit review', h('kbd', null, 'Esc')));
  if (!e) {
    return list.replaceChildren(h('div', { class: 'empty-list' }, 'All caught up: nothing left to review with these filters.'));
  }
  const t = typeDef(e.type);
  list.replaceChildren(h('div', { class: 'review', id: 'review' },
    h('div', { class: 'line1' },
      h('span', { class: 'badge', style: { background: t.color } }, t.label),
      h('span', { class: e.team === 'home' ? 'home-c' : e.team === 'away' ? 'away-c' : '' }, teamName(e.team)),
      e.confidence != null ? h('span', { class: 'tag' }, `${Math.round(e.confidence * 100)}% confident`) : h('span', { class: 'tag' }, 'confidence unknown'),
      e.origin === 'stub' ? h('span', { class: 'tag demo' }, 'demo') : null),
    h('div', { class: 'rtime' }, `${fmtTime(e.t_start)}–${fmtTime(e.t_end)} · Half ${e.period}`),
    h('dl', { class: 'rattrs' }, t.attributes.flatMap((a) => [
      h('dt', null, a.label), h('dd', null, e.attributes[a.id] ? pretty(e.attributes[a.id]) : '—')])),
    h('div', { class: 'ractions' },
      keyBtn('✓ Confirm', 'Enter', reviewConfirm, 'primary'),
      keyBtn('✎ Edit', 'E', reviewEdit),
      keyBtn(r.armDelete ? 'Press D again to delete' : '✕ Delete', 'D', reviewDelete, 'danger' + (r.armDelete ? ' armed' : ''))),
    h('div', { class: 'ractions' },
      keyBtn('Prev', '←', () => reviewMove(-1)),
      h('button', { class: 'btn', onclick: reviewShow }, '↻ Replay'),
      keyBtn('Next', '→', () => reviewMove(1)))));
}

document.addEventListener('keydown', (ev) => {
  if (!S.review || document.querySelector('dialog[open]')) return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.target.closest('input, select, textarea')) return;
  const actions = {
    Enter: reviewConfirm, e: reviewEdit, E: reviewEdit, d: reviewDelete, D: reviewDelete,
    ArrowRight: () => reviewMove(1), ArrowLeft: () => reviewMove(-1), Escape: exitReview,
  };
  if (!actions[ev.key]) return;
  ev.preventDefault(); // also stops Enter "clicking" a focused button, which would act twice
  actions[ev.key]();
});

// ---------- editor (edit existing event or add manual tag) ----------
function field(label, control) {
  return h('div', { class: 'field' }, h('label', null, label), control);
}

function select(options, value, onchange) {
  return h('select', { onchange: (e) => onchange(e.target.value) },
    options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value) }, l)));
}

function openEditor(ev, after = reloadEvents) {
  const m = S.match, pack = S.pack, isNew = !ev, v = video();
  const now = v ? v.currentTime : 0;
  const cur = ev
    ? { ...ev, attributes: { ...ev.attributes } }
    : { type: pack.event_types[0].id, team: 'home', period: 1, t_start: Number(now.toFixed(1)), t_end: Number((now + 3).toFixed(1)), attributes: {} };
  const dlg = h('dialog');
  const body = h('div');
  const err = h('div', { class: 'err' });

  function draw() {
    const et = typeDef(cur.type);
    body.replaceChildren(
      field('Type', select(pack.event_types.map((t) => [t.id, t.label]), cur.type, (val) => { cur.type = val; cur.attributes = {}; draw(); })),
      h('div', { class: 'row' },
        field('Team', select([['home', m.home_team], ['away', m.away_team], ['', 'Unassigned']], cur.team || '', (val) => { cur.team = val || null; })),
        field('Half', select(Array.from({ length: pack.periods }, (_, i) => [i + 1, `Half ${i + 1}`]), cur.period, (val) => { cur.period = Number(val); }))),
      h('div', { class: 'row' },
        field('Start (s)', h('input', { type: 'number', step: '0.1', min: '0', value: cur.t_start, oninput: (e) => { cur.t_start = Number(e.target.value); } })),
        field('End (s)', h('input', { type: 'number', step: '0.1', min: '0', value: cur.t_end, oninput: (e) => { cur.t_end = Number(e.target.value); } }))),
      ...et.attributes.map((a) =>
        field(a.label, select([['', '— unclassified —'], ...a.values.map((val) => [val, pretty(val)])], cur.attributes[a.id] || '',
          (val) => { if (val) cur.attributes[a.id] = val; else delete cur.attributes[a.id]; }))));
  }

  async function save() {
    const payload = { type: cur.type, team: cur.team, period: cur.period, t_start: cur.t_start, t_end: cur.t_end, attributes: cur.attributes };
    try {
      if (isNew) await api(`/matches/${m.id}/events`, { method: 'POST', body: payload });
      else await api(`/events/${ev.id}`, { method: 'PATCH', body: payload });
      dlg.close();
      await after();
    } catch (e) { err.textContent = e.message; }
  }

  async function remove() {
    if (!confirm('Delete this event?')) return;
    try { await api(`/events/${ev.id}`, { method: 'DELETE' }); dlg.close(); await after(); } catch (e) { err.textContent = e.message; }
  }

  draw();
  dlg.append(
    h('h3', null, isNew ? 'Tag an event' : 'Edit event'), body, err,
    h('div', { class: 'actions' },
      isNew ? null : h('button', { class: 'btn danger', onclick: remove }, 'Delete'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Cancel'),
      h('button', { class: 'btn primary', onclick: save }, 'Save')));
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

async function reloadEvents() {
  await loadEvents();
  renderHead(); renderTimeline(); renderSummary(); renderFilters(); renderList(); renderChecks();
}

// ---------- processing status ----------
// Still analysing, or still building the playback proxy.
const busy = (m) => ['uploaded', 'processing'].includes(m.status) || m.proxy_status === 'pending';

function startPolling() {
  clearInterval(S.poll);
  S.poll = setInterval(async () => {
    try {
      const prev = S.match;
      S.match = await api('/matches/' + S.match.id);
      if (prev.status !== S.match.status && ['ready', 'failed'].includes(S.match.status)) {
        clearInterval(S.poll);
        await openMatch(S.match.id); // re-polls if the proxy is still being built
        await refreshMatches();
        return;
      }
      if (prev.proxy_status !== 'ready' && S.match.proxy_status === 'ready') swapToProxy();
      renderBanner();
      if (!busy(S.match)) clearInterval(S.poll);
    } catch (_) { /* transient; try again next tick */ }
  }, 2000);
}

// Same URL, but the server now serves the proxy: reload in place, keeping the playhead.
function swapToProxy() {
  const v = video();
  if (!v) return;
  const t = v.currentTime, playing = !v.paused;
  v.src = `/api/matches/${S.match.id}/video?proxy=1`;
  v.addEventListener('loadedmetadata', () => { v.currentTime = t; if (playing) v.play().catch(() => {}); }, { once: true });
}

async function reprocess() {
  try {
    await api(`/matches/${S.match.id}/reprocess`, { method: 'POST' });
    await openMatch(S.match.id);
  } catch (e) { alert(e.message); }
}

// ---------- upload ----------
function putVideo(id, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', `/api/matches/${id}/video`);
    x.setRequestHeader('X-Filename', encodeURIComponent(file.name));
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      if (x.status < 300) return resolve(JSON.parse(x.responseText));
      if (x.status === 401) { showAuth(); return reject(new Error('Not signed in')); }
      let msg = `Upload failed (${x.status})`;
      try { const j = JSON.parse(x.responseText); if (typeof j.detail === 'string') msg = j.detail; } catch (_) { /* keep default */ }
      reject(new Error(msg));
    };
    x.onerror = () => reject(new Error('Network error during upload'));
    x.send(file);
  });
}

async function openUpload() {
  const sports = await api('/sports');
  const f = { sport: sports[0].sport, home: '', away: '', title: '', date: '', file: null };
  const dlg = h('dialog');
  const err = h('div', { class: 'err' });
  const bar = h('div', { class: 'bar', style: { display: 'none' } }, h('div', { style: { width: '0%' } }));
  const go = h('button', { class: 'btn primary' }, 'Upload & analyse');

  go.addEventListener('click', async () => {
    err.textContent = '';
    if (!f.home || !f.away) return (err.textContent = 'Enter both team names.');
    if (!f.file) return (err.textContent = 'Choose the match video file.');
    go.disabled = true;
    try {
      const match = await api('/matches', {
        method: 'POST',
        body: { title: f.title || `${f.home} v ${f.away}`, sport: f.sport, home_team: f.home, away_team: f.away, played_on: f.date || null },
      });
      bar.style.display = '';
      await putVideo(match.id, f.file, (p) => { bar.firstChild.style.width = `${Math.round(p * 100)}%`; });
      dlg.close();
      await refreshMatches();
      await openMatch(match.id);
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  });

  dlg.append(
    h('h3', null, 'New match'),
    field('Sport', select(sports.map((s) => [s.sport, s.name]), f.sport, (v) => { f.sport = v; })),
    h('div', { class: 'row' },
      field('Home team', h('input', { type: 'text', oninput: (e) => { f.home = e.target.value.trim(); } })),
      field('Away team', h('input', { type: 'text', oninput: (e) => { f.away = e.target.value.trim(); } }))),
    h('div', { class: 'row' },
      field('Title (optional)', h('input', { type: 'text', oninput: (e) => { f.title = e.target.value.trim(); } })),
      field('Date (optional)', h('input', { type: 'date', oninput: (e) => { f.date = e.target.value; } }))),
    field('Match video (mp4 downloaded from Veo)', h('input', { type: 'file', accept: 'video/*,.mp4,.mov,.m4v,.mkv', onchange: (e) => { f.file = e.target.files[0] || null; } })),
    bar, err,
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => dlg.close() }, 'Cancel'), go));
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

// ---------- auth ----------
function showAuth(mode = 'login') {
  clearInterval(S.poll);
  S.me = null; S.match = null; S.matches = [];
  document.body.classList.add('signed-out');
  $('#user-box').replaceChildren();
  const f = { club: '', email: '', password: '' };
  const err = h('div', { class: 'err' });
  const isLogin = mode === 'login';
  const submit = async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    try {
      S.me = isLogin
        ? await api('/auth/login', { method: 'POST', body: { email: f.email, password: f.password } })
        : await api('/auth/register', { method: 'POST', body: { club_name: f.club, email: f.email, password: f.password } });
      await boot();
    } catch (e) { err.textContent = e.message; }
  };
  $('#main').replaceChildren(
    h('form', { class: 'auth', onsubmit: submit },
      h('h2', null, isLogin ? 'Sign in' : 'Create your club'),
      isLogin ? null : field('Club name', h('input', { type: 'text', name: 'club', required: true, oninput: (e) => { f.club = e.target.value; } })),
      field('Email', h('input', { type: 'email', name: 'email', autocomplete: 'username', required: true, oninput: (e) => { f.email = e.target.value; } })),
      field('Password' + (isLogin ? '' : ' (8+ characters)'), h('input', { type: 'password', name: 'password', autocomplete: isLogin ? 'current-password' : 'new-password', required: true, minlength: isLogin ? null : 8, oninput: (e) => { f.password = e.target.value; } })),
      err,
      h('button', { class: 'btn primary', type: 'submit' }, isLogin ? 'Sign in' : 'Create club'),
      h('p', { class: 'muted' }, isLogin ? 'New club? ' : 'Already have an account? ',
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); showAuth(isLogin ? 'register' : 'login'); } }, isLogin ? 'Create an account' : 'Sign in'))),
  );
}

function renderUserBox() {
  $('#user-box').replaceChildren(
    h('button', { class: 'btn small', title: 'Club members', onclick: openClub }, `${S.me.club.name}`),
    h('span', { class: 'muted' }, S.me.email),
    h('button', { class: 'btn small', onclick: logout }, 'Sign out'));
}

async function logout() {
  await api('/auth/logout', { method: 'POST' }).catch(() => {});
  location.hash = '';
  showAuth();
}

async function openClub() {
  const dlg = h('dialog');
  const list = h('div', { class: 'members' });
  const err = h('div', { class: 'err' });
  const f = { email: '', password: '', role: 'member' };
  async function draw() {
    const users = await api('/auth/users');
    list.replaceChildren(...users.map((u) => h('div', { class: 'member' }, h('span', null, u.email), h('span', { class: 'tag' }, u.role))));
  }
  const form = S.me.role === 'admin'
    ? h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.textContent = '';
      try {
        await api('/auth/users', { method: 'POST', body: f });
        e.target.reset(); f.email = f.password = ''; await draw();
      } catch (x) { err.textContent = x.message; }
    } },
    h('div', { class: 'ftitle' }, 'Add a coach'),
    h('div', { class: 'row' },
      field('Email', h('input', { type: 'email', required: true, oninput: (e) => { f.email = e.target.value; } })),
      field('Temporary password', h('input', { type: 'text', required: true, minlength: 8, oninput: (e) => { f.password = e.target.value; } }))),
    field('Role', select([['member', 'Member'], ['admin', 'Admin (can add users)']], f.role, (v) => { f.role = v; })),
    err,
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'submit' }, 'Add')))
    : h('p', { class: 'muted' }, 'Ask a club admin to add coaches.');
  dlg.append(h('h3', null, S.me.club.name), h('div', { class: 'ftitle' }, 'Members'), list, form,
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')));
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  await draw();
  dlg.showModal();
}

// ---------- boot ----------
async function boot() {
  try {
    S.me = await api('/auth/me');
  } catch (e) {
    if (/Not signed in/.test(e.message)) return showAuth();
    return $('#main').replaceChildren(h('div', { class: 'empty' }, h('h2', null, 'Cannot reach the API'), h('p', null, e.message)));
  }
  document.body.classList.remove('signed-out');
  renderUserBox();
  await refreshMatches();
  const id = Number(location.hash.slice(1));
  if (id) openMatch(id); else renderEmpty();
}

document.addEventListener('DOMContentLoaded', () => {
  $('#new-match').addEventListener('click', openUpload);
  boot();
});
