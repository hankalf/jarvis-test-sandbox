/* AAL front-end. Vanilla; no build step. */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const SESSION = "default";

  const state = {
    graph: null,
    categories: [],
    config: null,
    kind: "image",
    count: 1,
    speak: false,
    pollTimer: null,
  };

  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    if (!res.ok) throw new Error((await res.text()).slice(0, 300) || res.statusText);
    return res.json();
  }

  /* ============================================================ topbar */

  async function loadStatus() {
    const s = await api("/api/status");
    $("#brand-name").textContent = s.brand;
    $("#pill-nodes").textContent = `${s.nodes} entries`;
    $("#pill-model").textContent = `${s.model} · ${s.effort}`;
    $("#pill-model").title = s.anthropic_key
      ? "Anthropic key detected"
      : "No ANTHROPIC_API_KEY in the environment — chat will fail until one is set";
    if (!s.anthropic_key) $("#pill-model").style.color = "var(--warn)";
    $("#pill-render").textContent = s.providers.fal ? "Fal AI connected" : "renders: local preview";
    return s;
  }

  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === "view-" + tab.dataset.view));
      if (tab.dataset.view === "brain" && graph) graph.resize();
      if (tab.dataset.view === "spy") renderSpy();
      if (tab.dataset.view === "studio") loadRenders();
    });
  });

  $("#brand-name").addEventListener("click", async () => {
    const name = prompt("Brand name", $("#brand-name").textContent);
    if (!name) return;
    await api("/api/brain/brand", { method: "POST", body: JSON.stringify({ name }) });
    await refreshBrain();
  });

  /* ============================================================ brain */

  let graph = null;

  async function refreshBrain() {
    state.graph = await api("/api/brain");
    state.categories = state.graph.categories;
    if (!graph) {
      graph = new window.BrainGraph($("#graph"), { onSelect: onGraphSelect });
    }
    graph.setData(state.graph);
    renderGrid();
    await loadStatus();
    renderTemplates();
  }

  function onGraphSelect(node) {
    if (!node) return hideInspector();
    if (node.kind === "hub") {
      return showInspector({
        kicker: "brand",
        title: state.graph.brand,
        body: `${state.graph.total} entries across ${state.categories.length} categories. `
            + `Everything here is in the assistant's context on every turn.`,
      });
    }
    if (node.kind === "cat") {
      const items = state.graph.nodes.filter((n) => n.category === node.slug);
      return showInspector({
        kicker: "category",
        title: `${node.label} · ${items.length}`,
        body: node.about,
        list: items,
      });
    }
    showInspector({ kicker: node.slug, title: node.data.title, node: node.data });
  }

  function showInspector(view) {
    const el = $("#inspector-body");
    let html = `<div class="kicker">${esc(view.kicker)}</div><h3>${esc(view.title)}</h3>`;
    if (view.body) html += `<div class="body">${esc(view.body)}</div>`;
    if (view.node) {
      const n = view.node;
      html += `<div class="body">${esc(n.body)}</div>`;
      if (n.tags.length) html += `<div>${n.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>`;
      const meta = Object.entries(n.meta || {});
      if (meta.length) {
        html += `<div class="meta">${meta.map(([k, v]) => `${esc(k)}: <b>${esc(String(v))}</b>`).join(" · ")}</div>`;
      }
      html += `<div class="meta">id ${esc(n.id)} · added by ${esc(n.source)}</div>`;
      html += `<div class="inspector-actions">
        <button class="btn small" data-act="edit">Edit</button>
        <button class="btn ghost small" data-act="delete">Delete</button>
        <button class="btn ghost small" data-act="ask">Ask about this</button></div>`;
    }
    if (view.list) {
      html += `<div class="meta">entries</div><ul style="list-style:none;padding:0;margin:8px 0 0;display:grid;gap:6px">`;
      html += view.list.map((n) =>
        `<li class="tag" style="display:block;cursor:pointer" data-node="${esc(n.id)}">${esc(n.title)}</li>`).join("");
      html += `</ul>`;
    }
    el.innerHTML = html;
    $("#inspector").hidden = false;

    el.querySelectorAll("[data-node]").forEach((li) => li.addEventListener("click", () => {
      const node = state.graph.nodes.find((n) => n.id === li.dataset.node);
      if (node) showInspector({ kicker: node.category, title: node.title, node });
    }));
    const act = (name, fn) => {
      const b = el.querySelector(`[data-act="${name}"]`);
      if (b) b.addEventListener("click", fn);
    };
    act("delete", async () => {
      if (!confirm(`Delete "${view.node.title}" from the brain?`)) return;
      await api(`/api/brain/node/${view.node.id}`, { method: "DELETE" });
      hideInspector();
      refreshBrain();
    });
    act("edit", () => openNodeModal(view.node));
    act("ask", () => {
      $("#chat-text").value = `About "${view.node.title}" (${view.node.id}) — `;
      $("#chat-text").focus();
    });
  }

  function hideInspector() { $("#inspector").hidden = true; }
  $("#inspector-close").addEventListener("click", hideInspector);

  function renderGrid() {
    const wrap = $("#grid-wrap");
    wrap.innerHTML = state.categories.map((cat) => {
      const items = state.graph.nodes.filter((n) => n.category === cat.slug);
      return `<div class="cat-card">
        <h4><span style="color:${cat.color}">${cat.glyph}</span> ${esc(cat.label)}
            <span class="count-chip">${items.length}</span></h4>
        <p class="about">${esc(cat.about)}</p>
        <ul>${items.map((n) => `<li data-node="${esc(n.id)}"><b>${esc(n.title)}</b>
            <span>${esc(trunc(n.body, 110))}</span></li>`).join("") || '<li style="opacity:.5">empty</li>'}</ul>
      </div>`;
    }).join("");
    wrap.querySelectorAll("[data-node]").forEach((li) => li.addEventListener("click", () => {
      const node = state.graph.nodes.find((n) => n.id === li.dataset.node);
      if (node) showInspector({ kicker: node.category, title: node.title, node });
    }));
  }

  $("#layout-toggle").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    $$("#layout-toggle button").forEach((b) => b.classList.toggle("is-active", b === btn));
    const isGraph = btn.dataset.layout === "graph";
    $("#graph-wrap").hidden = !isGraph;
    $("#grid-wrap").hidden = isGraph;
    if (isGraph && graph) graph.resize();
  });

  $("#brain-search").addEventListener("input", (ev) => {
    const q = ev.target.value.toLowerCase();
    if (graph) graph.setQuery(q);
    $$("#grid-wrap li[data-node]").forEach((li) => {
      li.style.display = !q || li.textContent.toLowerCase().includes(q) ? "" : "none";
    });
  });

  $("#btn-seed").addEventListener("click", async () => {
    if (!confirm("Load the demo brand? This adds ~45 entries (existing ones are kept).")) return;
    await api("/api/brain/seed", { method: "POST" });
    refreshBrain();
  });

  /* ---- add / edit modal ---- */

  let modalSave = null;

  function openNodeModal(node) {
    $("#modal-title").textContent = node ? "Edit entry" : "Add entry";
    $("#modal-body").innerHTML = `
      <label>Category <select id="f-cat">${state.categories.map((c) =>
        `<option value="${c.slug}" ${node && node.category === c.slug ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></label>
      <label>Title <input id="f-title" value="${node ? esc(node.title) : ""}" /></label>
      <label>Body <textarea id="f-body" rows="5">${node ? esc(node.body) : ""}</textarea></label>
      <label>Tags (comma separated) <input id="f-tags" value="${node ? esc(node.tags.join(", ")) : ""}" /></label>`;
    $("#modal").hidden = false;
    $("#f-title").focus();
    modalSave = async () => {
      const payload = {
        category: $("#f-cat").value,
        title: $("#f-title").value.trim(),
        body: $("#f-body").value.trim(),
        tags: $("#f-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
      };
      if (!payload.title) return;
      if (node) await api(`/api/brain/node/${node.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      else await api("/api/brain/node", { method: "POST", body: JSON.stringify(payload) });
      $("#modal").hidden = true;
      hideInspector();
      refreshBrain();
    };
  }

  $("#btn-add").addEventListener("click", () => openNodeModal(null));
  $("#modal-cancel").addEventListener("click", () => { $("#modal").hidden = true; });
  $("#modal-save").addEventListener("click", () => modalSave && modalSave());

  /* ============================================================ studio */

  async function loadConfig() {
    state.config = await api("/api/studio/config");
    const engines = Object.entries(state.config.engines);
    $("#engine-toggle").innerHTML = engines.map(([id, e]) =>
      `<button data-engine="${id}" class="${id === state.config.default_engine ? "is-active" : ""}">${esc(e.label)}</button>`).join("");
    $("#aspect-select").innerHTML = state.config.aspects.map((a) =>
      `<option ${a === "9:16" ? "selected" : ""}>${a}</option>`).join("");
    $("#resolution-select").innerHTML = state.config.resolutions.map((r) => `<option>${r}</option>`).join("");
    $("#provider-note").textContent = state.config.fal
      ? "Fal AI key detected — renders bill the provider's actual rate."
      : "No FAL_KEY set. Renders write a local placeholder card so the loop still works.";
    fillModels();

    $("#engine-toggle").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      $$("#engine-toggle button").forEach((b) => b.classList.toggle("is-active", b === btn));
      fillModels();
    });
  }

  function currentEngine() {
    const active = $("#engine-toggle button.is-active");
    return active ? active.dataset.engine : state.config.default_engine;
  }

  function fillModels() {
    const engine = state.config.engines[currentEngine()];
    const list = state.kind === "video" ? engine.video_models : engine.image_models;
    $("#model-select").innerHTML = list.map((m) =>
      `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
    updateCost();
  }

  async function updateCost() {
    if (!state.config) return;
    try {
      const { cost } = await api("/api/studio/estimate", {
        method: "POST",
        body: JSON.stringify({
          engine: currentEngine(), model: $("#model-select").value,
          kind: state.kind, count: state.count,
        }),
      });
      $("#cost").textContent = `≈ $${cost.toFixed(2)}`;
      $("#settings-cost").textContent = `$${cost.toFixed(2)}`;
    } catch (_) { /* estimate is cosmetic */ }
  }

  $("#kind-toggle").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    $$("#kind-toggle button").forEach((b) => b.classList.toggle("is-active", b === btn));
    state.kind = btn.dataset.kind;
    $(".settings h3").textContent = state.kind === "video" ? "Video Settings" : "Image Settings";
    fillModels();
  });
  $("#model-select").addEventListener("change", updateCost);
  $("#count-minus").addEventListener("click", () => { state.count = Math.max(1, state.count - 1); $("#count").textContent = state.count; updateCost(); });
  $("#count-plus").addEventListener("click", () => { state.count = Math.min(4, state.count + 1); $("#count").textContent = state.count; updateCost(); });

  function renderTemplates() {
    const prompts = (state.graph ? state.graph.nodes : []).filter((n) => n.category === "prompts");
    $("#templates").innerHTML = prompts.length
      ? prompts.map((p) => `<button data-prompt="${esc(p.id)}"><b>${esc(p.title)}</b>${esc(trunc(p.body, 120))}</button>`).join("")
      : `<div class="note">Your prompt bank is empty. Save a prompt from chat and it shows up here.</div>`;
    $("#templates").querySelectorAll("[data-prompt]").forEach((btn) => btn.addEventListener("click", () => {
      const node = state.graph.nodes.find((n) => n.id === btn.dataset.prompt);
      if (node) { $("#render-prompt").value = node.body; $("#templates").hidden = true; }
    }));
  }

  $("#btn-templates").addEventListener("click", () => { $("#templates").hidden = !$("#templates").hidden; });

  async function loadRenders() {
    const { renders } = await api("/api/studio/renders");
    const grid = $("#studio-results");
    if (!renders.length) {
      grid.innerHTML = `<div class="empty"><h2>Your studio is empty</h2>
        <p>Generate an image or video below and it lands here.</p></div>`;
      return;
    }
    grid.innerHTML = renders.map((r) => {
      let frame = `<div class="frame">${r.status === "failed" ? esc(trunc(r.error, 90)) : "Generating…"}</div>`;
      if (r.status === "done" && r.url) {
        frame = r.kind === "video"
          ? `<div class="frame"><video src="${esc(r.url)}" muted loop autoplay playsinline></video></div>`
          : `<div class="frame"><img src="${esc(r.url)}" alt="" loading="lazy" /></div>`;
      }
      return `<div class="shot ${r.status === "failed" ? "failed" : ""}">${frame}
        <div class="cap"><b>${esc(r.status)}</b> · ${esc(r.aspect)} · $${Number(r.cost).toFixed(2)}<br>
        ${esc(trunc(r.prompt, 90))}</div></div>`;
    }).join("");

    const pending = renders.some((r) => r.status === "queued" || r.status === "generating");
    clearTimeout(state.pollTimer);
    if (pending) state.pollTimer = setTimeout(loadRenders, 2500);
  }

  $("#btn-generate").addEventListener("click", async () => {
    const prompt = $("#render-prompt").value.trim();
    if (!prompt) return;
    const btn = $("#btn-generate");
    btn.disabled = true;
    try {
      await api("/api/studio/render", {
        method: "POST",
        body: JSON.stringify({
          kind: state.kind, prompt, engine: currentEngine(), model: $("#model-select").value,
          aspect: $("#aspect-select").value, resolution: $("#resolution-select").value,
          count: state.count,
        }),
      });
      loadRenders();
    } catch (err) {
      alert("Render failed: " + err.message);
    } finally {
      btn.disabled = false;
    }
  });

  /* ============================================================ ad spy */

  function renderSpy() {
    if (!state.graph) return;
    const spies = state.graph.nodes.filter((n) => n.category === "adspy");
    $("#spy-grid").innerHTML = spies.length ? spies.map((n) => {
      const days = n.meta && n.meta.days_live;
      return `<div class="spy-card">
        ${days ? `<span class="days">${days} days live</span>` : `<span class="tag">unknown run time</span>`}
        <h4>${esc(n.title)}</h4><p>${esc(n.body)}</p></div>`;
    }).join("") : `<div class="note">No competitor ads logged yet. Tell the assistant about one and it will file it here.</div>`;
  }

  $("#btn-recommend").addEventListener("click", async () => {
    const btn = $("#btn-recommend");
    btn.disabled = true;
    $("#recs").innerHTML = `<div class="note">Thinking across the whole brain…</div>`;
    try {
      const data = await api("/api/recommendations", { method: "POST", body: JSON.stringify({ count: 4 }) });
      const recs = data.recommendations || [];
      $("#recs").innerHTML = recs.length ? recs.map((r) => `<div class="rec">
        <h4>${esc(r.title || "Untitled")}</h4>
        <p class="why"><b>${esc(r.angle || "")}</b> — ${esc(r.why || "")}</p>
        <pre>${esc(r.prompt || "")}</pre>
        <button class="btn small" data-send="${esc(r.prompt || "")}">Send to studio</button></div>`).join("")
        : `<div class="note">${esc(trunc(data.raw || "No recommendations came back.", 400))}</div>`;
      $("#recs").querySelectorAll("[data-send]").forEach((b) => b.addEventListener("click", () => {
        $("#render-prompt").value = b.dataset.send;
        $('.tab[data-view="studio"]').click();
      }));
    } catch (err) {
      $("#recs").innerHTML = `<div class="chat-error">${esc(err.message)}</div>`;
    } finally {
      btn.disabled = false;
    }
  });

  /* ============================================================ chat */

  const log = $("#chat-log");

  function addMsg(role, text) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.innerHTML = `<div class="who">${role === "user" ? "you" : "aal"}</div><div class="bubble"></div>`;
    el.querySelector(".bubble").textContent = text || "";
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  async function send(text) {
    const intro = log.querySelector(".chat-intro");
    if (intro) intro.remove();
    addMsg("user", text);

    const el = addMsg("assistant", "");
    const bubble = el.querySelector(".bubble");
    let think = null;
    let full = "";
    let brainChanged = false;
    let rendersChanged = false;

    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, session: SESSION }),
    });
    if (!res.ok || !res.body) {
      bubble.innerHTML = `<span class="chat-error">${esc(await res.text())}</span>`;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }

        if (ev.type === "text") {
          full += ev.text;
          bubble.textContent = full;
        } else if (ev.type === "thinking") {
          if (!think) {
            think = document.createElement("div");
            think.className = "think";
            el.insertBefore(think, bubble);
          }
          think.textContent += ev.text;
          think.scrollTop = think.scrollHeight;
        } else if (ev.type === "tool_start") {
          const chip = document.createElement("span");
          chip.className = "toolchip";
          chip.dataset.tool = ev.name;
          chip.textContent = "⟳ " + ev.name;
          el.insertBefore(chip, bubble);
        } else if (ev.type === "tool_end") {
          const chip = el.querySelector(`.toolchip[data-tool="${ev.name}"]:not(.done)`);
          if (chip) { chip.classList.add("done"); chip.textContent = "✓ " + ev.name; chip.title = ev.summary || ""; }
          if (ev.info && ev.info.brain_changed) brainChanged = true;
          if (ev.info && ev.info.renders_changed) rendersChanged = true;
        } else if (ev.type === "error") {
          const err = document.createElement("div");
          err.className = "chat-error";
          err.textContent = ev.message;
          el.appendChild(err);
        } else if (ev.type === "done") {
          if (ev.brain_changed) brainChanged = true;
          if (ev.renders_changed) rendersChanged = true;
        }
        log.scrollTop = log.scrollHeight;
      }
    }

    if (think) think.remove();
    if (brainChanged) await refreshBrain();
    if (rendersChanged) await loadRenders();
    if (state.speak && full) speak(full);
  }

  $("#chat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const box = $("#chat-text");
    const text = box.value.trim();
    if (!text) return;
    box.value = "";
    try { await send(text); } catch (err) { addMsg("assistant", "").querySelector(".bubble").textContent = String(err); }
  });

  $("#chat-text").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); $("#chat-form").requestSubmit(); }
  });

  $("#btn-clear").addEventListener("click", async () => {
    await fetch(`/api/chat/${SESSION}`, { method: "DELETE" });
    log.innerHTML = `<div class="chat-intro"><p>Conversation cleared. The brain is untouched — I still know the brand.</p></div>`;
  });

  /* ---- voice: dictate in, speak out (browser APIs, no server round trip) ---- */

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recog = null;
  $("#btn-mic").addEventListener("click", () => {
    if (!SR) return alert("This browser has no speech recognition. Chrome and Edge do.");
    if (recog) { recog.stop(); return; }
    recog = new SR();
    recog.lang = navigator.language || "en-US";
    recog.interimResults = true;
    recog.continuous = false;
    $("#btn-mic").classList.add("is-on");
    recog.onresult = (ev) => {
      const text = Array.from(ev.results).map((r) => r[0].transcript).join("");
      $("#chat-text").value = text;
    };
    recog.onend = () => {
      recog = null;
      $("#btn-mic").classList.remove("is-on");
      if ($("#chat-text").value.trim()) $("#chat-form").requestSubmit();
    };
    recog.start();
  });

  function speak(text) {
    if (!window.speechSynthesis) return;
    const clean = text.replace(/[*_`#>]/g, "").slice(0, 800);
    const utter = new SpeechSynthesisUtterance(clean);
    utter.rate = 1.05;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  }

  $("#btn-speak").addEventListener("click", () => {
    state.speak = !state.speak;
    $("#btn-speak").classList.toggle("is-on", state.speak);
    if (!state.speak && window.speechSynthesis) window.speechSynthesis.cancel();
  });

  /* ============================================================ boot */

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  (async function boot() {
    try {
      await loadConfig();
      await refreshBrain();
      await loadRenders();
      const { turns } = await api(`/api/chat/${SESSION}/history`);
      if (turns.length) {
        log.innerHTML = "";
        turns.forEach((t) => addMsg(t.role, t.text));
      }
    } catch (err) {
      console.error(err);
      alert("AAL failed to start: " + err.message);
    }
  })();
})();
