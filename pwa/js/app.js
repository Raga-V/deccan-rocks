import * as store from './store.js';
import { cosine, embed, loadClip, VENDOR_FILES } from './clip.js';
import { distanceM, getFix } from './geo.js';

const NAG_TIERS = [[3, 'mild'], [7, 'sulky'], [14, 'dramatic']]; // keep in sync with server/config.py
const DEFAULTS = { threshold: 0.86, radius: 40, human_name: '' };
const NO_GPS_PENALTY = 0.04;  // without GPS we only have CLIP, so demand a closer match
const MAX_EMBEDDINGS = 8;
const DAY = 86400000;
const VENDOR_CACHE = 'vendor-v1', MEDIA_CACHE = 'media-v1';

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const uuid = () => crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });

let rocks = [];
let settings = { ...DEFAULTS };
let online = false;
let current = { view: 'rocks', arg: null };

// ------------------------------------------------------------------ rock helpers

const photoUrls = new Map();
function photoSrc(r) {
  if (r.photo instanceof Blob) {
    if (!photoUrls.has(r.id)) photoUrls.set(r.id, URL.createObjectURL(r.photo));
    return photoUrls.get(r.id);
  }
  return r.photo_url || '';
}
const rockName = (r) => (r.status === 'awake' && r.name) || 'Sleeping rock';
const daysAlone = (r) => Math.max(0, Math.floor((Date.now() - Date.parse(r.last_visit_at || r.adopted_at)) / DAY));
const loneliness = (r) => Math.min(100, Math.round((daysAlone(r) / 14) * 100));
const nextChapter = (r) => (r.chapters || []).find((c) => !(r.unlocked || []).includes(c.number));
function currentNag(r) {
  const d = daysAlone(r);
  return (r.nags || []).filter((n) => d >= n.days).pop() || null;
}

async function loadState() {
  rocks = (await store.all('rocks')).sort((a, b) => a.adopted_at.localeCompare(b.adopted_at));
  settings = { ...DEFAULTS, ...((await store.getMeta('settings')) || {}) };
  const badge = $('#inbox-badge');
  const n = rocks.filter(currentNag).length;
  badge.hidden = !n;
  badge.textContent = n;
}

async function saveSettings(patch) {
  settings = { ...settings, ...patch };
  await store.setMeta('settings', settings);
}

// ------------------------------------------------------------------ audio

const player = $('#player');
let silentUrl;
function silentWav() {
  // 0.1 s of silence, used to "unlock" audio during the tap so the rock can speak a few seconds later.
  const n = 800, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 8000, true); v.setUint32(28, 16000, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
}
function primeAudio() {
  silentUrl ??= silentWav();
  player.src = silentUrl;
  player.play().catch(() => {});
}
async function play(url) {
  if (!url) return false;
  let src = url;
  try {
    const hit = await caches.match(url);
    if (hit) src = URL.createObjectURL(await hit.blob());
  } catch {}
  player.src = src;
  try { await player.play(); return true; } catch { return false; }
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-play]');
  if (b) play(b.dataset.play);
});

// ------------------------------------------------------------------ camera

const camera = $('#camera');
function capture() {
  return new Promise((resolve) => {
    camera.value = '';
    camera.onchange = () => resolve(camera.files[0] || null);
    camera.oncancel = () => resolve(null);
    camera.click();
  });
}
async function downscale(file, maxSide) {
  const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const s = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bmp.width * s);
  canvas.height = Math.round(bmp.height * s);
  canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.85));
}

// ------------------------------------------------------------------ matching

function scoreAll(emb, fix) {
  return rocks.map((r) => {
    const similarity = Math.max(...r.embeddings.map((e) => cosine(emb, e)));
    const distance = fix && r.lat != null ? distanceM(fix, r) : null;
    // Give the benefit of the doubt for GPS error, but cap it so a 200 m fix can't unlock anything.
    const near = distance == null ? true : distance - Math.min(fix.accuracy, 30) <= settings.radius;
    return { rock: r, similarity, distance, near };
  });
}

function match(emb, fix) {
  const scored = scoreAll(emb, fix);
  const threshold = fix ? settings.threshold : settings.threshold + NO_GPS_PENALTY;
  const candidates = scored.filter((s) => s.near).sort((a, b) => b.similarity - a.similarity);
  const best = candidates[0];
  if (!best) {
    const nearest = scored.filter((s) => s.distance != null).sort((a, b) => a.distance - b.distance)[0];
    return { kind: 'nothing-near', nearest, scored, threshold, fix };
  }
  if (best.similarity >= threshold) return { kind: 'accepted', ...best, scored, threshold, fix };
  // Wrong rock. The rock that *should* be here (closest by GPS) objects.
  const claimant = fix ? [...candidates].sort((a, b) => (a.distance ?? 1e9) - (b.distance ?? 1e9))[0] : best;
  return { kind: 'rejected', ...claimant, bestSimilarity: best.similarity, scored, threshold, fix };
}

// ------------------------------------------------------------------ walks (screen-on time is the killer stat)

async function walkTick() {
  const walk = await store.getMeta('walk');
  if (!walk) return;
  const now = Date.now();
  if (document.visibilityState === 'hidden' && walk.visibleSince) {
    walk.visibleMs += now - walk.visibleSince;
    walk.visibleSince = null;
  } else if (document.visibilityState === 'visible' && !walk.visibleSince) {
    walk.visibleSince = now;
  }
  await store.setMeta('walk', walk);
}
document.addEventListener('visibilitychange', walkTick);
const walkSeconds = (w) => (w.visibleMs + (w.visibleSince ? Date.now() - w.visibleSince : 0)) / 1000;

// ------------------------------------------------------------------ sync with the laptop

async function checkOnline() {
  try {
    const res = await fetch('/api/health', { signal: AbortSignal.timeout(3000), cache: 'no-store' });
    online = res.ok ? await res.json() : false;
  } catch {
    online = false;
  }
  const pill = $('#net');
  pill.textContent = online ? 'home' : 'field';
  pill.className = `pill ${online ? 'on' : 'off'}`;
  pill.title = online ? 'Laptop reachable' : 'Offline: everything still works';
  return online;
}

const blobToDataUrl = (b) => new Promise((resolve, reject) => {
  const fr = new FileReader();
  fr.onload = () => resolve(fr.result);
  fr.onerror = () => reject(fr.error);
  fr.readAsDataURL(b);
});

async function cacheUrls(urls) {
  const cache = await caches.open(MEDIA_CACHE);
  let n = 0;
  for (const u of new Set(urls.filter(Boolean))) {
    if (await cache.match(u)) continue;
    try {
      const res = await fetch(u);
      if (res.ok) { await cache.put(u, res); n++; }
    } catch {}
  }
  return n;
}

async function sync() {
  const [allRocks, visits, samples, walks] = await Promise.all(['rocks', 'visits', 'samples', 'walks'].map(store.all));
  const unsent = (xs) => xs.filter((x) => !x.synced);
  const released = (await store.getMeta('released')) || [];
  const body = {
    human_name: settings.human_name || null,
    released,
    rocks: await Promise.all(unsent(allRocks).map(async (r) => ({
      id: r.id, photo: await blobToDataUrl(r.photo), embeddings: r.embeddings, lat: r.lat, lng: r.lng,
      gps_accuracy_m: r.gps_accuracy_m, adopted_at: r.adopted_at, spot: r.spot,
    }))),
    visits: await Promise.all(unsent(visits).map(async ({ synced, photo, ...v }) => ({
      ...v, photo: photo ? await blobToDataUrl(photo) : null,
    }))),
    samples: unsent(samples).map(({ synced, ...s }) => s),
    walks: unsent(walks).map(({ synced, ...w }) => w),
  };
  const res = await fetch('/api/sync', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`sync failed (${res.status}): ${await res.text()}`);
  const data = await res.json();

  for (const [name, xs] of [['visits', visits], ['samples', samples], ['walks', walks]]) {
    const acked = new Set(data.acked[name]);
    for (const x of xs) if (!x.synced && acked.has(x.id)) await store.put(name, { ...x, synced: true });
  }
  const stillReleased = released.filter((id) => !data.acked.released.includes(id));
  await store.setMeta('released', stillReleased);
  const local = new Map(allRocks.map((r) => [r.id, r]));
  for (const s of data.rocks) {
    if (stillReleased.includes(s.id)) continue;
    const l = local.get(s.id) || { id: s.id, unlocked: [] };
    const unlocked = new Set([...(l.unlocked || []), ...s.chapters.filter((c) => c.unlocked_at).map((c) => c.number)]);
    const last = [l.last_visit_at, s.last_visit_at].filter(Boolean).sort().pop() || null;
    const { last_visit_at, ...server } = s;
    await store.put('rocks', { ...l, ...server, unlocked: [...unlocked].sort((a, b) => a - b), last_visit_at: last, synced: true });
  }
  await loadState();
  const cached = await cacheUrls(data.rocks.flatMap((r) => [
    r.photo_url, r.rejection?.audio_url, ...r.chapters.map((c) => c.audio_url), ...r.nags.map((n) => n.audio_url),
  ]));
  return { sent: body.rocks.length + body.visits.length, rocks: data.rocks.length, cached, waking: data.waking };
}

// ------------------------------------------------------------------ auto-sync: rocks talk as soon as the laptop is reachable

const pause = (ms) => new Promise((r) => setTimeout(r, ms));
let syncing = null;
const syncOnce = () => (syncing ??= sync().finally(() => (syncing = null)));  // never two syncs at once
const fingerprint = () => rocks.map((r) => `${r.id}:${r.status}:${r.chapters?.length}:${r.unlocked?.length}`).join();
let watchingWake = false;

function banner(text) {
  const b = $('#banner');
  b.hidden = !text;
  b.textContent = text || '';
}

function toast(text, onTap) {
  const t = $('#toast');
  t.textContent = text;
  t.hidden = false;
  t.onclick = () => { t.hidden = true; onTap?.(); };
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 12000);
}

function afterSync(asleep, before) {
  const woke = rocks.filter((r) => asleep.has(r.id) && r.status === 'awake');
  if (woke.length === 1) toast(`🪨 ${woke[0].name} woke up. Go visit to hear chapter 1.`, () => show('rock', woke[0].id));
  else if (woke.length) toast(`🪨 ${woke.map((r) => r.name).join(', ')} woke up. Go visit them.`, () => show('rocks'));
  // Re-render read-only views if something changed; never interrupt adopting, visiting or calibrating.
  if (fingerprint() !== before && ['rocks', 'rock', 'inbox'].includes(current.view)) show(current.view, current.arg);
}

async function watchWake(asleep, before) {
  if (watchingWake) return;
  watchingWake = true;
  try {
    for (;;) {
      const n = rocks.filter((r) => r.status !== 'awake').length;
      banner(n ? `🌙 ${n} rock${n > 1 ? 's are' : ' is'} waking up on your laptop (about 2 min each)…`
        : '🌙 Your rocks are thinking up new chapters…');
      await pause(4000);
      const s = await fetch('/api/wake', { cache: 'no-store' }).then((r) => r.json()).catch(() => null);
      if (!s?.running) break;  // done, or we walked out of range (the laptop carries on without us)
    }
    await syncOnce().catch(() => {});
  } finally {
    watchingWake = false;
    banner(null);
  }
  afterSync(asleep, before);
}

async function autoSync() {
  if (document.visibilityState !== 'visible' || !(await checkOnline())) return;
  await loadState();  // may run right after an adoption, before any view reloaded the list
  const asleep = new Set(rocks.filter((r) => r.status !== 'awake').map((r) => r.id));
  const before = fingerprint();
  const res = await syncOnce().catch(() => null);
  if (!res) return;
  if (res.waking || online.waking) watchWake(asleep, before);
  else afterSync(asleep, before);
}

async function offlineReady() {
  const cache = await caches.open(VENDOR_CACHE);
  const hits = await Promise.all(VENDOR_FILES.map((f) => cache.match(f)));
  return hits.every(Boolean);
}

async function prepareOffline(status) {
  status('Downloading the vision model from your laptop (~100 MB, once)...');
  const cache = await caches.open(VENDOR_CACHE);
  for (const f of VENDOR_FILES) if (!(await cache.match(f))) await cache.add(f);
  status('Warming up CLIP...');
  await loadClip();
  try { await navigator.storage?.persist?.(); } catch {}
  status('Ready for the hill. Works offline now.');
}

// ------------------------------------------------------------------ views

const views = {};
const main = $('#main');

async function show(view, arg = null) {
  current = { view, arg };
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  await loadState();
  window.scrollTo(0, 0);
  await views[view](arg);
}
document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => show(b.dataset.view)));

function rockCard(r) {
  const ch = r.chapters?.length || 0, un = r.unlocked?.length || 0;
  const lonely = loneliness(r);
  return `
    <button class="card rock" data-rock="${esc(r.id)}">
      <img src="${esc(photoSrc(r))}" alt="">
      <div>
        <h3>${esc(rockName(r))} ${r.status === 'awake' ? '' : '<span class="zzz">💤</span>'}</h3>
        <p class="muted">${esc(r.spot || fmtDate(r.adopted_at))}</p>
        <div class="meter" title="loneliness"><i style="width:${lonely}%"></i></div>
        <p class="muted small">${daysAlone(r)} days since a visit · ${un}/${ch} chapters</p>
      </div>
    </button>`;
}

views.rocks = async () => {
  $('#title').textContent = 'Your rocks';
  main.innerHTML = rocks.length
    ? `<button class="btn primary wide" id="adopt">+ Adopt a rock</button>${rocks.map(rockCard).join('')}`
    : `<div class="empty"><p class="big">🪨</p><p>No rocks yet.</p><p class="muted">Go find one. A big one. Old.</p>
       <button class="btn primary" id="adopt">Adopt a rock</button></div>`;
  $('#adopt').onclick = () => show('adopt');
  main.querySelectorAll('[data-rock]').forEach((el) => (el.onclick = () => show('rock', el.dataset.rock)));
};

views.rock = async (id) => {
  const r = rocks.find((x) => x.id === id);
  if (!r) return show('rocks');
  $('#title').textContent = rockName(r);
  const p = r.personality;
  const unlocked = new Set(r.unlocked || []);
  main.innerHTML = `
    <img class="hero" src="${esc(photoSrc(r))}" alt="">
    <section class="panel">
      <p class="muted">${esc(r.spot || '')} · adopted ${fmtDate(r.adopted_at)}${r.voice ? ` · voice ${esc(r.voice)}` : ''}</p>
      ${r.status !== 'awake' ? `<p>💤 Still asleep. It wakes up a couple of minutes after your phone can reach the laptop.</p>` : `
        <p><b>Temperament:</b> ${esc(p?.temperament)}</p>
        <p><b>Grudge:</b> ${esc(p?.grudge)}</p>
        <p><b>Secretly likes:</b> ${esc(p?.secret_like)}</p>
        <p><b>Quirks:</b> ${esc((p?.quirks || []).join('; '))}</p>
        <p><b>Will not be confused with:</b> ${esc(p?.neighbour)}</p>
        <p class="story">${esc(r.backstory)}</p>`}
    </section>
    <h2>Chapters</h2>
    ${(r.chapters || []).map((c) => unlocked.has(c.number)
      ? `<div class="panel chapter"><b>${c.number}.</b> ${esc(c.text)} ${c.audio_url ? `<button class="btn small" data-play="${esc(c.audio_url)}">▶</button>` : ''}</div>`
      : `<div class="panel chapter locked">🔒 Chapter ${c.number}: visit to unlock</div>`).join('') || '<p class="muted">None yet.</p>'}
    ${r.rejection ? `<p><button class="btn ghost" data-play="${esc(r.rejection.audio_url || '')}">▶ What it says to imposters</button></p>` : ''}
    <p><button class="btn ghost danger" id="release">Release this rock</button></p>`;
  $('#release').onclick = async () => {
    if (!confirm(`Release ${rockName(r)}? Its chapters and voice go too. It will not notice for a few million years.`)) return;
    await store.del('rocks', r.id);
    await store.setMeta('released', [...((await store.getMeta('released')) || []), r.id]);
    show('rocks');
  };
};

views.adopt = async () => {
  $('#title').textContent = 'Adopt a rock';
  let blob = null, fix = null;
  main.innerHTML = `
    <div class="panel">
      <p>Stand back so the whole rock is in frame. Remember the angle: you'll photograph it the same way on visits.</p>
      <button class="btn primary wide" id="shoot">📷 Take its photo</button>
      <img id="preview" class="hero" hidden alt="">
      <label>Where is it? <span class="muted">(optional, helps the rock's personality)</span>
        <input id="spot" placeholder="e.g. Khajaguda hill, near the big cleft"></label>
      <p id="gps" class="muted">GPS: not yet</p>
      <button class="btn primary wide" id="save" disabled>Adopt</button>
      <p id="status" class="muted"></p>
    </div>`;
  let fixP = null;
  $('#shoot').onclick = async () => {
    fixP = getFix({ timeoutMs: 20000, goodEnoughM: 8 });
    $('#gps').textContent = 'GPS: finding you...';
    fixP.then((f) => {
      fix = f;
      $('#gps').textContent = f ? `GPS: ±${Math.round(f.accuracy)} m` : 'GPS: unavailable (the rock will rely on looks alone)';
    });
    const file = await capture();
    if (!file) return;
    blob = await downscale(file, 1024);
    $('#preview').src = URL.createObjectURL(blob);
    $('#preview').hidden = false;
    $('#save').disabled = false;
  };
  $('#save').onclick = async () => {
    $('#save').disabled = true;
    const status = (t) => ($('#status').textContent = t);
    try {
      status('Looking at it properly (CLIP)...');
      const [emb] = await Promise.all([embed(blob), fixP]);
      const rock = {
        id: uuid(), photo: blob, embeddings: [emb], lat: fix?.lat ?? null, lng: fix?.lng ?? null,
        gps_accuracy_m: fix?.accuracy ?? null, adopted_at: new Date().toISOString(), spot: $('#spot').value.trim() || null,
        status: 'sleeping', unlocked: [], chapters: [], nags: [], synced: false,
      };
      await store.put('rocks', rock);
      status('Adopted. It is asleep 💤. It wakes up as soon as your phone can reach the laptop.');
      const here = current;
      setTimeout(() => current === here && show('rock', rock.id), 1200);  // unless they've moved on
      autoSync();  // so the laptop starts thinking right away
    } catch (e) {
      status(`Couldn't adopt: ${e.message}. Is the vision model downloaded? (Home tab)`);
      $('#save').disabled = false;
    }
  };
};

views.visit = async () => {
  $('#title').textContent = 'Visit';
  main.innerHTML = rocks.length
    ? `<div class="visit-hero"><button class="btn primary huge" id="go">📷<br>Visit a rock</button>
       <p class="muted">Photograph it from roughly the angle you adopted it.</p></div><div id="result"></div>`
    : `<div class="empty"><p>You have no rocks to visit. Adopt one first.</p></div>`;
  const go = $('#go');
  if (!go) return;
  go.onclick = async () => {
    primeAudio();
    const fixP = getFix();
    const file = await capture();
    if (!file) return;
    const result = $('#result');
    result.innerHTML = `<div class="panel"><p>Listening to the rock...</p></div>`;
    try {
      const blob = await downscale(file, 768);
      const [emb, fix] = await Promise.all([embed(blob), fixP]);
      await handleVisit(match(emb, fix), emb, blob, result);
      autoSync();
    } catch (e) {
      result.innerHTML = `<div class="panel bad"><p>Something broke: ${esc(e.message)}</p></div>`;
    }
  };
};

async function handleVisit(m, emb, blob, el) {
  const ts = new Date().toISOString();
  const visit = {
    id: uuid(), rock_id: m.rock?.id ?? null, ts, photo: blob, embedding: m.kind === 'accepted' ? emb : null,
    similarity: m.similarity ?? m.bestSimilarity ?? null, gps_distance_m: m.distance ?? null,
    gps_accuracy_m: m.fix?.accuracy ?? null, accepted: m.kind === 'accepted', chapter_number: null, reason: m.kind, synced: false,
  };
  const evidence = m.rock ? `<p class="muted small">looks ${(visit.similarity ?? 0).toFixed(3)} alike (needs ${m.threshold.toFixed(2)})
     · ${m.distance != null ? `${Math.round(m.distance)} m from its spot (±${Math.round(m.fix.accuracy)} m)` : 'no GPS'}</p>` : '';
  let html, audio = null;

  if (m.kind === 'accepted') {
    const r = m.rock;
    const ch = r.status === 'awake' ? nextChapter(r) : null;
    if (ch) {
      visit.chapter_number = ch.number;
      r.unlocked = [...(r.unlocked || []), ch.number];
      audio = ch.audio_url;
    }
    r.last_visit_at = ts;
    r.embeddings = [r.embeddings[0], ...[...r.embeddings.slice(1), emb].slice(-(MAX_EMBEDDINGS - 1))];
    await store.put('rocks', r);
    html = `<div class="panel good"><img class="thumb" src="${esc(photoSrc(r))}" alt="">
      <h2>It's really ${esc(rockName(r))}.</h2>
      ${ch ? `<p class="speech">${esc(ch.text)}</p>${audio ? `<button class="btn small" data-play="${esc(audio)}">▶ again</button>` : ''}`
        : r.status !== 'awake' ? `<p>💤 It's still asleep. Sync at home tonight and it will have things to say.</p>`
        : `<p>It's mulling over what to say next. Sync at home tonight.</p>`}
      ${evidence}</div>`;
  } else if (m.kind === 'rejected') {
    const r = m.rock;
    audio = r.rejection?.audio_url;
    html = `<div class="panel bad"><img class="thumb" src="${esc(photoSrc(r))}" alt="">
      <h2>${esc(rockName(r))} objects.</h2>
      <p class="speech">${esc(r.rejection?.text || "That is not me. I would know.")}</p>${evidence}</div>`;
  } else {
    const n = m.nearest;
    html = `<div class="panel"><h2>None of your rocks are here.</h2>
      <p class="muted">${n ? `Nearest is ${esc(rockName(n.rock))}, ${Math.round(n.distance)} m away.` : 'No GPS fix either.'}</p></div>`;
  }
  await store.put('visits', visit);
  const walk = await store.getMeta('walk');
  if (walk) await store.setMeta('walk', { ...walk, visits: walk.visits + 1 });
  el.innerHTML = html;
  if (audio && !(await play(audio))) el.insertAdjacentHTML('beforeend', '<p class="muted small">Tap ▶ to hear it.</p>');
}

views.inbox = async () => {
  $('#title').textContent = 'Rock inbox';
  const lonely = rocks.filter(currentNag).sort((a, b) => daysAlone(b) - daysAlone(a));
  const sleeping = rocks.filter((r) => r.status !== 'awake');
  main.innerHTML = `
    ${lonely.map((r) => {
      const n = currentNag(r);
      return `<div class="panel nag"><img class="thumb" src="${esc(photoSrc(r))}" alt="">
        <h3>${esc(rockName(r))} <span class="muted small">· ${daysAlone(r)} days</span></h3>
        <p class="speech">${esc(n.text)}</p>
        ${n.audio_url ? `<button class="btn small" data-play="${esc(n.audio_url)}">▶</button>` : ''}</div>`;
    }).join('')}
    ${sleeping.length ? `<p class="muted">💤 ${sleeping.length} rock${sleeping.length > 1 ? 's are' : ' is'} asleep until your phone reaches the laptop.</p>` : ''}
    ${!lonely.length && rocks.length ? '<div class="empty"><p>Everyone is content.</p><p class="muted">Suspicious.</p></div>' : ''}
    ${!rocks.length ? '<div class="empty"><p class="muted">No rocks, no guilt. Yet.</p></div>' : ''}`;
};

views.home = async () => {
  $('#title').textContent = 'Home base';
  await checkOnline();
  const walk = await store.getMeta('walk');
  const walks = (await store.all('walks')).sort((a, b) => b.started_at.localeCompare(a.started_at));
  const samples = await store.all('samples');
  const pending = (await store.all('visits')).filter((v) => !v.synced).length + rocks.filter((r) => !r.synced).length
    + ((await store.getMeta('released')) || []).length;
  const ready = await offlineReady();
  main.innerHTML = `
    <section class="panel">
      <h2>${online ? '🏠 Laptop reachable' : '⛰️ In the field'}</h2>
      ${online ? `<p class="muted small">Gemma (${esc(online.model)}): ${online.gemma ? '✓' : '✗ not pulled'} · voices: ${online.voices.length}${online.nightly_wake ? ` · wakes rocks nightly at ${esc(online.nightly_wake)}` : ''}</p>` : '<p class="muted small">Everything works offline. Sync when you are back on home Wi-Fi.</p>'}
      <p>${pending} thing${pending === 1 ? '' : 's'} waiting to sync.</p>
      <button class="btn primary" id="sync" ${online ? '' : 'disabled'}>⇅ Sync</button>
      <button class="btn" id="wake" ${online ? '' : 'disabled'}>🌙 Wake the rocks</button>
      <label class="inline"><input type="checkbox" id="mock"> mock (no Gemma)</label>
      <pre id="log" class="log" hidden></pre>
    </section>
    <section class="panel">
      <h2>Offline vision</h2>
      <p id="prep-status" class="muted">${ready ? 'CLIP model cached ✓' : 'Not downloaded yet: do this at home before your first walk.'}</p>
      <button class="btn" id="prep" ${online || ready ? '' : 'disabled'}>${ready ? 'Re-check' : 'Download for offline'}</button>
    </section>
    <section class="panel">
      <h2>Walk</h2>
      ${walk ? `<p>Walking since ${new Date(walk.started_at).toLocaleTimeString()}. Screen on: <b id="screen">${Math.round(walkSeconds(walk))}</b> s · ${walk.visits} visits</p>
        <button class="btn primary" id="walk-end">End walk</button>`
      : `<button class="btn" id="walk-start">🚶 Start walk</button>`}
      ${walks.map((w) => `<p class="muted small">${fmtDate(w.started_at)}: ${Math.round((Date.parse(w.ended_at) - Date.parse(w.started_at)) / 60000)} min walk, screen on ${Math.round(w.screen_on_s)} s, ${w.visits} visits</p>`).join('')}
    </section>
    <section class="panel">
      <h2>Matching</h2>
      <label>Similarity threshold <b id="thr-v">${settings.threshold.toFixed(2)}</b>
        <input type="range" id="thr" min="0.70" max="0.98" step="0.01" value="${settings.threshold}"></label>
      <label>GPS radius <b id="rad-v">${settings.radius}</b> m
        <input type="range" id="rad" min="10" max="120" step="5" value="${settings.radius}"></label>
      <button class="btn" id="calib">🎯 Calibration photo</button>
      <p class="muted small">${samples.length} calibration samples. Analyse them at home with <code>python scripts/calibration.py</code>.</p>
    </section>
    <section class="panel">
      <label>What should the rocks call you?
        <input id="human" value="${esc(settings.human_name)}" placeholder="your name"></label>
    </section>`;

  const log = $('#log');
  async function pollWake() {
    const s = await (await fetch('/api/wake', { cache: 'no-store' })).json();
    log.hidden = false;
    log.textContent = s.log.join('\n') || 'starting...';
    if (s.running) setTimeout(pollWake, 2000);
    else if (current.view === 'home') { await syncOnce().catch(() => {}); log.textContent += '\nsynced.'; }
  }
  $('#sync').onclick = async (e) => {
    e.target.disabled = true;
    try {
      const r = await syncOnce();
      e.target.textContent = `⇅ Synced: sent ${r.sent}, ${r.rocks} rocks, ${r.cached} new voice files`;
    } catch (err) { e.target.textContent = `Sync failed: ${err.message}`; }
    e.target.disabled = false;
  };
  $('#wake').onclick = async () => {
    log.hidden = false;
    try {
      await syncOnce();  // the laptop needs the new rocks before it can wake them
      await fetch(`/api/wake?mock=${$('#mock').checked}`, { method: 'POST' });
      pollWake();
    } catch (err) { log.textContent = `Couldn't start: ${err.message}`; }
  };
  $('#prep').onclick = async () => {
    try { await prepareOffline((t) => ($('#prep-status').textContent = t)); }
    catch (e) { $('#prep-status').textContent = `Failed: ${e.message}. Did you run scripts/fetch_assets.py on the laptop?`; }
  };
  $('#walk-start')?.addEventListener('click', async () => {
    await store.setMeta('walk', { id: uuid(), started_at: new Date().toISOString(), visibleMs: 0, visibleSince: Date.now(), visits: 0 });
    show('home');
  });
  $('#walk-end')?.addEventListener('click', async () => {
    const w = await store.getMeta('walk');
    await store.put('walks', { id: w.id, started_at: w.started_at, ended_at: new Date().toISOString(),
      screen_on_s: walkSeconds(w), visits: w.visits, synced: false });
    await store.setMeta('walk', null);
    show('home');
  });
  $('#thr').oninput = (e) => { $('#thr-v').textContent = (+e.target.value).toFixed(2); saveSettings({ threshold: +e.target.value }); };
  $('#rad').oninput = (e) => { $('#rad-v').textContent = e.target.value; saveSettings({ radius: +e.target.value }); };
  $('#human').onchange = (e) => saveSettings({ human_name: e.target.value.trim() });
  $('#calib').onclick = () => show('calibrate');
};

views.calibrate = async () => {
  $('#title').textContent = 'Calibrate';
  main.innerHTML = `<div class="panel"><p>Photograph a rock (one of yours, or a lookalike neighbour), then say which it really is.
    The numbers tell you where to set the threshold.</p><button class="btn primary wide" id="shoot">📷 Calibration photo</button></div><div id="out"></div>`;
  $('#shoot').onclick = async () => {
    const fixP = getFix();
    const file = await capture();
    if (!file) return;
    const out = $('#out');
    out.innerHTML = '<p class="muted">Measuring...</p>';
    const [emb, fix] = await Promise.all([downscale(file, 768).then(embed), fixP]);
    const scored = scoreAll(emb, fix).sort((a, b) => b.similarity - a.similarity);
    out.innerHTML = `<div class="panel"><table class="scores">
      ${scored.map((s) => `<tr><td>${esc(rockName(s.rock))}</td><td>${s.similarity.toFixed(3)}</td>
        <td>${s.distance != null ? `${Math.round(s.distance)} m` : '–'}</td></tr>`).join('')}</table>
      <p>Which rock is this really?</p>
      ${scored.map((s) => `<button class="btn small" data-truth="${esc(s.rock.id)}">${esc(rockName(s.rock))}</button>`).join('')}
      <button class="btn small ghost" data-truth="">None of mine</button></div>`;
    out.querySelectorAll('[data-truth]').forEach((b) => (b.onclick = async () => {
      await store.put('samples', {
        id: uuid(), ts: new Date().toISOString(), truth: b.dataset.truth || null, synced: false,
        scores: Object.fromEntries(scored.map((s) => [s.rock.id, { similarity: s.similarity, distance_m: s.distance }])),
      });
      out.innerHTML = '<p>Saved. Take another, from a different angle or of a neighbour.</p>';
    }));
  };
};

// ------------------------------------------------------------------ boot

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
setInterval(() => { if (current.view === 'home' && $('#screen')) store.getMeta('walk').then((w) => w && ($('#screen').textContent = Math.round(walkSeconds(w)))); }, 1000);
await loadState();
show('rocks');
autoSync();
setInterval(autoSync, 20000);
window.addEventListener('online', autoSync);
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && autoSync());
