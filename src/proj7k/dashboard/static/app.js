// proj7k dashboard: the shared client for the sync, downscaler and profiler pages.
// One WebSocket per page; jobs are started over it and their progress comes back as job_update / job_log frames.
"use strict";

const P7 = (() => {
  const embedded = window.top !== window;
  if (embedded) document.documentElement.classList.add("embedded");

  // --- DOM helper ---------------------------------------------------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    }
    return node;
  }
  const $ = (sel, root) => (root || document).querySelector(sel);

  // --- WebSocket --------------------------------------------------------------------------------------------
  let ws = null;
  let queue = [];
  const listeners = {};
  const jobWatchers = {};     // job id -> {onUpdate, onLog, resolve}
  const requestWatchers = {}; // request id -> watcher, until the job id is known
  let requestSeq = 0;

  function on(type, fn) { (listeners[type] = listeners[type] || []).push(fn); }
  function emit(type, msg) { (listeners[type] || []).forEach((fn) => { try { fn(msg); } catch (e) { console.error(e); } }); }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
      emit("connection", true);
      const pending = queue; queue = [];
      pending.forEach((m) => ws.send(m));
    };
    ws.onclose = () => { emit("connection", false); setTimeout(connect, 2000); };
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "job_started" && msg.request_id in requestWatchers) {
        const w = requestWatchers[msg.request_id];
        delete requestWatchers[msg.request_id];
        jobWatchers[msg.job.id] = w;
        w.onUpdate(msg.job);
      } else if (msg.type === "job_update") {
        const w = jobWatchers[msg.job.id];
        if (w) {
          w.onUpdate(msg.job);
          if (["done", "failed", "cancelled"].includes(msg.job.status)) {
            delete jobWatchers[msg.job.id];
            w.resolve(msg.job);
          }
        }
      } else if (msg.type === "job_log") {
        const w = jobWatchers[msg.id];
        if (w && w.onLog) w.onLog(msg.line);
      } else if (msg.type === "error" && msg.request_id in requestWatchers) {
        const w = requestWatchers[msg.request_id];
        delete requestWatchers[msg.request_id];
        w.resolve({ status: "failed", error: msg.message, artifacts: [], logs: [] });
      }
      emit(msg.type, msg);
    };
  }

  function send(obj) {
    const text = JSON.stringify(obj);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(text); else queue.push(text);
  }

  // Start a job; resolves with the finished job. `uploads` maps a parameter to a File.
  async function startJob(kind, params, { uploads, onUpdate, onLog } = {}) {
    const encoded = {};
    for (const [key, file] of Object.entries(uploads || {})) {
      if (file) encoded[key] = { name: file.name, data: await fileToBase64(file) };
    }
    const request_id = `r${Date.now()}-${++requestSeq}`;
    return new Promise((resolve) => {
      requestWatchers[request_id] = { onUpdate: onUpdate || (() => {}), onLog, resolve };
      send({ type: "job_start", kind, params, uploads: encoded, request_id });
    });
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-store" });
    return res.json();
  }

  // --- widgets ----------------------------------------------------------------------------------------------
  const STATUS_TEXT = { queued: "排队中", running: "运行中", done: "完成", failed: "失败", cancelled: "已取消" };

  // A card that follows one job: status, log, error and output files. Returns {update(job), log(line), reset()}.
  function jobPanel(container) {
    const badge = el("span", { class: "badge queued", text: "" });
    const title = el("span", { class: "hint" });
    const cancel = el("button", { class: "small hidden", text: "取消", onclick: () => current && send({ type: "job_cancel", id: current.id }) });
    const error = el("div", { class: "job-error hidden" });
    const files = el("div", { class: "artifacts" });
    const logBox = el("div", { class: "log hidden" });
    const toggle = el("button", { class: "small", text: "显示日志", onclick: () => {
      logBox.classList.toggle("hidden");
      toggle.textContent = logBox.classList.contains("hidden") ? "显示日志" : "隐藏日志";
    } });
    const root = el("div", { class: "hidden" },
      el("div", { class: "job-head" }, el("div", { class: "row" }, badge, title), el("div", { class: "row" }, cancel, toggle)),
      error, files, logBox);
    container.appendChild(root);
    let current = null;

    return {
      reset() {
        current = null; root.classList.remove("hidden");
        logBox.textContent = ""; files.innerHTML = ""; error.classList.add("hidden");
        badge.className = "badge queued"; badge.textContent = "提交中"; title.textContent = "";
      },
      update(job) {
        current = job;
        badge.className = `badge ${job.status}`;
        badge.textContent = STATUS_TEXT[job.status] || job.status;
        const secs = job.started_at ? ((job.finished_at || Date.now() / 1000) - job.started_at) : 0;
        title.textContent = job.id ? `任务 ${job.id}${secs ? ` · ${secs.toFixed(1)}s` : ""}` : "";
        cancel.classList.toggle("hidden", job.status !== "queued");
        if (job.error) { error.textContent = job.error; error.classList.remove("hidden"); }
        files.innerHTML = "";
        (job.artifacts || []).forEach((a, i) => files.appendChild(artifactRow(job.id, i, a)));
      },
      log(line) {
        logBox.textContent += line + "\n";
        logBox.scrollTop = logBox.scrollHeight;
      },
      showLog() { logBox.classList.remove("hidden"); toggle.textContent = "隐藏日志"; },
    };
  }

  function artifactRow(jobId, index, a) {
    const buttons = [];
    if (a.kind === "html") {
      buttons.push(el("a", { href: a.url, target: "_blank", rel: "noopener" }, el("button", { class: "small primary", text: "打开" })));
    } else {
      buttons.push(el("a", { href: a.url, download: a.name }, el("button", { class: "small", text: "下载" })));
    }
    if (a.kind === "osz") {
      buttons.push(el("button", { class: "small primary", text: "导入 osu!", title: "用系统关联程序（osu!lazer）打开这个 .osz",
        onclick: () => send({ type: "open_artifact", id: jobId, index }) }));
    }
    buttons.push(el("button", { class: "small", text: "打开文件夹", onclick: () => send({ type: "open_artifact", id: jobId, index, reveal: true }) }));
    return el("div", { class: "artifact" },
      el("span", { class: "badge", text: a.label }), el("span", { class: "name", title: a.path, text: a.name }), ...buttons);
  }

  // A drop zone that also opens a file picker. Returns {get file(), clear()}.
  function dropZone(node, accept, onChange) {
    const idle = node.textContent.trim();
    const label = el("span", { text: idle });
    const input = el("input", { type: "file", accept, class: "hidden" });
    node.textContent = "";
    node.appendChild(label);
    node.after(input);
    let file = null;
    const set = (f) => {
      file = f || null;
      node.classList.toggle("has-file", !!file);
      label.textContent = file ? `已选择：${file.name}（点击更换，右键清除）` : idle;
      if (onChange) onChange(file);
    };
    node.addEventListener("click", () => input.click());
    node.addEventListener("contextmenu", (e) => { e.preventDefault(); input.value = ""; set(null); });
    input.addEventListener("change", () => set(input.files[0]));
    node.addEventListener("dragover", (e) => { e.preventDefault(); node.classList.add("over"); });
    node.addEventListener("dragleave", () => node.classList.remove("over"));
    node.addEventListener("drop", (e) => { e.preventDefault(); node.classList.remove("over"); if (e.dataTransfer.files[0]) set(e.dataTransfer.files[0]); });
    return { get file() { return file; }, clear() { input.value = ""; set(null); } };
  }

  const SKILL_LABELS = {
    jack: "Jack", tech: "Tech", speed: "Speed", stream: "Stream",
    ln_general: "LN Gen", ln_tech: "LN Tech", ln_inverse: "LN Inv", ln_release: "LN Rel",
  };

  // Horizontal bars of eight skill stars; `b` (optional) is drawn over `a` for a before/after comparison.
  function skillBars(a, b) {
    const keys = Object.keys(SKILL_LABELS).filter((k) => k in a || (b && k in b));
    const max = Math.max(1, ...keys.map((k) => Math.max(a[k] || 0, (b && b[k]) || 0)));
    return el("div", { class: "bars" }, keys.map((k) => {
      const va = a[k] || 0, vb = b ? (b[k] || 0) : null;
      return el("div", { class: "bar-row" },
        el("span", { text: SKILL_LABELS[k] }),
        el("div", { class: "bar-track" },
          el("div", { class: "bar-fill a", style: `width:${(va / max) * 100}%` }),
          vb !== null ? el("div", { class: "bar-fill b", style: `width:${(vb / max) * 100}%` }) : null),
        el("span", { class: "bar-val", text: vb !== null ? `${va.toFixed(2)} → ${vb.toFixed(2)}★` : `${va.toFixed(2)}★` }));
    }));
  }

  function stat(k, v) { return el("div", { class: "stat" }, el("div", { class: "k", text: k }), el("div", { class: "v", text: String(v) })); }

  function kv(pairs) {
    return el("dl", { class: "kv" }, pairs.filter((p) => p).map(([k, v]) => [el("dt", { text: k }), el("dd", { text: v === null || v === undefined ? "—" : String(v) })]));
  }

  function skillName(k) { return SKILL_LABELS[k] || k || "—"; }
  const pct = (x) => `${(x * 100).toFixed(1)}%`;

  connect();
  return { el, $, on, send, startJob, getJSON, jobPanel, dropZone, skillBars, stat, kv, skillName, pct, embedded, SKILL_LABELS };
})();
