/* Wall calendar kiosk client.
 *
 * No build step and no framework on purpose: this runs unattended for months
 * on a Pi behind a monitor, and a plain file the browser can parse is one less
 * thing that can rot.
 *
 * Dates: the server sends ISO strings with real offsets. All *display* goes
 * through Intl with the configured timezone, and all *calendar arithmetic*
 * uses UTC-based civil dates ({y, m, d} triples). That combination stays
 * correct even when the panel's own clock is set to a different timezone.
 */

const state = {
  settings: null,
  events: [],
  photos: [],
  mode: "photos",
  view: "today",
  anchor: null, // civil date the week/month views are centred on
  feeds: [],
  loaded: { back: 0, days: 0 },
};

const el = (id) => document.getElementById(id);

// ---------------------------------------------------------------- time utils

let TZ = "UTC";
let partsFormatter = null;

function refreshFormatters() {
  partsFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone: TZ,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Wall-clock breakdown of an instant, in the configured timezone. */
function zparts(date) {
  const out = {};
  for (const part of partsFormatter.formatToParts(date)) {
    if (part.type !== "literal") out[part.type] = parseInt(part.value, 10);
  }
  return { y: out.year, m: out.month, d: out.day, h: out.hour, mi: out.minute };
}

const civil = (p) => ({ y: p.y, m: p.m, d: p.d });
const civilKey = (c) => `${c.y}-${String(c.m).padStart(2, "0")}-${String(c.d).padStart(2, "0")}`;
const civilToUTC = (c) => Date.UTC(c.y, c.m - 1, c.d);

function addDays(c, n) {
  const dt = new Date(civilToUTC(c) + n * 86400000);
  return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate() };
}

const dayOfWeek = (c) => new Date(civilToUTC(c)).getUTCDay(); // 0 = Sunday
const daysBetween = (a, b) => Math.round((civilToUTC(b) - civilToUTC(a)) / 86400000);
const today = () => civil(zparts(new Date()));
const minutesOfDay = (date) => { const p = zparts(date); return p.h * 60 + p.mi; };

function startOfWeek(c) {
  const weekStartsMonday = (state.settings?.weekStartsOn || "sunday") === "monday";
  const dow = dayOfWeek(c);
  const offset = weekStartsMonday ? (dow + 6) % 7 : dow;
  return addDays(c, -offset);
}

function fmtTime(date) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TZ,
    hour: "numeric",
    minute: "2-digit",
    hourCycle: state.settings?.clock24h ? "h23" : "h12",
  }).format(date);
}

function fmtDayLong(c) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(new Date(civilToUTC(c)));
}

function fmtWeekday(c) {
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "long" })
    .format(new Date(civilToUTC(c)));
}

function fmtDateNoWeekday(c) {
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "long", day: "numeric" })
    .format(new Date(civilToUTC(c)));
}

/** "in 25 minutes" / "in about 3 hours" — plainer than a bare clock time for
 *  someone checking whether they have time to sit down. */
function humanUntil(date) {
  const minutes = Math.round((date - new Date()) / 60000);
  if (minutes < 0) return "";
  if (minutes < 1) return "right now";
  if (minutes === 1) return "in 1 minute";
  if (minutes < 60) return `in ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  if (hours === 1) return "in about an hour";
  if (hours < 12) return `in about ${hours} hours`;
  return "";
}

function fmtDayShort(c) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(new Date(civilToUTC(c)));
}

function fmtMonthYear(c) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    month: "long",
    year: "numeric",
  }).format(new Date(civilToUTC(c)));
}

function relativeDayLabel(c) {
  const delta = daysBetween(today(), c);
  if (delta === 0) return "Today";
  if (delta === 1) return "Tomorrow";
  if (delta === -1) return "Yesterday";
  return "";
}

// ------------------------------------------------------------------- events

/** Every civil day an event touches, so multi-day events show on each. */
function eventDayKeys(ev) {
  const start = civil(zparts(new Date(ev.start)));
  const endDate = new Date(ev.end);
  let end = civil(zparts(endDate));
  // An event ending exactly at midnight belongs to the previous day.
  const endParts = zparts(endDate);
  if (endParts.h === 0 && endParts.mi === 0 && daysBetween(start, end) > 0) {
    end = addDays(end, -1);
  }
  const keys = [];
  const span = Math.min(daysBetween(start, end), 90);
  for (let i = 0; i <= span; i++) keys.push(civilKey(addDays(start, i)));
  return keys;
}

function eventsByDay() {
  const map = new Map();
  for (const ev of state.events) {
    for (const key of eventDayKeys(ev)) {
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(ev);
    }
  }
  for (const list of map.values()) {
    list.sort((a, b) => (a.allDay === b.allDay ? a.start.localeCompare(b.start) : a.allDay ? -1 : 1));
  }
  return map;
}

function eventTimeLabel(ev) {
  if (ev.allDay) return "All day";
  return `${fmtTime(new Date(ev.start))} – ${fmtTime(new Date(ev.end))}`;
}

function isPast(ev) { return new Date(ev.end) < new Date(); }
function isNow(ev) { const n = new Date(); return new Date(ev.start) <= n && n < new Date(ev.end); }

function nextUpcoming() {
  const now = new Date();
  return state.events.filter((e) => new Date(e.start) > now).sort((a, b) => a.start.localeCompare(b.start))[0] || null;
}

// ---------------------------------------------------------------- networking

/* A token is only needed when the kiosk browser and the server are on different
 * machines (the Docker split). On-device the server trusts loopback, so this
 * stays empty and nothing changes. */
const TOKEN_KEY = "walldisplay-token";

function readToken() {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) {
    localStorage.setItem(TOKEN_KEY, fromUrl);
    history.replaceState(null, "", location.pathname);
    return fromUrl;
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

const TOKEN = readToken();

async function api(path, options) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["X-Wall-Token"] = TOKEN;
  const resp = await fetch(path, {
    headers,
    ...options,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  TZ = state.settings.timezone || "UTC";
  refreshFormatters();
  state.anchor = today();
  applyAppearance();
  renderLegend();
}

/** Theme, type size, and motion all come from config.yaml so the panel can be
 *  tuned for whoever is reading it without touching CSS. */
function applyAppearance() {
  const s = state.settings;
  document.documentElement.dataset.theme = s.theme === "dark" ? "dark" : "light";
  document.documentElement.style.setProperty("--scale", String(s.textScale || 1));
  document.body.classList.toggle("reduce-motion", !!s.reduceMotion);

  // With week and month hidden there is nothing to navigate, so the whole
  // control cluster goes too -- one screen, no way to get lost on it.
  const showNav = s.showWeekMonth !== false;
  el("views").hidden = !showNav;
  for (const id of ["btn-prev", "btn-next", "btn-today"]) el(id).hidden = !showNav;
  if (!showNav) state.view = "today";
}

/** Fetch a window wide enough to cover what the current view can reach. */
async function loadEvents(force = false) {
  const anchor = state.anchor || today();
  const spread = state.view === "month" ? 45 : 21;
  const needBack = Math.min(30, Math.max(1, -daysBetween(today(), addDays(anchor, -spread))));
  const needDays = Math.min(180, Math.max(state.settings?.agendaDays || 14, daysBetween(today(), addDays(anchor, spread))));

  if (!force && needBack <= state.loaded.back && needDays <= state.loaded.days) {
    return;
  }
  const back = Math.max(needBack, state.loaded.back);
  const days = Math.max(needDays, state.loaded.days);
  const data = await api(`/api/events?days=${days}&back=${back}`);
  state.events = data.events;
  state.feeds = data.feeds;
  state.loaded = { back, days };
  renderStatus();
}

async function loadPhotos() {
  const data = await api("/api/photos");
  state.photos = shuffle(data.photos);
  el("photo-empty").hidden = state.photos.length > 0;
}

function shuffle(items) {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

// ---------------------------------------------------------------- websocket

let socket = null;
let reconnectDelay = 1000;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.addEventListener("open", () => {
    reconnectDelay = 1000;
    renderStatus();
  });

  socket.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      applyMode(message.state.mode);
    } else if (message.type === "events-changed") {
      await loadEvents(true);
      render();
    } else if (message.type === "photos-changed") {
      await loadPhotos();
    }
  });

  socket.addEventListener("close", () => {
    renderStatus();
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  });

  socket.addEventListener("error", () => socket.close());
}

let lastPing = 0;
function pingPresence(source = "touch") {
  const now = Date.now();
  if (now - lastPing < 5000) return;
  lastPing = now;
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "presence", source }));
  } else {
    api("/api/presence", { method: "POST", body: JSON.stringify({ source }) }).catch(() => {});
  }
}

// ------------------------------------------------------------------- modes

function applyMode(mode) {
  const changed = mode !== state.mode;
  state.mode = mode;
  document.body.classList.toggle("mode-off", mode === "off");
  el("photo-layer").classList.toggle("is-visible", mode === "photos");
  el("calendar-layer").classList.toggle("is-visible", mode === "calendar");

  if (changed && mode === "photos") {
    // Coming to rest: forget wherever the last person navigated to.
    closeSheet();
    state.view = "today";
    state.anchor = today();
    syncViewButtons();
    render();
  }
  if (changed && mode === "calendar") {
    render();
  }
}

async function requestMode(mode) {
  try {
    const snapshot = await api("/api/state/mode", { method: "POST", body: JSON.stringify({ mode }) });
    applyMode(snapshot.mode);
  } catch (err) {
    console.warn("mode change failed", err);
  }
}

// ------------------------------------------------------------- photo frame

let photoIndex = 0;
let photoSlot = 0;

function showNextPhoto() {
  if (!state.photos.length) return;
  const images = [el("photo-a"), el("photo-b")];
  const incoming = images[photoSlot];
  const outgoing = images[1 - photoSlot];

  incoming.onload = () => {
    incoming.classList.add("is-shown");
    outgoing.classList.remove("is-shown");
    photoSlot = 1 - photoSlot;
  };
  incoming.src = state.photos[photoIndex % state.photos.length];
  photoIndex++;
  if (photoIndex % state.photos.length === 0) state.photos = shuffle(state.photos);
}

function driftOverlay() {
  const overlay = el("photo-overlay");
  const positions = [
    { left: "6vw", right: "auto", top: "auto", bottom: "10vh" },
    { left: "auto", right: "6vw", top: "auto", bottom: "10vh" },
    { left: "6vw", right: "auto", top: "8vh", bottom: "auto" },
    { left: "auto", right: "6vw", top: "8vh", bottom: "auto" },
  ];
  Object.assign(overlay.style, positions[Math.floor(Math.random() * positions.length)]);
}

function renderPhotoOverlay() {
  const now = new Date();
  el("photo-weekday").textContent = fmtWeekday(today());
  el("photo-time").textContent = fmtTime(now);
  el("photo-date").textContent = fmtDateNoWeekday(today());
  const next = nextUpcoming();
  el("photo-next").textContent = next
    ? `Next: ${next.title} — ${next.allDay ? fmtDayShort(civil(zparts(new Date(next.start)))) : fmtTime(new Date(next.start)) + " " + (daysBetween(today(), civil(zparts(new Date(next.start)))) === 0 ? "today" : fmtDayShort(civil(zparts(new Date(next.start)))))}`
    : "";
}

// ----------------------------------------------------------------- rendering

function render() {
  if (!state.settings) return;
  renderClock();
  renderRangeLabel();
  const root = el("view-root");
  root.innerHTML = "";
  if (state.view === "today") {
    root.appendChild(state.settings.simpleView ? renderSimpleView() : renderTodayView());
  }
  else if (state.view === "week") root.appendChild(renderWeekView());
  else root.appendChild(renderMonthView());
}

function renderClock() {
  const now = new Date();
  const c = today();
  el("clock").textContent = fmtTime(now);
  // "What day is it?" is the question a wall calendar most often answers, so
  // the weekday gets equal billing with the time rather than hiding in a
  // subtitle. Suppressed only if the config turns it off.
  const banner = state.settings?.showWeekdayBanner !== false;
  el("weekday").textContent = banner ? fmtWeekday(c) : "";
  el("weekday").hidden = !banner;
  el("today-date").textContent = banner ? fmtDateNoWeekday(c) : fmtDayLong(c);
}

function renderRangeLabel() {
  const anchor = state.anchor || today();
  const label = el("range-label");
  if (state.view === "today") {
    label.textContent = "";
  } else if (state.view === "week") {
    const start = startOfWeek(anchor);
    const end = addDays(start, 6);
    label.textContent = `${fmtDayShort(start)} – ${fmtDayShort(end)}`;
  } else {
    label.textContent = fmtMonthYear(anchor);
  }
}

function renderLegend() {
  const legend = el("legend");
  legend.innerHTML = "";
  for (const cal of state.settings.calendars || []) {
    const span = document.createElement("span");
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.style.background = cal.color;
    span.append(dot, document.createTextNode(cal.name));
    legend.appendChild(span);
  }
}

function renderStatus() {
  const status = el("status");
  const broken = (state.feeds || []).filter((f) => !f.ok);
  const offline = !socket || socket.readyState !== WebSocket.OPEN;
  const notes = [];
  if (offline) notes.push("reconnecting…");
  if (broken.length) notes.push(`${broken.map((f) => f.name).join(", ")} not syncing`);
  status.textContent = notes.join(" · ");
  status.classList.toggle("is-warning", notes.length > 0);
}

function eventRow(ev, { showDay = false } = {}) {
  const row = document.createElement("button");
  row.className = "event-row";
  row.style.setProperty("--row-color", ev.color);
  if (isPast(ev)) row.classList.add("is-past");
  if (isNow(ev)) row.classList.add("is-now");
  if (!ev.confirmed) row.classList.add("is-unconfirmed");

  const when = document.createElement("div");
  when.className = "event-when";
  when.textContent = ev.allDay
    ? "All day"
    : fmtTime(new Date(ev.start));

  const body = document.createElement("div");
  const title = document.createElement("div");
  title.className = "event-title";
  title.textContent = ev.title;
  body.appendChild(title);

  const bits = [ev.calendar];
  if (ev.location) bits.push(ev.location);
  if (showDay) bits.unshift(fmtDayShort(civil(zparts(new Date(ev.start)))));
  const meta = document.createElement("div");
  meta.className = "event-meta";
  meta.textContent = bits.filter(Boolean).join(" · ");
  body.appendChild(meta);

  if (!ev.confirmed) {
    const flag = document.createElement("div");
    flag.className = "event-flag";
    flag.textContent = "Needs confirming — tap to review";
    body.appendChild(flag);
  }

  row.append(when, body);
  row.addEventListener("click", () => openDetail(ev));
  return row;
}

/** Big-type view: what's happening now or next, then the rest of today and
 *  tomorrow. No scrolling, no density — the whole point is that it reads from
 *  the other side of the room without anyone having to touch it. */
function renderSimpleView() {
  const wrap = document.createElement("div");
  wrap.className = "simple";
  const byDay = eventsByDay();
  const now = new Date();

  const todays = byDay.get(civilKey(today())) || [];
  const tomorrow = addDays(today(), 1);
  const tomorrows = byDay.get(civilKey(tomorrow)) || [];

  const remaining = todays.filter((e) => new Date(e.end) > now);
  const running = remaining.find(isNow);
  // Something happening right now outranks something later today.
  const hero = running || remaining[0] || tomorrows[0] || null;
  const heroIsTomorrow = hero !== null && !remaining.includes(hero);

  wrap.appendChild(buildHero(hero, running === hero, heroIsTomorrow));

  const laterToday = remaining.filter((e) => e !== hero);
  if (laterToday.length) {
    wrap.appendChild(buildSection("Later today", laterToday, 3));
  }
  const laterTomorrow = tomorrows.filter((e) => e !== hero);
  if (laterTomorrow.length) {
    wrap.appendChild(buildSection(`Tomorrow — ${fmtWeekday(tomorrow)}`, laterTomorrow, 3));
  }
  return wrap;
}

function buildHero(ev, isRunning, isTomorrow) {
  const hero = document.createElement("button");
  hero.className = "hero";

  const label = document.createElement("div");
  label.className = "hero-label";

  if (ev === null) {
    hero.classList.add("is-empty");
    hero.disabled = true;
    label.textContent = "Today";
    const nothing = document.createElement("div");
    nothing.className = "hero-title";
    nothing.textContent = "Nothing planned today.";
    hero.append(label, nothing);
    return hero;
  }

  label.textContent = isRunning ? "Happening now" : isTomorrow ? "Tomorrow" : "Next";

  const when = document.createElement("div");
  when.className = "hero-when";
  when.textContent = ev.allDay
    ? (isTomorrow ? "All day tomorrow" : "All day")
    : (isTomorrow ? `${fmtWeekday(addDays(today(), 1))}, ${fmtTime(new Date(ev.start))}` : fmtTime(new Date(ev.start)));

  const title = document.createElement("div");
  title.className = "hero-title";
  title.textContent = ev.title;
  hero.append(label, when, title);

  if (ev.location) {
    const where = document.createElement("div");
    where.className = "hero-where";
    where.textContent = ev.location;
    hero.appendChild(where);
  }

  const countdown = isRunning ? "Started " + fmtTime(new Date(ev.start))
    : isTomorrow ? "" : humanUntil(new Date(ev.start));
  if (countdown) {
    const note = document.createElement("div");
    note.className = "hero-countdown";
    note.textContent = countdown;
    hero.appendChild(note);
  }
  if (!ev.confirmed) {
    const flag = document.createElement("div");
    flag.className = "big-flag";
    flag.textContent = "Needs confirming — tap to review";
    hero.appendChild(flag);
  }

  hero.style.setProperty("--row-color", ev.color);
  hero.addEventListener("click", () => openDetail(ev));
  return hero;
}

function buildSection(labelText, events, limit) {
  const section = document.createElement("section");
  section.className = "simple-section";
  const label = document.createElement("h2");
  label.className = "section-label";
  label.textContent = labelText;
  section.appendChild(label);

  events.slice(0, limit).forEach((ev) => section.appendChild(bigRow(ev)));
  if (events.length > limit) {
    const more = document.createElement("div");
    more.className = "simple-more";
    more.textContent = `and ${events.length - limit} more`;
    section.appendChild(more);
  }
  return section;
}

function bigRow(ev) {
  const row = document.createElement("button");
  row.className = "big-row";
  row.style.setProperty("--row-color", ev.color);
  if (isPast(ev)) row.classList.add("is-past");
  if (!ev.confirmed) row.classList.add("is-unconfirmed");

  const when = document.createElement("div");
  when.className = "big-when";
  when.textContent = ev.allDay ? "All day" : fmtTime(new Date(ev.start));

  const body = document.createElement("div");
  const title = document.createElement("div");
  title.className = "big-title";
  title.textContent = ev.title;
  body.appendChild(title);
  if (ev.location) {
    const where = document.createElement("div");
    where.className = "big-where";
    where.textContent = ev.location;
    body.appendChild(where);
  }
  if (!ev.confirmed) {
    const flag = document.createElement("div");
    flag.className = "big-flag";
    flag.textContent = "Needs confirming";
    body.appendChild(flag);
  }

  row.append(when, body);
  row.addEventListener("click", () => openDetail(ev));
  return row;
}

function renderTodayView() {
  const grid = document.createElement("div");
  grid.className = "today-grid";
  const byDay = eventsByDay();
  const todayKey = civilKey(today());

  const left = document.createElement("section");
  left.className = "panel";
  const leftTitle = document.createElement("h2");
  leftTitle.className = "panel-title";
  leftTitle.textContent = fmtDayLong(today());
  left.appendChild(leftTitle);

  const todays = byDay.get(todayKey) || [];
  if (!todays.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "Nothing scheduled today.";
    left.appendChild(note);
  } else {
    todays.forEach((ev) => left.appendChild(eventRow(ev)));
  }

  const right = document.createElement("section");
  right.className = "panel";
  const rightTitle = document.createElement("h2");
  rightTitle.className = "panel-title";
  rightTitle.textContent = "Coming up";
  right.appendChild(rightTitle);

  let shown = 0;
  const horizon = state.settings.agendaDays || 14;
  for (let i = 1; i <= horizon; i++) {
    const day = addDays(today(), i);
    const events = byDay.get(civilKey(day));
    if (!events || !events.length) continue;
    const heading = document.createElement("div");
    heading.className = "day-heading";
    const strong = document.createElement("strong");
    strong.textContent = relativeDayLabel(day) || fmtDayShort(day);
    heading.appendChild(strong);
    if (relativeDayLabel(day)) {
      const sub = document.createElement("span");
      sub.textContent = fmtDayShort(day);
      heading.appendChild(sub);
    }
    right.appendChild(heading);
    events.forEach((ev) => right.appendChild(eventRow(ev)));
    shown += events.length;
  }
  if (!shown) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = `Nothing in the next ${horizon} days.`;
    right.appendChild(note);
  }

  grid.append(left, right);
  return grid;
}

/** Greedy lane assignment so overlapping events sit side by side. */
function layoutOverlaps(events) {
  const placed = [];
  const lanesEnd = [];
  for (const ev of events) {
    const start = new Date(ev.start).getTime();
    const end = new Date(ev.end).getTime();
    let lane = lanesEnd.findIndex((laneEnd) => laneEnd <= start);
    if (lane === -1) {
      lane = lanesEnd.length;
      lanesEnd.push(end);
    } else {
      lanesEnd[lane] = end;
    }
    placed.push({ ev, lane });
  }
  return { placed, lanes: Math.max(1, lanesEnd.length) };
}

function renderWeekView() {
  const wrap = document.createElement("div");
  wrap.className = "week";
  const byDay = eventsByDay();
  const start = startOfWeek(state.anchor || today());
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const todayKey = civilKey(today());

  // Hour window: default working-ish hours, widened to fit whatever is there.
  let minHour = 7;
  let maxHour = 21;
  for (const day of days) {
    for (const ev of byDay.get(civilKey(day)) || []) {
      if (ev.allDay) continue;
      minHour = Math.min(minHour, Math.floor(minutesOfDay(new Date(ev.start)) / 60));
      maxHour = Math.max(maxHour, Math.ceil(minutesOfDay(new Date(ev.end)) / 60) || 24);
    }
  }
  minHour = Math.max(0, minHour);
  maxHour = Math.min(24, Math.max(maxHour, minHour + 6));
  const totalMinutes = (maxHour - minHour) * 60;
  const pct = (minutes) => ((minutes - minHour * 60) / totalMinutes) * 100;

  const head = document.createElement("div");
  head.className = "week-head";
  head.appendChild(document.createElement("div"));
  for (const day of days) {
    const cell = document.createElement("div");
    cell.className = "week-daylabel" + (civilKey(day) === todayKey ? " is-today" : "");
    const dow = document.createElement("div");
    dow.className = "dow";
    dow.textContent = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" })
      .format(new Date(civilToUTC(day)));
    const num = document.createElement("div");
    num.className = "dnum";
    num.textContent = String(day.d);
    cell.append(dow, num);
    head.appendChild(cell);
  }

  const allDayRow = document.createElement("div");
  allDayRow.className = "week-allday";
  allDayRow.appendChild(document.createElement("div"));
  for (const day of days) {
    const cell = document.createElement("div");
    cell.className = "allday-cell";
    for (const ev of (byDay.get(civilKey(day)) || []).filter((e) => e.allDay)) {
      const chip = document.createElement("button");
      chip.className = "allday-chip";
      chip.style.setProperty("--row-color", ev.color);
      chip.textContent = ev.title;
      chip.addEventListener("click", () => openDetail(ev));
      cell.appendChild(chip);
    }
    allDayRow.appendChild(cell);
  }

  const body = document.createElement("div");
  body.className = "week-body";

  const hourCol = document.createElement("div");
  hourCol.className = "hour-col";
  for (let hour = minHour; hour <= maxHour; hour++) {
    const tick = document.createElement("div");
    tick.className = "hour-tick";
    tick.style.top = `${pct(hour * 60)}%`;
    tick.textContent = state.settings.clock24h
      ? `${String(hour % 24).padStart(2, "0")}:00`
      : `${((hour % 24) % 12) || 12}${hour % 24 < 12 ? "a" : "p"}`;
    hourCol.appendChild(tick);
  }
  body.appendChild(hourCol);

  for (const day of days) {
    const col = document.createElement("div");
    col.className = "day-col" + (civilKey(day) === todayKey ? " is-today" : "");
    for (let hour = minHour + 1; hour < maxHour; hour++) {
      const line = document.createElement("div");
      line.className = "hour-line";
      line.style.top = `${pct(hour * 60)}%`;
      col.appendChild(line);
    }

    const timed = (byDay.get(civilKey(day)) || []).filter((e) => !e.allDay);
    const { placed, lanes } = layoutOverlaps(timed);
    for (const { ev, lane } of placed) {
      // Clamp to this column's day so multi-day events don't overflow it.
      const dayStart = daysBetween(civil(zparts(new Date(ev.start))), day) > 0 ? minHour * 60 : minutesOfDay(new Date(ev.start));
      const endsLater = daysBetween(day, civil(zparts(new Date(ev.end)))) > 0;
      const dayEnd = endsLater ? maxHour * 60 : minutesOfDay(new Date(ev.end));

      const height = pct(dayEnd) - pct(dayStart);
      const block = document.createElement("button");
      block.className = "week-event";
      block.style.setProperty("--row-color", ev.color);
      block.style.top = `${Math.max(0, pct(dayStart))}%`;
      block.style.height = `${Math.max(2.5, height)}%`;
      block.style.left = `calc(${(lane / lanes) * 100}% + 3px)`;
      block.style.width = `calc(${(1 / lanes) * 100}% - 6px)`;
      block.style.opacity = isPast(ev) ? "0.45" : "1";

      const title = document.createElement("div");
      title.className = "we-title";
      title.textContent = ev.title;
      block.appendChild(title);
      // A short block only has room for the title once it wraps, and the title
      // is the useful half. The threshold is a percentage of the visible range,
      // so it scales with how many hours are on screen.
      if (height >= 7.5) {
        const time = document.createElement("div");
        time.className = "we-time";
        time.textContent = fmtTime(new Date(ev.start));
        block.appendChild(time);
      }
      block.addEventListener("click", () => openDetail(ev));
      col.appendChild(block);
    }

    if (civilKey(day) === todayKey) {
      const nowMinutes = minutesOfDay(new Date());
      if (nowMinutes >= minHour * 60 && nowMinutes <= maxHour * 60) {
        const line = document.createElement("div");
        line.className = "now-line";
        line.style.top = `${pct(nowMinutes)}%`;
        col.appendChild(line);
      }
    }
    body.appendChild(col);
  }

  wrap.append(head, allDayRow, body);
  return wrap;
}

function renderMonthView() {
  const wrap = document.createElement("div");
  wrap.className = "month";
  const byDay = eventsByDay();
  const anchor = state.anchor || today();
  const first = { y: anchor.y, m: anchor.m, d: 1 };
  const gridStart = startOfWeek(first);
  const todayKey = civilKey(today());

  const dowRow = document.createElement("div");
  dowRow.className = "month-dow";
  for (let i = 0; i < 7; i++) {
    const cell = document.createElement("div");
    cell.textContent = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" })
      .format(new Date(civilToUTC(addDays(gridStart, i))));
    dowRow.appendChild(cell);
  }

  const grid = document.createElement("div");
  grid.className = "month-grid";
  // Six rows always, so the layout doesn't jump between months.
  for (let i = 0; i < 42; i++) {
    const day = addDays(gridStart, i);
    const key = civilKey(day);
    const cell = document.createElement("div");
    cell.className = "month-cell";
    if (day.m !== anchor.m) cell.classList.add("is-outside");
    if (key === todayKey) cell.classList.add("is-today");

    const num = document.createElement("div");
    num.className = "month-daynum";
    num.textContent = String(day.d);
    cell.appendChild(num);

    const events = byDay.get(key) || [];
    events.slice(0, 3).forEach((ev) => {
      const chip = document.createElement("button");
      chip.className = "month-chip";
      const dot = document.createElement("i");
      dot.className = "dot";
      dot.style.background = ev.color;
      const label = document.createElement("span");
      label.textContent = ev.allDay ? ev.title : `${fmtTime(new Date(ev.start))} ${ev.title}`;
      chip.append(dot, label);
      chip.addEventListener("click", (e) => { e.stopPropagation(); openDetail(ev); });
      cell.appendChild(chip);
    });
    if (events.length > 3) {
      const more = document.createElement("div");
      more.className = "month-more";
      more.textContent = `+${events.length - 3} more`;
      cell.appendChild(more);
    }

    cell.addEventListener("click", () => {
      state.anchor = day;
      state.view = "today";
      syncViewButtons();
      render();
    });
    grid.appendChild(cell);
  }

  wrap.append(dowRow, grid);
  return wrap;
}

// -------------------------------------------------------------------- sheet

function openSheet(build) {
  const sheet = el("sheet");
  sheet.innerHTML = "";
  build(sheet);
  sheet.hidden = false;
  el("scrim").hidden = false;
}

function closeSheet() {
  el("sheet").hidden = true;
  el("scrim").hidden = true;
}

function openDetail(ev) {
  openSheet((sheet) => {
    const heading = document.createElement("h2");
    heading.textContent = ev.title;
    const sub = document.createElement("p");
    sub.className = "sub";
    sub.textContent = `${fmtDayLong(civil(zparts(new Date(ev.start))))} · ${eventTimeLabel(ev)}`;
    sheet.append(heading, sub);

    const list = document.createElement("dl");
    const line = (label, value) => {
      if (!value) return;
      const row = document.createElement("div");
      row.className = "detail-line";
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      row.append(dt, dd);
      list.appendChild(row);
    };
    line("Calendar", ev.calendar);
    line("Where", ev.location);
    line("Notes", ev.notes);
    line("Source", ev.source === "feed" ? "Synced calendar" : ev.source);
    sheet.appendChild(list);

    const actions = document.createElement("div");
    actions.className = "sheet-actions";

    if (ev.editable && !ev.confirmed) {
      const confirm = document.createElement("button");
      confirm.className = "btn primary";
      confirm.textContent = "Confirm";
      confirm.addEventListener("click", async () => {
        await api(`/api/events/${ev.id}/confirm`, { method: "POST" });
        closeSheet();
      });
      actions.appendChild(confirm);
    }
    if (ev.editable) {
      const remove = document.createElement("button");
      remove.className = "btn danger";
      remove.textContent = "Delete";
      remove.addEventListener("click", async () => {
        await api(`/api/events/${ev.id}`, { method: "DELETE" });
        closeSheet();
      });
      actions.appendChild(remove);
    }
    const close = document.createElement("button");
    close.className = "btn";
    close.textContent = "Close";
    close.addEventListener("click", closeSheet);
    actions.appendChild(close);
    sheet.appendChild(actions);
  });
}

function openAddForm() {
  openSheet((sheet) => {
    const anchor = state.anchor || today();
    const heading = document.createElement("h2");
    heading.textContent = "New appointment";
    const sub = document.createElement("p");
    sub.className = "sub";
    sub.textContent = "Saved on this display. Synced calendars stay read-only.";
    sheet.append(heading, sub);

    const form = document.createElement("form");
    form.innerHTML = `
      <div class="field">
        <label for="f-title">What</label>
        <input id="f-title" name="title" required maxlength="200" placeholder="Dr. Patel — follow-up">
      </div>
      <div class="field-row">
        <div class="field">
          <label for="f-date">Date</label>
          <input id="f-date" name="date" type="date" required value="${civilKey(anchor)}">
        </div>
        <div class="field">
          <label for="f-time">Time</label>
          <input id="f-time" name="time" type="time" value="09:00">
        </div>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="f-duration">Length</label>
          <select id="f-duration" name="duration">
            <option value="30">30 minutes</option>
            <option value="60" selected>1 hour</option>
            <option value="90">1.5 hours</option>
            <option value="120">2 hours</option>
            <option value="480">All day</option>
          </select>
        </div>
        <div class="field">
          <label for="f-cal">Label</label>
          <input id="f-cal" name="calendar" maxlength="80" value="Added here">
        </div>
      </div>
      <div class="field">
        <label for="f-loc">Where (optional)</label>
        <input id="f-loc" name="location" maxlength="300">
      </div>
      <div class="form-error" id="f-error"></div>
      <div class="sheet-actions">
        <button type="button" class="btn" id="f-cancel">Cancel</button>
        <button type="submit" class="btn primary">Save</button>
      </div>
    `;
    sheet.appendChild(form);
    form.querySelector("#f-cancel").addEventListener("click", closeSheet);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const allDay = data.get("duration") === "480";
      const time = allDay ? "00:00" : (data.get("time") || "09:00");
      const startLocal = `${data.get("date")}T${time}:00`;
      const minutes = allDay ? 24 * 60 : parseInt(data.get("duration"), 10);
      const endMs = new Date(`${startLocal}Z`).getTime() + minutes * 60000;
      const endLocal = new Date(endMs).toISOString().slice(0, 19);

      try {
        // Sent without an offset on purpose: the server stamps it with the
        // configured timezone, which is what the person standing here meant.
        await api("/api/events", {
          method: "POST",
          body: JSON.stringify({
            title: data.get("title"),
            start: startLocal,
            end: endLocal,
            allDay,
            location: data.get("location") || null,
            calendar: data.get("calendar") || "Added here",
            color: "#8b93a7",
          }),
        });
        closeSheet();
      } catch (err) {
        form.querySelector("#f-error").textContent = String(err.message || err);
      }
    });
  });
}

// ------------------------------------------------------------------- wiring

function syncViewButtons() {
  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.view === state.view);
  });
}

function navigate(direction) {
  const anchor = state.anchor || today();
  if (state.view === "week") state.anchor = addDays(anchor, 7 * direction);
  else if (state.view === "month") state.anchor = shiftMonth(anchor, direction);
  else state.anchor = addDays(anchor, direction);
  loadEvents().then(render).catch(() => render());
}

function shiftMonth(c, direction) {
  const index = c.y * 12 + (c.m - 1) + direction;
  return { y: Math.floor(index / 12), m: (index % 12) + 1, d: 1 };
}

function wireEvents() {
  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.view = btn.dataset.view;
      if (state.view === "today") state.anchor = today();
      syncViewButtons();
      loadEvents().then(render).catch(() => render());
    });
  });

  el("btn-prev").addEventListener("click", () => navigate(-1));
  el("btn-next").addEventListener("click", () => navigate(1));
  el("btn-today").addEventListener("click", () => {
    state.anchor = today();
    render();
  });
  el("btn-add").addEventListener("click", openAddForm);
  el("btn-photos").addEventListener("click", () => { closeSheet(); requestMode("photos"); });
  el("scrim").addEventListener("click", closeSheet);

  // Any touch counts as presence. In photo mode it also wakes the calendar --
  // and that first tap must not also press whatever ends up under the finger.
  // The calendar becomes hit-testable the moment the mode flips, which can
  // happen between pointerdown and click, so the whole gesture gets swallowed.
  let wakeGuardUntil = 0;
  document.addEventListener(
    "pointerdown",
    (event) => {
      pingPresence("touch");
      if (state.mode === "calendar") return;
      wakeGuardUntil = Date.now() + 700;
      event.preventDefault();
      event.stopPropagation();
      requestMode("calendar");
    },
    { capture: true }
  );

  for (const type of ["pointerup", "click", "mousedown", "mouseup"]) {
    document.addEventListener(
      type,
      (event) => {
        if (Date.now() > wakeGuardUntil) return;
        event.preventDefault();
        event.stopPropagation();
      },
      { capture: true }
    );
  }
}

async function init() {
  await loadSettings();
  wireEvents();
  syncViewButtons();
  await Promise.all([loadEvents(true), loadPhotos()]);

  // Ask for the mode over plain HTTP rather than waiting for the WebSocket to
  // tell us. If the socket can't connect -- a proxy in the way, a flaky first
  // moments of boot -- neither layer would ever be shown and the wall would
  // just sit there black.
  try {
    applyMode((await api("/api/state")).mode);
  } catch (err) {
    console.warn("could not read display mode, defaulting to calendar", err);
    applyMode("calendar");
  }

  render();
  renderPhotoOverlay();
  showNextPhoto();
  connect();

  setInterval(() => {
    renderClock();
    renderPhotoOverlay();
  }, 10000);

  // Re-render so "now" markers and past-event dimming stay honest.
  setInterval(() => { if (state.mode === "calendar") render(); }, 60000);

  // Fallback path for a wedged WebSocket: keep the mode roughly right over
  // plain HTTP so presence still switches the wall, just less instantly.
  setInterval(async () => {
    if (socket && socket.readyState === WebSocket.OPEN) return;
    try {
      applyMode((await api("/api/state")).mode);
    } catch { /* offline; keep showing what we have */ }
  }, 10000);

  setInterval(showNextPhoto, (state.settings.photoIntervalSeconds || 45) * 1000);
  setInterval(driftOverlay, 120000);
  setInterval(() => loadPhotos().catch(() => {}), 15 * 60000);
  setInterval(renderStatus, 5000);
}

init().catch((err) => {
  document.body.innerHTML =
    `<div style="padding:2rem;font:16px system-ui;color:#e8ecf5">` +
    `<h1>Wall display failed to start</h1><pre>${String(err)}</pre></div>`;
});
