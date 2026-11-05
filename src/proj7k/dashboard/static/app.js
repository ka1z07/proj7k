// proj7k dashboard: the shared client for the sync, downscaler and profiler pages.
// One WebSocket per page; jobs are started over it and their progress comes back as job_update / job_log frames.
"use strict";

const P7 = (() => {
  const embedded = window.top !== window;
  if (embedded) document.documentElement.classList.add("embedded");
  // Entrance animations run while the page is first filled, not on every later refresh of its data.
  // A page still showing its #loading line waits for its content first.
  window.addEventListener("load", () => {
    const settle = () => setTimeout(() => document.documentElement.classList.add("settled"), 1500);
    if (!document.getElementById("loading")) return settle();
    const watch = new MutationObserver(() => { if (!document.getElementById("loading")) { watch.disconnect(); settle(); } });
    watch.observe(document.body, { childList: true, subtree: true });
  });

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

  // --- settings (the 设置 page) -------------------------------------------------------------------------------
  // One JSON object in localStorage, shared by the shell and every page in it (one origin); a change on the 设置 page
  // reaches the other pages through the storage event. Storage may be blocked: the defaults then hold.
  const SETTINGS_KEY = "p7.settings";
  const SETTINGS_DEFAULTS = {
    motion: "system",      // "system": follow the system's reduce-motion switch; "off": no animation at all
    tips: true,            // styled hover tips instead of the browser's own
    remember: true,        // each tool keeps its last choices (P7.rememberForm)
    toast: true,           // a note in the corner when a job finishes
    sound: false,          // a short chime with it
    osNotify: false,       // a system notification too, while the window is in the background
    downscaler_out: "", generator_out: "",  // default output folders ("" = the dashboard's own)
    package: true,         // pack an .osz by default
    sync_lazer: false,     // add practice charts to osu!lazer's collection by default
    goal_dan: "",          // the progress page's target tier ("" = none)
    fail_hint: true,       // after a failed or D-rank play, a note offering a practice chart and a replay diagnosis
  };
  const settings = (() => {
    let values = {};
    const load = () => { try { values = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {}; } catch (e) { values = {}; } };
    load();
    const followers = [];
    const get = (k) => (k in values ? values[k] : SETTINGS_DEFAULTS[k]);
    function set(k, v) {
      values[k] = v;
      try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(values)); } catch (e) { /* this page still has it */ }
      followers.forEach((fn) => fn(k, v));
    }
    function reset() {
      values = {};
      try { localStorage.removeItem(SETTINGS_KEY); } catch (e) { /* nothing stored */ }
      followers.forEach((fn) => fn(null));
    }
    window.addEventListener("storage", (e) => {
      if (e.key !== SETTINGS_KEY) return;
      load();
      followers.forEach((fn) => fn(null));
    });
    return { get, set, reset, onChange: (fn) => followers.push(fn), defaults: SETTINGS_DEFAULTS };
  })();
  if (settings.get("motion") === "off") document.documentElement.classList.add("no-motion");

  // A tool's form remembered between visits (settings.remember): the value of each listed input, select and radio
  // group, saved when it changes and put back by restore(), which fires `change` so the page's own handlers follow.
  // A select whose options load later is restored once they are there: call restore() again then.
  const FORM_PREFIX = "p7.form.";
  function rememberForm(page, fields) {
    const key = FORM_PREFIX + page;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(key) || "{}") || {}; } catch (e) { saved = {}; }
    const nodes = (f) => [...document.querySelectorAll(f.startsWith("@") ? `input[name="${f.slice(1)}"]` : `#${f}`)];
    const read = (f) => {
      const ns = nodes(f);
      if (f.startsWith("@")) { const c = ns.find((n) => n.checked); return c ? c.value : undefined; }
      if (!ns[0]) return undefined;
      return ns[0].type === "checkbox" ? ns[0].checked : ns[0].value;
    };
    function save() {
      if (!settings.get("remember")) return;
      fields.forEach((f) => { const v = read(f); if (v !== undefined) saved[f] = v; });
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) { /* not remembered this time */ }
    }
    fields.forEach((f) => nodes(f).forEach((n) => n.addEventListener("change", save)));
    function restore(only) {
      if (!settings.get("remember")) return;
      (only ? [].concat(only) : fields).forEach((f) => {
        if (!(f in saved)) return;
        const v = saved[f];
        const ns = nodes(f);
        let target = null;
        if (f.startsWith("@")) {
          target = ns.find((n) => n.value === v && !n.disabled);
          if (target && !target.checked) target.checked = true; else target = null;
        } else if (ns[0] && ns[0].type === "checkbox") {
          if (ns[0].checked !== !!v) { ns[0].checked = !!v; target = ns[0]; }
        } else if (ns[0] && (ns[0].tagName !== "SELECT" || [...ns[0].options].some((o) => o.value === v))) {
          if (ns[0].value !== v) { ns[0].value = v; target = ns[0]; }
        }
        if (target) target.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }
    return { restore, has: (f) => settings.get("remember") && f in saved };
  }
  function forgetForms() {
    try {
      Object.keys(localStorage).filter((k) => k.startsWith(FORM_PREFIX)).forEach((k) => localStorage.removeItem(k));
    } catch (e) { /* nothing stored */ }
  }

  // --- WebSocket --------------------------------------------------------------------------------------------
  let ws = null;
  let queue = [];
  const listeners = {};
  const jobWatchers = {};     // job id -> {onUpdate, onLog, resolve}
  const requestWatchers = {}; // request id -> watcher, until the job id is known
  let requestSeq = 0;
  // Uploads ride job_start base64-encoded; the server's MAX_MESSAGE_BYTES is 256 MiB, so leave room for the rest.
  const MAX_UPLOAD_BASE64 = 255 * 2 ** 20;

  function on(type, fn) { (listeners[type] = listeners[type] || []).push(fn); }
  function emit(type, msg) { (listeners[type] || []).forEach((fn) => { try { fn(msg); } catch (e) { console.error(e); } }); }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
      emit("connection", true);
      const pending = queue; queue = [];
      pending.forEach((m) => ws.send(m));
      Object.values(requestWatchers).forEach((w) => { w.sent = true; });
      // Jobs followed across a reconnect: the server may have restarted and forgotten them (see the `jobs` reply).
      if (Object.keys(jobWatchers).length) ws.send(JSON.stringify({ type: "jobs_list" }));
    };
    ws.onclose = () => {
      emit("connection", false);
      // A job_start already sent but not yet answered is lost with the connection (the server restarted, or closed
      // it because the message was too large); fail it instead of waiting forever.
      for (const [id, w] of Object.entries(requestWatchers)) {
        if (!w.sent) continue;
        delete requestWatchers[id];
        w.resolve(lostJob(tr("连接中断，任务没有送达看板（看板重启了，或上传的文件太大），请重新运行")));
      }
      setTimeout(connect, 2000);
    };
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
      } else if (msg.type === "jobs") {
        // The server's jobs after a reconnect: a followed job it no longer knows died with a restart; one that
        // finished while the connection was down resolves now.
        const known = Object.fromEntries((msg.jobs || []).map((j) => [j.id, j]));
        for (const [id, w] of Object.entries(jobWatchers)) {
          const job = known[id];
          if (job && !["done", "failed", "cancelled"].includes(job.status)) continue;
          delete jobWatchers[id];
          const final = job || lostJob(tr("看板重启了，这个任务随之中断，请重新运行"), id);
          w.onUpdate(final);
          w.resolve(final);
        }
      } else if (msg.type === "error" && msg.request_id in requestWatchers) {
        const w = requestWatchers[msg.request_id];
        delete requestWatchers[msg.request_id];
        w.resolve({ status: "failed", error: msg.message, artifacts: [], logs: [] });
      }
      emit(msg.type, msg);
    };
  }

  // Returns whether the message went out now (false: queued until the connection opens).
  function send(obj) {
    const text = JSON.stringify(obj);
    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(text); return true; }
    queue.push(text);
    return false;
  }

  function lostJob(error, id) {
    return { id, status: "failed", error, artifacts: [], logs: [] };
  }

  // Start a job; resolves with the finished job. `uploads` maps a parameter to a File.
  async function startJob(kind, params, { uploads, onUpdate, onLog } = {}) {
    const encoded = {};
    for (const [key, file] of Object.entries(uploads || {})) {
      if (file) encoded[key] = { name: file.name, data: await fileToBase64(file) };
    }
    // The server closes the connection on a message over MAX_MESSAGE_BYTES (server.py) instead of answering.
    const size = Object.values(encoded).reduce((n, u) => n + u.data.length, 0);
    if (size > MAX_UPLOAD_BASE64) {
      return lostJob(tr("上传的文件太大（{size} MB），看板最多收 {max} MB", { size: (size * 3 / 4 / 2 ** 20).toFixed(0), max: (MAX_UPLOAD_BASE64 * 3 / 4 / 2 ** 20).toFixed(0) }));
    }
    const request_id = `r${Date.now()}-${++requestSeq}`;
    return new Promise((resolve) => {
      const w = { onUpdate: onUpdate || (() => {}), onLog, resolve, sent: false };
      requestWatchers[request_id] = w;
      w.sent = send({ type: "job_start", kind, params, uploads: encoded, request_id });
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
  const STATUS_TEXT = { queued: tr("排队中"), running: tr("运行中"), done: tr("完成"), failed: tr("失败"), cancelled: tr("已取消") };
  // A running job that was asked to stop runs on to its next safe point (ADR-0049).
  const statusText = (job) => (job.stopping ? tr("取消中") : STATUS_TEXT[job.status] || job.status);

  // The cancel button of the job `getJob()` returns. A queued job is dropped at once; a running one asks for a second
  // click first (it may have run for minutes), then shows that it is stopping. Returns {node, update(job)}.
  function cancelControl(getJob) {
    let armed = null;  // the timer while a running job's cancel waits for its confirming click
    const disarm = () => { clearTimeout(armed); armed = null; };
    const node = el("button", { class: "small danger hidden", text: tr("取消"), onclick: (e) => {
      e.stopPropagation();
      const job = getJob();
      if (!job || job.stopping) return;
      if (job.status === "running" && !armed) {
        node.textContent = tr("确定取消？");
        armed = setTimeout(() => { armed = null; update(getJob()); }, 4000);
        return;
      }
      disarm();
      send({ type: "job_cancel", id: job.id });
    } });
    function update(job) {
      const active = !!job && (job.status === "queued" || job.status === "running");
      if (!active || job.stopping) disarm();
      node.classList.toggle("hidden", !active);
      node.disabled = !!(job && job.stopping);
      node.title = job && job.status === "running" ? tr("在下一个安全点停下，不会停在写入中途") : "";
      if (!armed) node.textContent = job && job.stopping ? tr("取消中…") : tr("取消");
    }
    return { node, update };
  }

  // How long a job has waited or run, live: "排队 0:12" while queued, "已运行 1:23" while running, "用时 1:23" after.
  // Times are the server's clock; the page's may differ a little, so a negative wait shows as zero.
  function jobClock(job) {
    if (!job) return "";
    const now = Date.now() / 1000;
    if (job.status === "queued") return job.created_at ? tr("排队 {t}", { t: duration(Math.max(0, now - job.created_at)) }) : "";
    if (!job.started_at) return "";
    const secs = Math.max(0, (job.finished_at || now) - job.started_at);
    return job.status === "running" ? tr("已运行 {t}", { t: duration(secs) }) : tr("用时 {t}", { t: duration(secs) });
  }
  // Every live clock on the page, ticked once a second together. `fn` returns false once it no longer needs ticks.
  const clocks = new Set();
  setInterval(() => clocks.forEach((fn) => { if (fn() === false) clocks.delete(fn); }), 1000);
  const tick = (fn) => { if (fn() !== false) clocks.add(fn); };

  // A card that follows one job: status, log, error and output files. Returns {update(job), log(line), reset()}.
  function jobPanel(container) {
    const badge = el("span", { class: "badge queued", text: "" });
    const title = el("span", { class: "hint" });
    const cancel = cancelControl(() => current);
    const error = el("div", { class: "job-error hidden" });
    const files = el("div", { class: "artifacts" });
    const logBox = el("div", { class: "log hidden" });
    const toggle = el("button", { class: "small", text: tr("显示日志"), onclick: () => {
      logBox.classList.toggle("hidden");
      toggle.textContent = logBox.classList.contains("hidden") ? tr("显示日志") : tr("隐藏日志");
    } });
    const root = el("div", { class: "hidden" },
      el("div", { class: "job-head" }, el("div", { class: "row" }, badge, title), el("div", { class: "row" }, cancel.node, toggle)),
      error, files, logBox);
    container.appendChild(root);
    let current = null;

    return {
      reset() {
        current = null; cancel.update(null); root.classList.remove("hidden");
        logBox.textContent = ""; files.innerHTML = ""; error.classList.add("hidden");
        badge.className = "badge queued"; badge.textContent = tr("提交中"); title.textContent = "";
      },
      update(job) {
        current = job;
        badge.className = `badge ${job.status}`;
        badge.textContent = statusText(job);
        const shown = job;
        const paint = () => {
          if (current !== shown) return false;
          const t = jobClock(job);
          title.textContent = job.id ? [tr("任务 {id}", { id: job.id }), t].filter(Boolean).join(" · ") : "";
          return job.status === "queued" || job.status === "running";
        };
        tick(paint);
        cancel.update(job);
        if (job.error) { error.textContent = job.error; error.classList.remove("hidden"); }
        files.innerHTML = "";
        (job.artifacts || []).forEach((a, i) => files.appendChild(artifactRow(job.id, i, a)));
      },
      log(line) {
        logBox.textContent += line + "\n";
        logBox.scrollTop = logBox.scrollHeight;
      },
      showLog() { logBox.classList.remove("hidden"); toggle.textContent = tr("隐藏日志"); },
    };
  }

  // A page's own queue of jobs (each tool's batch queue): every run gets an entry, in the order submitted, with its
  // job card and a slot for its results, so a second run can be queued while the first is still going (the dashboard
  // runs jobs that share data one after another, ADR-0043). Returns {add(label) -> entry, active()}; an entry is
  // {update(job), log(line), showLog(), results, label}.
  function jobQueue(container, title) {
    const count = el("span", { class: "hint" });
    const clear = el("button", { class: "small", text: tr("清除已结束"), title: tr("从这里移走已完成、失败或已取消的任务；「任务」页仍然保留它们"),
      onclick: () => { entries.filter((e) => !e.active()).forEach((e) => { e.root.remove(); entries.splice(entries.indexOf(e), 1); }); refresh(); } });
    const list = el("div", { class: "queue-list" });
    const root = el("div", { class: "card hidden" },
      el("div", { class: "queue-head" }, el("h2", { text: title || tr("任务队列") }), count, el("span", { class: "grow" }), clear), list);
    container.appendChild(root);
    const entries = [];
    function refresh() {
      const n = entries.filter((e) => e.active()).length;
      count.textContent = entries.length ? tr("{n} 个进行中 · 共 {total} 个", { n, total: entries.length }) : "";
      clear.disabled = n === entries.length;
      root.classList.toggle("hidden", !entries.length);
    }
    function add(label) {
      const jobBox = el("div");
      const results = el("div", { class: "queue-results" });
      const entryRoot = el("div", { class: "queue-item" }, el("div", { class: "queue-label ellipsis", text: label, title: label }), jobBox, results);
      list.append(entryRoot);
      const panel = jobPanel(jobBox);
      panel.reset();
      let status = "submitting";
      const entry = {
        root: entryRoot, results, label,
        active: () => ["submitting", "queued", "running"].includes(status),
        update(job) { status = job.status; panel.update(job); refresh(); },
        log: (line) => panel.log(line),
        showLog: () => panel.showLog(),
      };
      entries.push(entry);
      refresh();
      return entry;
    }
    return { add, active: () => entries.filter((e) => e.active()).length };
  }

  // What each kind of job is called, for the jobs page and the shell's notes.
  const JOB_KIND = {
    sync_setup: tr("安装 / 检查 bridge"), sync_once: tr("曲库同步"), sync_revert: tr("还原为官方星级"),
    lazer_control: tr("osu!lazer 开关"), downscale: tr("降阶练习"), generate: tr("谱面生成"),
    generator_project: tr("修改生成谱"),
    profile_replay: tr("回放诊断"), profile_player: tr("玩家画像"), profile_habits: tr("发力习惯"), import_replays: tr("导入回放"),
  };

  // A short note in the corner (the shell shows one when a job ends). `onclick` makes it a link; it fades on its own.
  // `actions` [{text, onclick, primary}] are buttons on the note; clicking one also closes it.
  function toast(text, { kind = "", sub = "", onclick = null, ms = 6000, actions = [] } = {}) {
    let box = document.querySelector(".p7-toasts");
    if (!box) { box = el("div", { class: "p7-toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(box); }
    const close = el("button", { class: "small plain", text: "✕", title: tr("关闭"), onclick: (e) => { e.stopPropagation(); gone(); } });
    const node = el("div", { class: `p7-toast ${kind}${onclick ? " link" : ""}`, onclick: onclick ? () => { onclick(); gone(); } : null },
      el("div", { class: "grow" }, el("div", { class: "t", text }), sub ? el("div", { class: "hint ellipsis", text: sub, title: sub }) : null,
        actions.length ? el("div", { class: "row toast-actions" }, ...actions.map((a) => el("button", { class: `small${a.primary ? " primary" : ""}`, text: a.text, title: a.title || null,
          onclick: (e) => { e.stopPropagation(); a.onclick(); gone(); } }))) : null), close);
    box.appendChild(node);
    while (box.children.length > 4) box.firstChild.remove();
    let timer = setTimeout(gone, ms);
    node.addEventListener("mouseenter", () => clearTimeout(timer));
    node.addEventListener("mouseleave", () => { timer = setTimeout(gone, 2500); });
    function gone() { clearTimeout(timer); node.classList.add("out"); setTimeout(() => node.remove(), motion ? 220 : 0); }
    return node;
  }
  // Two soft notes, rising for a job that finished, falling for one that failed.
  let audio = null;
  function chime(ok = true) {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const t0 = audio.currentTime;
      (ok ? [660, 880] : [520, 390]).forEach((f, i) => {
        const o = audio.createOscillator(), g = audio.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t0 + i * 0.14);
        g.gain.exponentialRampToValueAtTime(0.12, t0 + i * 0.14 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.14 + 0.3);
        o.connect(g).connect(audio.destination);
        o.start(t0 + i * 0.14); o.stop(t0 + i * 0.14 + 0.32);
      });
    } catch (e) { /* no audio here */ }
  }

  // Hover tips: an element's title shows in the dashboard's own bubble (settings.tips) a moment after the pointer
  // settles on it. While it shows, the text moves to data-p7tip so the browser's own tooltip stays away.
  (() => {
    let bubble = null, timer = null, owner = null;
    function hide() {
      clearTimeout(timer); timer = null;
      if (bubble) bubble.classList.remove("on");
      if (owner && owner.dataset.p7tip !== undefined) {
        // The page may have set a new title meanwhile; that one wins.
        if (!owner.hasAttribute("title")) owner.setAttribute("title", owner.dataset.p7tip);
        delete owner.dataset.p7tip;
      }
      owner = null;
    }
    function show(t, text) {
      if (!document.body.contains(t)) return hide();
      if (!bubble) { bubble = el("div", { class: "p7-tip", role: "tooltip" }); document.body.appendChild(bubble); }
      bubble.textContent = text;
      bubble.style.left = "0px"; bubble.style.top = "0px";
      bubble.classList.add("on");
      const r = t.getBoundingClientRect(), b = bubble.getBoundingClientRect();
      let top = r.top - b.height - 8;
      if (top < 6) top = r.bottom + 8;
      const left = Math.max(6, Math.min(window.innerWidth - b.width - 6, r.left + r.width / 2 - b.width / 2));
      bubble.style.left = `${left}px`; bubble.style.top = `${Math.min(top, window.innerHeight - b.height - 6)}px`;
    }
    document.addEventListener("pointerover", (e) => {
      if (!settings.get("tips") || e.pointerType === "touch") return;
      const t = e.target.closest && e.target.closest("[title]");
      if (!t || t === owner || t.tagName === "IFRAME" || t.tagName === "HTML") return;
      const text = (t.getAttribute("title") || "").trim();
      hide();
      if (!text) return;
      owner = t; t.dataset.p7tip = text; t.removeAttribute("title");
      timer = setTimeout(() => show(t, text), 350);
    });
    document.addEventListener("pointerout", (e) => { if (owner && !(e.relatedTarget && owner.contains(e.relatedTarget))) hide(); });
    ["pointerdown", "keydown", "blur"].forEach((k) => window.addEventListener(k, hide));
    window.addEventListener("scroll", hide, true);
  })();

  function artifactRow(jobId, index, a) {
    const buttons = [];
    if (a.kind === "html") {
      buttons.push(el("a", { href: a.url, target: "_blank", rel: "noopener", title: a.label }, el("button", { class: "small primary", text: tr("打开") })));
    } else {
      buttons.push(el("a", { href: a.url, download: a.name }, el("button", { class: "small", text: tr("下载") })));
    }
    if (a.kind === "osz") {
      buttons.push(el("button", { class: "small primary", text: tr("导入 osu!"), title: tr("用系统关联程序（osu!lazer）打开这个 .osz"),
        onclick: () => send({ type: "open_artifact", id: jobId, index }) }));
    }
    buttons.push(el("button", { class: "small", text: tr("打开文件夹"), onclick: () => send({ type: "open_artifact", id: jobId, index, reveal: true }) }));
    return el("div", { class: "artifact" },
      el("span", { class: "badge", text: a.label }), el("span", { class: "name", title: a.path, text: a.name }), ...buttons);
  }

  // A drop zone that also opens a file picker. Returns {get file(), set(file), clear()}.
  function dropZone(node, accept, onChange) {
    // Read before the page's static text is swapped for English, so looked up here.
    const idle = tr(node.textContent.trim().replace(/\s+/g, " "));
    const label = el("span", { text: idle });
    const input = el("input", { type: "file", accept, class: "hidden" });
    node.textContent = "";
    node.appendChild(label);
    node.after(input);
    let file = null;
    const set = (f) => {
      file = f || null;
      node.classList.toggle("has-file", !!file);
      label.textContent = file ? tr("已选择：{name}（点击更换，右键清除）", { name: file.name }) : idle;
      if (onChange) onChange(file);
    };
    node.addEventListener("click", () => input.click());
    node.addEventListener("contextmenu", (e) => { e.preventDefault(); input.value = ""; set(null); });
    input.addEventListener("change", () => set(input.files[0]));
    node.addEventListener("dragover", (e) => { e.preventDefault(); node.classList.add("over"); });
    node.addEventListener("dragleave", () => node.classList.remove("over"));
    node.addEventListener("drop", (e) => { e.preventDefault(); node.classList.remove("over"); if (e.dataTransfer.files[0]) set(e.dataTransfer.files[0]); });
    return { get file() { return file; }, set, clear() { input.value = ""; set(null); } };
  }

  const SKILL_LABELS = {
    jack: "Jack", tech: "Tech", speed: "Speed", stream: "Stream",
    ln_general: "LN Gen", ln_tech: "LN Tech", ln_inverse: "LN Inv", ln_release: "LN Rel",
  };

  // Pattern tags (ADR-0058): what a chart is made of, by the community's names, each with a one-line definition.
  const TAG_ZH = { jack: tr("叠键"), minijack: tr("小叠"), chordjack: tr("和弦叠"), longjack: tr("长叠"), stream: tr("单点流"), jumpstream: tr("双押流"), chordstream: tr("和弦流"), dense_chordstream: tr("密集和弦流"), trill: tr("交互"), jumptrill: tr("双押交互"), split_jumptrill: tr("分手交互"), bracket: tr("括号"), roll: tr("滚键"), stair: tr("楼梯"), anchor: tr("锚"), grace: tr("装饰音"), burst: tr("爆发"), polyrhythm: tr("复合节奏"), hybrid: tr("混合"), inverse: tr("反键"), release: tr("放手"), shield: tr("盾"), delay: tr("错位长条"), coordination: tr("协调") };
  const TAG_WHY = { jack: tr("同一列连续三下以上，不慢于当前节拍格"), minijack: tr("同一列连续两下"), chordjack: tr("三音以上的和弦连续叠键"), longjack: tr("同一列连续六下以上"), stream: tr("连续四行以上单音、不重复列的节拍行"), jumpstream: tr("由双音和弦组成的流"), chordstream: tr("由三音以上和弦组成的流"), dense_chordstream: tr("两秒内平均每行三音以上的和弦流"), trill: tr("两列单音交替四行以上"), jumptrill: tr("两组双音和弦交替四行以上"), split_jumptrill: tr("每个和弦左右手各一音的双押交互"), bracket: tr("多组交互叠在一起：列交错的交替和弦"), roll: tr("单音朝一个方向走过五列以上"), stair: tr("单音朝一个方向走过三到四列"), anchor: tr("一只手一半以上的音落在同一列，而这只手还在动"), grace: tr("紧贴前一行（不到 0.4 拍格）、落在别的列、不在爆发里"), burst: tr("连续四行以上快于周围的节拍格（0.6 以下）"), polyrhythm: tr("两种节奏同时出现：非二分的间隔（相邻或隔一行）占两秒窗口的四分之一以上"), hybrid: tr("两秒内米和长条混在一起"), inverse: tr("按下时另外三列以上正被按住"), release: tr("长条尾落在没有按键的行"), shield: tr("长条所在列在其头之前或尾之后一拍格内有米"), delay: tr("长条头或尾跨列错开不到半拍格"), coordination: tr("一次按下与另一列的放开相距不到四分之一拍格") };
  function tagName(k) { return TAG_ZH[k] || k || "—"; }
  // Chips for a chart's pattern labels (`patterns` is the engine frame's `patterns` dict), largest share first.
  function tagChips(patterns) {
    const labels = (patterns && patterns.labels) || [];
    if (!labels.length) return el("p", { class: "hint", text: tr("没有占到 8% 以上的模式") });
    return el("div", { class: "chips" }, labels.map((k) => el("span", { class: "chip", title: TAG_WHY[k] || k },
      `${tagName(k)} ${Math.round(100 * ((patterns.shares || {})[k] || 0))}%`)));
  }

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

  // --- technique ranges (ADR-0050) ----------------------------------------------------------------------------
  // The engine's eight pools, each with a range of stars: a two-handled slider and a box at each end; an empty box
  // (or a handle at its end of the track) leaves that side open. Used by the generator page and the chart editor.
  const MIX_POOLS = [["rc_jack", "Jack"], ["rc_tech", "Tech"], ["rc_speed", "Speed"], ["rc_stamina", "Stamina"],
    ["ln_general", tr("LN 综合")], ["ln_tech", "LN Tech"], ["ln_inverse", tr("反键")], ["ln_release", tr("LN 放键")]];
  const MIX_MAX = 15;
  // Returns {node, value() -> "rc_jack=4.5..6,..." ("" for none), set(mix), reference(pools, label)}.
  function mixControl() {
    const rows = {};
    const node = el("div", { class: "mix-ranges" });
    const fmt = (v) => (v === null ? "" : String(Math.round(v * 10) / 10));
    for (const [key, name] of MIX_POOLS) {
      const lo = el("input", { type: "number", step: "0.1", min: "0", max: String(MIX_MAX), placeholder: tr("不限"), class: "mix-num" });
      const hi = el("input", { type: "number", step: "0.1", min: "0", max: String(MIX_MAX), placeholder: tr("不限"), class: "mix-num" });
      const a = el("input", { type: "range", min: "0", max: String(MIX_MAX), step: "0.1", value: "0", "aria-label": tr("{name} 下限", { name }) });
      const b = el("input", { type: "range", min: "0", max: String(MIX_MAX), step: "0.1", value: String(MIX_MAX), "aria-label": tr("{name} 上限", { name }) });
      const fill = el("div", { class: "mix-fill" });
      const mark = el("div", { class: "mix-mark hidden" });
      const ref = el("span", { class: "mix-ref hint" });
      const clear = el("button", { class: "small mix-clear", text: "×", title: tr("清除这一行"), type: "button" });
      const r = { lo, hi, a, b, fill, mark, ref };
      const paint = () => {
        const x0 = Number(a.value) / MIX_MAX * 100, x1 = Number(b.value) / MIX_MAX * 100;
        fill.style.left = `${x0}%`; fill.style.width = `${Math.max(0, x1 - x0)}%`;
        const open = lo.value === "" && hi.value === "";
        row.classList.toggle("open", open);
      };
      const fromBoxes = () => {
        a.value = lo.value === "" ? "0" : String(Math.min(Number(lo.value), MIX_MAX));
        b.value = hi.value === "" ? String(MIX_MAX) : String(Math.min(Number(hi.value), MIX_MAX));
        paint();
      };
      const fromSliders = (moved) => {
        if (Number(a.value) > Number(b.value)) (moved === a ? b : a).value = moved.value;
        lo.value = Number(a.value) <= 0 ? "" : fmt(Number(a.value));
        hi.value = Number(b.value) >= MIX_MAX ? "" : fmt(Number(b.value));
        paint();
      };
      a.addEventListener("input", () => fromSliders(a));
      b.addEventListener("input", () => fromSliders(b));
      lo.addEventListener("input", fromBoxes);
      hi.addEventListener("input", fromBoxes);
      clear.addEventListener("click", () => { lo.value = ""; hi.value = ""; fromBoxes(); });
      const row = el("div", { class: "mix-row open" },
        el("span", { class: "mix-name", text: name }), lo,
        el("div", { class: "mix-track" }, el("div", { class: "mix-rail" }), fill, mark, a, b), hi, clear, ref);
      r.paint = paint; r.fromBoxes = fromBoxes;
      rows[key] = r;
      node.appendChild(row);
      paint();
    }
    function value() {
      const parts = [];
      for (const [key] of MIX_POOLS) {
        const r = rows[key];
        const lo = r.lo.value === "" ? null : Number(r.lo.value), hi = r.hi.value === "" ? null : Number(r.hi.value);
        if (lo === null && hi === null) continue;
        if (lo !== null && hi !== null && lo > hi) throw new Error(tr("{name} 的下限高于上限", { name: MIX_POOLS.find((p) => p[0] === key)[1] }));
        parts.push(`${key}=${lo === null ? "" : lo}..${hi === null ? "" : hi}`);
      }
      return parts.join(",");
    }
    // A mix as the server keeps it: {pool: [lo, hi]} ranges (a lean of -1..1 has no slider and is left out).
    function set(mix) {
      for (const [key] of MIX_POOLS) {
        const v = mix && Array.isArray(mix[key]) ? mix[key] : [null, null];
        rows[key].lo.value = v[0] === null || v[0] === undefined ? "" : fmt(v[0]);
        rows[key].hi.value = v[1] === null || v[1] === undefined ? "" : fmt(v[1]);
        rows[key].fromBoxes();
      }
    }
    // Each pool's stars in the chart (or the selection) now, as a mark on its track and a number beside it.
    function reference(pools, label) {
      for (const [key] of MIX_POOLS) {
        const r = rows[key], v = pools ? pools[key] : undefined;
        r.mark.classList.toggle("hidden", v === undefined);
        r.ref.textContent = v === undefined ? "" : `${Number(v).toFixed(2)}★`;
        r.ref.title = v === undefined ? "" : `${label || tr("现在")} ${Number(v).toFixed(2)}★`;
        if (v !== undefined) r.mark.style.left = `${Math.min(v, MIX_MAX) / MIX_MAX * 100}%`;
      }
    }
    return { node, value, set, reference };
  }

  // Motion is decoration: a system set to reduce motion, or the 设置 page's switch, gets every number and bar in its
  // final state at once.
  const motion = settings.get("motion") !== "off" && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  // A number that counts up from zero to `value` (formatted by `fmt`) once it is on the page.
  function counter(value, fmt, ms = 700) {
    const node = el("span", { text: fmt(motion ? 0 : value) });
    if (!motion || !value) { node.textContent = fmt(value); return node; }
    const t0 = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - t0) / ms);
      node.textContent = fmt(value * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    return node;
  }

  function stat(k, v) { return el("div", { class: "stat" }, el("div", { class: "k", text: k }), el("div", { class: "v", text: String(v) })); }

  function kv(pairs) {
    return el("dl", { class: "kv" }, pairs.filter((p) => p).map(([k, v]) => [el("dt", { text: k }), el("dd", { text: v === null || v === undefined ? "—" : String(v) })]));
  }

  function skillName(k) { return SKILL_LABELS[k] || k || "—"; }
  const pct = (x) => `${(x * 100).toFixed(1)}%`;

  // --- the game's current chart -----------------------------------------------------------------------------
  // The chart osu!lazer has selected, as the server last saw it ({chart, unresolved}); `fn` runs now and on every
  // change. Read again on each reconnect, so a page opened before the game or the server still catches up.
  let gameState = { chart: null, unresolved: null };
  const gameFollowers = [];
  function onGameChart(fn) { gameFollowers.push(fn); fn(gameState); }
  function setGameChart(msg) {
    gameState = { chart: msg.chart || null, unresolved: msg.unresolved || null };
    gameFollowers.forEach((fn) => { try { fn(gameState); } catch (e) { console.error(e); } });
  }
  on("game_chart", setGameChart);
  on("connection", (up) => { if (up) getJSON("/api/current").then(setGameChart).catch(() => {}); });

  const chartName = (c) => `${c.artist} - ${c.title} [${c.version}]`;

  // One line naming the game's current chart, for a card or the top bar.
  function gameChartLine(state) {
    if (state.chart) {
      const c = state.chart;
      return el("div", { class: "now-playing" },
        el("span", { class: "badge done", text: tr("游戏当前谱面") }),
        el("a", { href: "#", class: "np-title plain", title: tr("打开谱面详情"), text: chartName(c),
          onclick: (e) => { e.preventDefault(); openChart(c.md5_hash); } }),
        el("span", { class: "np-meta", text: `${Number(c.star_rating).toFixed(2)}★ · ${c.dan_tier || "—"} · ${tr("主技法 {skill}", { skill: skillName(c.dominant_technique) })}` }));
    }
    if (state.unresolved) {
      const u = state.unresolved;
      return el("div", { class: "now-playing" },
        el("span", { class: "badge failed", text: tr("不是 7K / 未入库") }),
        el("span", { class: "np-title", text: `${u.artist} - ${u.title} [${u.difficulty}]` }),
        el("span", { class: "np-meta", text: tr("游戏里选中的这张不在 7K 曲库索引里；换一张 7K 谱，或先在「曲库同步」里同步。") }));
    }
    return el("div", { class: "now-playing" },
      el("span", { class: "badge queued", text: tr("未检测到") }),
      el("span", { class: "np-meta", text: tr("打开 osu!lazer 并选一张 7K 谱，这里会自动跟上。") }));
  }

  // --- moving between pages ---------------------------------------------------------------------------------
  // Pages live in the shell's iframes (and the chart / play details in its side panel). A page asks the shell to
  // show another page, optionally with parameters that page acts on (P7.onNav); opened alone, it navigates itself.
  function toShell(msg) {
    if (embedded) window.top.postMessage({ p7: true, ...msg }, location.origin);
    else if (msg.kind === "detail") location.href = `/${msg.page}?${new URLSearchParams(msg.params)}`;
    else location.href = `/#${msg.page}`;
  }
  const navigate = (page, params) => toShell({ kind: "nav", page, params: params || {} });
  const openChart = (md5) => md5 && toShell({ kind: "detail", page: "chart", params: { md5 } });
  const openPlay = (id) => id && toShell({ kind: "detail", page: "play", params: { id } });
  const navFollowers = [], shownFollowers = [];
  function onNav(fn) { navFollowers.push(fn); }
  // Runs each time the shell brings this page back to the front (pages stay loaded while hidden).
  function onShown(fn) { shownFollowers.push(fn); }
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || !e.data.p7) return;
    const fns = e.data.kind === "params" ? navFollowers : e.data.kind === "shown" ? shownFollowers : [];
    fns.forEach((fn) => { try { fn(e.data.params || {}); } catch (err) { console.error(err); } });
  });
  // Tell the shell this page is ready for parameters held back while it loaded.
  if (embedded) window.addEventListener("load", () => window.top.postMessage({ p7: true, kind: "ready" }, location.origin));

  // --- the player's data ------------------------------------------------------------------------------------
  const query = new URLSearchParams(location.search);
  let playerName = query.get("player") || "";
  function playerParam() { return playerName ? `player=${encodeURIComponent(playerName)}` : ""; }
  const api = (path) => getJSON(`/api/me/${path}${path.includes("?") ? "&" : "?"}${playerParam()}`);
  const coverUrl = (md5, w) => `/api/me/cover/${md5}?w=${w || 480}`;

  const RANK_CLASS = { SS: "rank-ss", S: "rank-s", A: "rank-a", B: "rank-b", C: "rank-c", D: "rank-d", F: "rank-f" };
  function rankBadge(p) {
    const label = p.rank_label || p;
    const silver = p.rank === "XH" || p.rank === "SH";
    return el("span", { class: `rank ${RANK_CLASS[label] || ""}${silver ? " silver" : ""}`, text: label, title: silver ? tr("{rank}（带 HD/FL 的银色评级）", { rank: label }) : label });
  }
  function cover(md5, has, w, cls) {
    return el("div", { class: `cover ${cls || ""}` }, has ? el("img", { src: coverUrl(md5, w), loading: "lazy", alt: "" }) : null);
  }
  const acc = (x) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(2)}%`);
  const stars = (x) => (x === null || x === undefined || x < 0 ? "—" : `${Number(x).toFixed(2)}★`);
  const num = (x) => (x === null || x === undefined ? "—" : Number(x).toLocaleString(P7I18N.locale));
  function ago(iso) {
    if (!iso) return "—";
    const t = new Date(iso).getTime();
    const d = (Date.now() - t) / 1000;
    if (d < 60) return tr("刚刚");
    if (d < 3600) return tr("{n} 分钟前", { n: Math.floor(d / 60) });
    if (d < 86400) return tr("{n} 小时前", { n: Math.floor(d / 3600) });
    if (d < 86400 * 30) return tr("{n} 天前", { n: Math.floor(d / 86400) });
    return new Date(t).toLocaleDateString(P7I18N.locale);
  }
  const fullDate = (iso) => (iso ? new Date(iso).toLocaleString(P7I18N.locale, { hour12: false }) : "—");
  function duration(s) {
    s = Math.round(s || 0);
    if (s >= 3600) return tr("{h} 小时 {m} 分", { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  function modBadges(mods) { return (mods || []).map((m) => el("span", { class: "mod", text: m })); }
  // A chart's name: the original-language title when it has one, the romanised one in the tooltip.
  function chartTitle(c) {
    const t = c.title_unicode || c.title || tr("（无标题）");
    return el("span", { class: "chart-title", title: `${c.artist} - ${c.title} [${c.version}]`, text: t });
  }
  // Clicking anything that names a chart or a play opens it.
  function chartLink(c, ...children) {
    return el("a", { href: "#", class: "plain", onclick: (e) => { e.preventDefault(); e.stopPropagation(); openChart(c.md5); } }, ...children);
  }

  // One play as a list row: cover, chart, mods, rank, accuracy, stars and when. Clicking it opens the play.
  function playRow(p, { showChart = true } = {}) {
    return el("div", { class: "list-row", onclick: () => openPlay(p.id), title: tr("查看这局") },
      showChart ? cover(p.md5, p.has_cover, 160, "thumb") : null,
      el("div", { class: "grow" },
        showChart ? el("div", { class: "ellipsis" }, chartLink(p, chartTitle(p)), el("span", { class: "muted", text: ` [${p.version}]` })) : null,
        el("div", { class: "hint ellipsis" }, modBadges(p.mods), `${num(p.score)} · ${p.max_combo}x`,
          p.ur !== null && p.ur !== undefined ? ` · UR ${p.ur}` : "", showChart ? "" : ` · ${fullDate(p.date)}`)),
      el("div", { class: "side" }, rankBadge(p)),
      el("div", { class: "side" }, el("div", { text: acc(p.accuracy) }), el("div", { class: "hint", text: `${stars(p.stars)} ${p.dan || ""}` })),
      showChart ? el("div", { class: "side hint", style: "width:74px", text: ago(p.date) }) : null);
  }

  // A floating tooltip for SVG charts: show(evt, lines) / hide().
  const tip = (() => {
    let node = null;
    return {
      show(evt, lines) {
        if (!node) { node = el("div", { class: "tooltip" }); document.body.appendChild(node); }
        node.replaceChildren(...lines.map((l, i) => el("div", { class: i ? "hint" : "", text: l })));
        node.style.display = "block";
        const x = Math.min(evt.clientX + 14, window.innerWidth - node.offsetWidth - 8);
        node.style.left = `${x}px`; node.style.top = `${evt.clientY + 14}px`;
      },
      hide() { if (node) node.style.display = "none"; },
    };
  })();
  // The SVG namespace, derived from the page's own (XHTML) one: the static files carry no URLs at all.
  const SVG_NS = document.documentElement.namespaceURI.replace("1999/xhtml", "2000/svg");
  const svg = (tag, attrs, ...children) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) node.setAttribute(k, v);
    children.flat().forEach((c) => c && node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return node;
  };

  connect();
  return {
    el, $, on, send, startJob, getJSON, jobPanel, jobQueue, jobClock, tick, settings, rememberForm, forgetForms, toast, chime, JOB_KIND, artifactRow, STATUS_TEXT, statusText, cancelControl, dropZone, skillBars, tagChips, tagName, TAG_ZH, TAG_WHY, stat, kv, skillName, pct, embedded, SKILL_LABELS, motion, counter,
    onGameChart, gameChartLine, chartName,
    navigate, openChart, openPlay, onNav, onShown, api, coverUrl, rankBadge, cover, acc, stars, num, ago, fullDate, duration,
    modBadges, chartTitle, chartLink, query, playRow, tip, svg, mixControl, MIX_POOLS,
    get player() { return playerName; }, set player(v) { playerName = v || ""; },
  };
})();
