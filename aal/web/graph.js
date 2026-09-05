/* Force-directed brain graph on a canvas. No dependencies.
   Hub -> category -> leaf, laid out by a small spring/repulsion simulation. */
(function () {
  "use strict";

  const TAU = Math.PI * 2;

  class BrainGraph {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.onSelect = (opts && opts.onSelect) || function () {};
      this.nodes = [];
      this.links = [];
      this.byId = new Map();
      this.scale = 1;
      this.tx = 0;
      this.ty = 0;
      this.hover = null;
      this.selected = null;
      this.query = "";
      this.alpha = 1;
      this.raf = null;
      this._bind();
      this.resize();
    }

    /* ---------------------------------------------------------- data */

    setData(graph) {
      const prev = new Map(this.nodes.map((n) => [n.id, n]));
      const w = this.canvas.clientWidth || 900;
      const h = this.canvas.clientHeight || 600;

      const nodes = [];
      const links = [];
      const hub = {
        id: "hub", kind: "hub", label: graph.brand, count: graph.total,
        color: "#6f9dff", r: 26, x: w / 2, y: h / 2, vx: 0, vy: 0, fixed: true,
      };
      nodes.push(hub);

      const cats = graph.categories;
      cats.forEach((cat, i) => {
        const angle = (i / cats.length) * TAU - Math.PI / 2;
        const radius = Math.min(w, h) * 0.27;
        nodes.push({
          id: cat.id, kind: "cat", slug: cat.slug, label: cat.label, count: cat.count,
          color: cat.color, glyph: cat.glyph, about: cat.about, r: 15,
          x: w / 2 + Math.cos(angle) * radius, y: h / 2 + Math.sin(angle) * radius,
          vx: 0, vy: 0, angle,
        });
        links.push({ a: "hub", b: cat.id, dashed: true, len: radius });
      });

      const catById = new Map(cats.map((c) => [c.slug, c]));
      const seen = new Map();
      graph.nodes.forEach((node) => {
        const cat = catById.get(node.category);
        if (!cat) return;
        const n = seen.get(node.category) || 0;
        seen.set(node.category, n + 1);
        const parent = nodes.find((p) => p.id === cat.id);
        const spread = (n - 2) * 0.34;
        nodes.push({
          id: node.id, kind: "leaf", slug: node.category, label: node.title,
          color: cat.color, r: 5.5, data: node,
          x: parent.x + Math.cos(parent.angle + spread) * 90,
          y: parent.y + Math.sin(parent.angle + spread) * 90,
          vx: 0, vy: 0,
        });
        links.push({ a: cat.id, b: node.id, len: 84 });
      });

      graph.edges.forEach((e) => {
        if (prevHas(nodes, e.src) && prevHas(nodes, e.dst)) {
          links.push({ a: e.src, b: e.dst, len: 130, cross: true });
        }
      });

      // Keep positions across refreshes so the graph does not jump around.
      nodes.forEach((n) => {
        const old = prev.get(n.id);
        if (old) { n.x = old.x; n.y = old.y; n.vx = old.vx; n.vy = old.vy; }
      });

      this.nodes = nodes;
      this.links = links;
      this.byId = new Map(nodes.map((n) => [n.id, n]));
      this.alpha = 1;
      this.start();
    }

    setQuery(q) {
      this.query = (q || "").trim().toLowerCase();
      this.alpha = Math.max(this.alpha, 0.12);
      this.start();
    }

    matches(node) {
      if (!this.query) return true;
      const hay = (node.label + " " + (node.data ? node.data.body + " " + node.data.tags.join(" ") : "")).toLowerCase();
      return hay.includes(this.query);
    }

    /* ---------------------------------------------------------- sim */

    step() {
      const nodes = this.nodes;
      const n = nodes.length;
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      const cx = w / 2, cy = h / 2;
      const repel = n > 400 ? 900 : 2600;

      for (let i = 0; i < n; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < n; j++) {
          const b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
          if (d2 > 90000) continue;
          const f = repel / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
        }
      }

      this.links.forEach((l) => {
        const a = this.byId.get(l.a), b = this.byId.get(l.b);
        if (!a || !b) return;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 1;
        const f = (d - (l.len || 100)) * 0.012;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      });

      nodes.forEach((node) => {
        if (node.fixed) { node.x = cx; node.y = cy; node.vx = node.vy = 0; return; }
        node.vx += (cx - node.x) * 0.0016;
        node.vy += (cy - node.y) * 0.0016;
        node.vx *= 0.86; node.vy *= 0.86;
        node.x += node.vx * this.alpha;
        node.y += node.vy * this.alpha;
      });

      this.alpha *= 0.994;
    }

    /* ---------------------------------------------------------- render */

    draw() {
      const ctx = this.ctx;
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.translate(this.tx, this.ty);
      ctx.scale(this.scale, this.scale);

      this.links.forEach((l) => {
        const a = this.byId.get(l.a), b = this.byId.get(l.b);
        if (!a || !b) return;
        const lit = this.query && (this.matches(a) || this.matches(b));
        ctx.beginPath();
        ctx.setLineDash(l.dashed || l.cross ? [3, 5] : []);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = l.cross ? "rgba(157,123,255,.35)" : (lit ? "rgba(180,205,255,.45)" : "rgba(120,150,200,.14)");
        ctx.lineWidth = l.cross ? 1 : 0.8;
        ctx.stroke();
      });
      ctx.setLineDash([]);

      this.nodes.forEach((node) => {
        const dim = this.query && !this.matches(node);
        const sel = this.selected === node.id;
        const hov = this.hover === node.id;
        ctx.globalAlpha = dim ? 0.16 : 1;

        if (node.kind !== "leaf" || sel || hov) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, node.r * 2.4, 0, TAU);
          ctx.fillStyle = hexA(node.color, node.kind === "hub" ? 0.22 : 0.12);
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + (hov ? 2 : 0), 0, TAU);
        ctx.fillStyle = node.kind === "hub" ? node.color : hexA(node.color, 0.16);
        ctx.fill();
        ctx.lineWidth = sel ? 2.2 : 1.2;
        ctx.strokeStyle = sel ? "#ffffff" : node.color;
        ctx.stroke();

        if (node.kind === "hub") {
          ctx.fillStyle = "#04070f";
          ctx.font = "700 13px ui-sans-serif, system-ui";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText("AAL", node.x, node.y);
        } else if (node.kind === "cat") {
          ctx.fillStyle = node.color;
          ctx.font = "12px ui-sans-serif, system-ui";
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(node.glyph || "•", node.x, node.y);
        }

        if (node.kind !== "leaf" || hov || sel || (this.query && this.matches(node))) {
          ctx.textAlign = "center"; ctx.textBaseline = "top";
          ctx.fillStyle = node.kind === "leaf" ? "#9db0cc" : "#dbe6f7";
          ctx.font = node.kind === "leaf" ? "10.5px ui-sans-serif, system-ui"
                                          : "600 11.5px ui-sans-serif, system-ui";
          const label = node.label.length > 30 ? node.label.slice(0, 29) + "…" : node.label;
          ctx.fillText(label, node.x, node.y + node.r + 6);
          if (node.kind === "cat") {
            ctx.fillStyle = "#5b6b87";
            ctx.font = "10px ui-monospace, monospace";
            ctx.fillText(String(node.count), node.x, node.y + node.r + 20);
          }
        }
        ctx.globalAlpha = 1;
      });

      ctx.restore();
    }

    frame() {
      this.step();
      this.draw();
      if (this.alpha > 0.004 || this._dragging) {
        this.raf = requestAnimationFrame(() => this.frame());
      } else {
        this.raf = null;
        this.draw();
      }
    }

    start() {
      if (!this.raf) this.raf = requestAnimationFrame(() => this.frame());
    }

    /* ---------------------------------------------------------- input */

    resize() {
      this.dpr = window.devicePixelRatio || 1;
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      this.canvas.width = Math.max(1, Math.floor(w * this.dpr));
      this.canvas.height = Math.max(1, Math.floor(h * this.dpr));
      this.alpha = Math.max(this.alpha, 0.25);
      this.start();
    }

    toWorld(ev) {
      const rect = this.canvas.getBoundingClientRect();
      return {
        x: (ev.clientX - rect.left - this.tx) / this.scale,
        y: (ev.clientY - rect.top - this.ty) / this.scale,
      };
    }

    pick(pt) {
      let best = null, bestD = Infinity;
      for (const node of this.nodes) {
        const d = Math.hypot(node.x - pt.x, node.y - pt.y);
        const hit = node.r + 8;
        if (d < hit && d < bestD) { best = node; bestD = d; }
      }
      return best;
    }

    _bind() {
      const c = this.canvas;
      let panning = false, last = null;

      c.addEventListener("pointerdown", (ev) => {
        const pt = this.toWorld(ev);
        const node = this.pick(pt);
        if (node && node.kind !== "hub") {
          this._dragging = node;
          node.fixed = false;
          c.setPointerCapture(ev.pointerId);
        } else {
          panning = true;
          last = { x: ev.clientX, y: ev.clientY };
        }
      });

      c.addEventListener("pointermove", (ev) => {
        if (this._dragging) {
          const pt = this.toWorld(ev);
          this._dragging.x = pt.x; this._dragging.y = pt.y;
          this._dragging.vx = this._dragging.vy = 0;
          this.alpha = Math.max(this.alpha, 0.35);
          this.start();
          return;
        }
        if (panning) {
          this.tx += ev.clientX - last.x;
          this.ty += ev.clientY - last.y;
          last = { x: ev.clientX, y: ev.clientY };
          this.draw();
          return;
        }
        const hit = this.pick(this.toWorld(ev));
        const id = hit ? hit.id : null;
        if (id !== this.hover) {
          this.hover = id;
          c.style.cursor = hit ? "pointer" : "grab";
          this.draw();
        }
      });

      const release = (ev) => {
        if (this._dragging) {
          this._dragging = null;
          this.alpha = Math.max(this.alpha, 0.2);
          this.start();
        }
        panning = false;
        if (ev) { try { c.releasePointerCapture(ev.pointerId); } catch (_) {} }
      };
      c.addEventListener("pointerup", (ev) => {
        const moved = this._dragging;
        release(ev);
        if (!moved) return;
      });
      c.addEventListener("pointercancel", release);
      window.addEventListener("pointerup", () => { panning = false; });

      c.addEventListener("click", (ev) => {
        const hit = this.pick(this.toWorld(ev));
        this.selected = hit ? hit.id : null;
        this.draw();
        this.onSelect(hit || null);
      });

      c.addEventListener("wheel", (ev) => {
        ev.preventDefault();
        const rect = c.getBoundingClientRect();
        const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
        const factor = Math.exp(-ev.deltaY * 0.0015);
        const next = Math.min(3, Math.max(0.3, this.scale * factor));
        this.tx = mx - (mx - this.tx) * (next / this.scale);
        this.ty = my - (my - this.ty) * (next / this.scale);
        this.scale = next;
        this.draw();
      }, { passive: false });

      window.addEventListener("resize", () => this.resize());
    }
  }

  function prevHas(nodes, id) { return nodes.some((n) => n.id === id); }

  function hexA(hex, alpha) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  window.BrainGraph = BrainGraph;
})();
