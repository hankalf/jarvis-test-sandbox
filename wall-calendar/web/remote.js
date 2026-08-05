/* Caregiver page: check the panel is alive, send photos, add appointments.
 *
 * Everything here goes through the same token-guarded API the panel uses. The
 * token lives in localStorage; a 401 anywhere drops straight back to the gate,
 * so a rotated token can't leave the page half-working.
 */

const TOKEN_KEY = "walldisplay-remote-token";
const el = (id) => document.getElementById(id);

let token = "";
let timezone = "UTC";

// ------------------------------------------------------------------ transport

class AuthError extends Error {}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers["X-Wall-Token"] = token;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) throw new AuthError("Access code rejected");
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return resp.status === 204 ? null : resp.json();
}

// ---------------------------------------------------------------- formatting

const fmtDateTime = (iso) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  }).format(new Date(iso));

function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = n, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function fmtUptime(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

const MODE_LABEL = { photos: "Showing photos", calendar: "Showing calendar", off: "Screen off (quiet hours)" };

// -------------------------------------------------------------------- status

async function refreshStatus() {
  const status = await api("/api/status");
  timezone = status.timezone || "UTC";

  el("device-name").textContent = status.deviceName || "Wall Calendar";
  el("device-sub").textContent =
    `${MODE_LABEL[status.display.mode] || status.display.mode} · ` +
    `${new Intl.DateTimeFormat("en-US", { timeZone: timezone, hour: "numeric", minute: "2-digit" })
      .format(new Date(status.localTime))} on the display`;

  const feedsOk = status.feeds.filter((f) => f.ok).length;
  const feedsStale = status.feeds.filter((f) => !f.ok && f.cached).length;
  const feedsBroken = status.feeds.filter((f) => !f.ok && !f.cached).length;

  let feedText = "no calendars set up";
  let feedClass = "";
  if (status.feeds.length) {
    if (feedsBroken) { feedText = `${feedsBroken} not working`; feedClass = "bad"; }
    else if (feedsStale) { feedText = `${feedsStale} showing saved copy`; feedClass = "bad"; }
    else { feedText = `${feedsOk} syncing`; feedClass = "ok"; }
  }

  renderStats([
    ["Display", MODE_LABEL[status.display.mode] || status.display.mode, ""],
    ["Someone nearby", status.display.present ? "Yes" : "No", status.display.present ? "ok" : ""],
    ["Calendars", feedText, feedClass],
    ["Photos", `${status.photoCount}`, ""],
    ["Photo storage", fmtBytes(status.photoBytes), ""],
    ["Disk free", fmtBytes(status.diskFreeBytes), ""],
    ["Running for", fmtUptime(status.uptimeSeconds), ""],
  ]);

  renderWarnings([
    ...status.warnings,
    ...status.feeds
      .filter((f) => !f.ok)
      .map((f) => `Calendar "${f.name}" isn't syncing: ${f.error}`),
  ]);
  renderUpcoming(status.upcoming);
}

function renderStats(rows) {
  const target = el("stats");
  target.innerHTML = "";
  for (const [label, value, cls] of rows) {
    const stat = document.createElement("dl");
    stat.className = "stat";
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    if (cls) dd.className = cls;
    stat.append(dt, dd);
    target.appendChild(stat);
  }
}

function renderWarnings(messages) {
  const target = el("warnings");
  target.innerHTML = "";
  for (const message of messages) {
    const div = document.createElement("div");
    div.className = "warning";
    div.textContent = message;
    target.appendChild(div);
  }
}

function renderUpcoming(events) {
  const list = el("upcoming");
  list.innerHTML = "";
  if (!events.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Nothing scheduled in the next week.";
    list.appendChild(li);
    return;
  }
  for (const ev of events) {
    const li = document.createElement("li");
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = ev.allDay
      ? new Intl.DateTimeFormat("en-US", { timeZone: timezone, weekday: "short", month: "short", day: "numeric" })
          .format(new Date(ev.start)) + " · all day"
      : fmtDateTime(ev.start);
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = ev.title;
    li.append(when, what);
    if (!ev.confirmed) {
      const flag = document.createElement("span");
      flag.className = "pending";
      flag.textContent = "unconfirmed";
      li.appendChild(flag);
    }
    list.appendChild(li);
  }
}

// -------------------------------------------------------------------- photos

async function refreshPhotos() {
  const { items } = await api("/api/photos");
  el("photo-count").textContent = items.length ? `(${items.length})` : "";
  const grid = el("photo-grid");
  grid.innerHTML = "";
  for (const photo of items) {
    const cell = document.createElement("div");
    cell.className = "thumb";
    const img = document.createElement("img");
    img.src = photo.url;
    img.alt = photo.name;
    img.loading = "lazy";
    const remove = document.createElement("button");
    remove.textContent = "×";
    remove.title = `Remove ${photo.name}`;
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove this photo from the display?`)) return;
      await guard(() => api(`/api/photos/${encodeURIComponent(photo.name)}`, { method: "DELETE" }));
      await guard(refreshPhotos);
    });
    cell.append(img, remove);
    grid.appendChild(cell);
  }
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;

  const statusEl = el("upload-status");
  statusEl.textContent = `Sending ${files.length} photo${files.length > 1 ? "s" : ""}…`;

  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);

  try {
    const result = await api("/api/photos", { method: "POST", body: form });
    const parts = [];
    if (result.added.length) {
      parts.push(`<span class="good">Added ${result.added.length}.</span>`);
    }
    for (const bad of result.rejected) {
      parts.push(`<span class="bad">Skipped ${bad.name}: ${bad.reason}</span>`);
    }
    statusEl.innerHTML = parts.join("<br>") || "Nothing to add.";
    await refreshPhotos();
  } catch (err) {
    if (err instanceof AuthError) return showGate("Access code rejected");
    statusEl.innerHTML = `<span class="bad">Upload failed: ${err.message}</span>`;
  }
}

// ------------------------------------------------------------------- add event

function openSheet() {
  const now = new Date();
  const local = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
  el("e-date").value = local;
  el("event-error").textContent = "";
  el("event-form").reset();
  el("e-date").value = local;
  el("sheet").hidden = false;
  el("scrim").hidden = false;
}

function closeSheet() {
  el("sheet").hidden = true;
  el("scrim").hidden = true;
}

async function submitEvent(event) {
  event.preventDefault();
  const data = new FormData(el("event-form"));
  const allDay = data.get("duration") === "1440";
  const time = allDay ? "00:00" : (data.get("time") || "09:00");
  const start = `${data.get("date")}T${time}:00`;
  const minutes = parseInt(data.get("duration"), 10);
  // Arithmetic in UTC so it can't be shifted by the phone's own timezone; the
  // server stamps the result with the display's zone.
  const end = new Date(new Date(`${start}Z`).getTime() + minutes * 60000)
    .toISOString().slice(0, 19);

  try {
    await api("/api/events", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        start, end, allDay,
        location: data.get("location") || null,
        calendar: data.get("calendar") || "Added remotely",
        color: "#7a3fa0",
      }),
    });
    closeSheet();
    await refreshStatus();
  } catch (err) {
    if (err instanceof AuthError) return showGate("Access code rejected");
    el("event-error").textContent = err.message;
  }
}

// --------------------------------------------------------------------- gate

function showGate(message = "") {
  el("app").hidden = true;
  el("gate").hidden = false;
  el("gate-error").textContent = message;
}

function showApp() {
  el("gate").hidden = true;
  el("app").hidden = false;
}

/** Run a call, and fall back to the gate if the token stopped working. */
async function guard(fn) {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof AuthError) {
      localStorage.removeItem(TOKEN_KEY);
      token = "";
      showGate("Access code rejected");
      return null;
    }
    console.warn(err);
    return null;
  }
}

async function connect(candidate) {
  token = candidate;
  const status = await api("/api/status"); // throws AuthError on a bad token
  localStorage.setItem(TOKEN_KEY, candidate);
  showApp();
  timezone = status.timezone || "UTC";
  await Promise.all([refreshStatus(), refreshPhotos()]);
}

// -------------------------------------------------------------------- wiring

function wire() {
  el("gate-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await connect(el("gate-token").value.trim());
    } catch (err) {
      el("gate-error").textContent =
        err instanceof AuthError ? "That code wasn't accepted." : `Couldn't reach the display: ${err.message}`;
    }
  });

  el("btn-signout").addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    token = "";
    showGate();
  });

  el("btn-show-calendar").addEventListener("click", async () => {
    await guard(() => api("/api/state/mode", { method: "POST", body: JSON.stringify({ mode: "calendar" }) }));
    await guard(refreshStatus);
  });
  el("btn-show-photos").addEventListener("click", async () => {
    await guard(() => api("/api/state/mode", { method: "POST", body: JSON.stringify({ mode: "photos" }) }));
    await guard(refreshStatus);
  });

  el("btn-add").addEventListener("click", openSheet);
  el("e-cancel").addEventListener("click", closeSheet);
  el("scrim").addEventListener("click", closeSheet);
  el("event-form").addEventListener("submit", submitEvent);

  const input = el("file-input");
  const zone = el("dropzone");
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { uploadFiles(input.files); input.value = ""; });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("is-over"); });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove("is-over"); });
  }
  zone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer?.files));

  // Keeps "is it working" honest without the user pulling to refresh.
  setInterval(() => { if (!el("app").hidden) guard(refreshStatus); }, 30000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !el("app").hidden) guard(refreshStatus);
  });
}

async function init() {
  wire();
  const url = new URLSearchParams(location.search).get("token");
  if (url) history.replaceState(null, "", location.pathname);
  const stored = url || localStorage.getItem(TOKEN_KEY) || "";
  if (!stored) return showGate();
  try {
    await connect(stored);
  } catch (err) {
    showGate(err instanceof AuthError ? "" : `Couldn't reach the display: ${err.message}`);
  }
}

init();
