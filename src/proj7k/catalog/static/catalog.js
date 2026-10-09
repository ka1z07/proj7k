// proj7k catalog: the listing and the beatmapset page. No external scripts; covers come from osu!'s CDN
// when the set has an osu! id and fall back to a gradient.
"use strict";

const Catalog = (() => {
  // osu!'s difficulty colour spectrum (osu-web `getDiffColour`): RGB interpolated with gamma 2.2.
  const DOMAIN = [0.1, 1.25, 2, 2.5, 3.3, 4.2, 4.9, 5.8, 6.7, 7.7, 9];
  const RANGE = ["#4290fb", "#4fc0ff", "#4fffd5", "#7cff4f", "#f6f05c", "#ff8068", "#ff4e6f", "#c645b8", "#6563de", "#18158e", "#000000"];
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  function starColour(sr) {
    if (sr == null) return null;
    if (sr < 0.1) return "#aaaaaa";
    if (sr >= 9) return "#000000";
    let i = DOMAIN.findIndex((d) => sr < d) - 1;
    const t = (sr - DOMAIN[i]) / (DOMAIN[i + 1] - DOMAIN[i]);
    const a = rgb(RANGE[i]), b = rgb(RANGE[i + 1]), g = 2.2;
    const c = a.map((x, k) => Math.round(Math.pow(Math.pow(x / 255, g) * (1 - t) + Math.pow(b[k] / 255, g) * t, 1 / g) * 255));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  const starText = (sr) => (sr >= 6.5 ? "#ffd966" : "rgba(0,0,0,.8)");

  const SKILL_LABELS = {
    rc_jack: "Jack", rc_tech: "Tech", rc_speed: "Speed", rc_stamina: "Stream",
    ln_general: "LN Gen", ln_tech: "LN Tech", ln_inverse: "LN Inv", ln_release: "LN Rel",
  };
  const SKILL_ZH = {
    rc_jack: "叠键", rc_tech: "技巧", rc_speed: "速度", rc_stamina: "耐力切",
    ln_general: "LN 综合", ln_tech: "LN 技巧", ln_inverse: "反键", ln_release: "放手",
  };
  const STATUS_ZH = { ranked: "Ranked", approved: "Approved", qualified: "Qualified", loved: "Loved", pending: "Pending", wip: "WIP", graveyard: "Graveyard", unknown: "未知" };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  const hue = (id) => Math.abs(id * 47) % 360;

  function starPill(sr, kind) {
    if (sr == null) return `<span class="star na" title="${kind === "o" ? "暂无官方星级" : ""}"><span class="k">${kind === "o" ? "官" : "引"}</span>—</span>`;
    return `<span class="star" style="background:${starColour(sr)};color:${starText(sr)}"><span class="k">${kind === "o" ? "官" : "引"}</span>${fmt(sr)}</span>`;
  }

  function cover(el, setId, size) {
    if (setId > 0) {
      const url = `https://assets.ppy.sh/beatmaps/${setId}/covers/${size}`;
      const img = new Image();
      img.onload = () => { el.style.backgroundImage = `url("${url}")`; el.classList.remove("none"); };
      img.src = url;
    }
    el.style.setProperty("--h", hue(setId));
  }

  async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  }

  async function navMeta() {
    try {
      const s = await getJSON("/api/stats");
      document.getElementById("meta").textContent =
        `${s.beatmapsets} 组 · ${s.beatmaps} 个难度 · 引擎 ${s.engine_version}` + (s.stale ? ` · ${s.stale} 个待重算` : "");
    } catch (e) { /* the page works without it */ }
  }

  // ---- listing ------------------------------------------------------------------------------------

  const SORTS = [
    ["newest", "最新"], ["official", "官方 SR"], ["engine", "引擎 SR"], ["delta", "偏差"],
    ["title", "标题"], ["artist", "艺术家"], ["bpm", "BPM"], ["length", "长度"], ["ranked", "上架日期"],
  ];
  const STATUSES = ["any", "ranked", "loved", "qualified", "pending", "graveyard", "unknown"];
  const DIFF_ROWS = 5;

  function readState() {
    const p = new URLSearchParams(location.search);
    return { q: p.get("q") || "", status: p.get("status") || "any", skill: p.get("skill") || "any",
             sort: p.get("sort") || "newest_desc", page: parseInt(p.get("page") || "1", 10) };
  }
  function writeState(st, push) {
    const p = new URLSearchParams();
    if (st.q) p.set("q", st.q);
    if (st.status !== "any") p.set("status", st.status);
    if (st.skill !== "any") p.set("skill", st.skill);
    if (st.sort !== "newest_desc") p.set("sort", st.sort);
    if (st.page > 1) p.set("page", st.page);
    const url = `${location.pathname}${p.toString() ? "?" + p : ""}`;
    (push ? history.pushState : history.replaceState).call(history, null, "", url);
  }

  function pills(node, items, current, onPick) {
    node.innerHTML = items.map(([v, label]) => `<button class="pill${v === current ? " active" : ""}" data-v="${esc(v)}">${label}</button>`).join("");
    node.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => onPick(b.dataset.v)));
  }

  function card(set) {
    const maps = set.beatmaps.slice().sort((a, b) => (b.matched - a.matched) || ((a.official_sr ?? a.engine_sr) - (b.official_sr ?? b.engine_sr)));
    const shown = maps.slice(0, DIFF_ROWS).sort((a, b) => (a.official_sr ?? a.engine_sr) - (b.official_sr ?? b.engine_sr));
    const rows = shown.map((b) => `
      <a class="name${b.matched ? " match" : ""}" href="/beatmapsets/${set.id}#${b.id}" title="${esc(b.version)}">${esc(b.version)}</a>
      ${starPill(b.official_sr, "o")}${starPill(b.engine_sr, "e")}
      <span class="dan" title="主技能：${esc(SKILL_ZH[b.dominant_skill] || b.dominant_skill)}">${esc(b.dan)} · ${esc(SKILL_LABELS[b.dominant_skill] || "")}</span>`).join("");
    const rest = set.beatmaps.length - shown.length;
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <a class="cover none" href="/beatmapsets/${set.id}"><span class="keys">7K</span></a>
      <div class="body">
        <a class="title" href="/beatmapsets/${set.id}" title="${esc(set.title_unicode)}">${esc(set.title_unicode || set.title)}</a>
        <div class="artist">${esc(set.artist_unicode || set.artist)}</div>
        <div class="mapper"><span>谱师 <b>${esc(set.creator)}</b></span><span class="status ${esc(set.status)}">${esc(STATUS_ZH[set.status] || set.status)}</span></div>
        <div class="diffs"><span class="head">难度</span><span class="head">官方</span><span class="head">引擎</span><span class="head" style="text-align:right">段位 · 技能</span>${rows}</div>
        ${rest > 0 ? `<a class="more" href="/beatmapsets/${set.id}">还有 ${rest} 个难度 →</a>` : ""}
      </div>`;
    cover(el.querySelector(".cover"), set.id, "list@2x.jpg");
    return el;
  }

  function pager(node, page, pages, go) {
    if (pages <= 1) { node.innerHTML = ""; return; }
    const nums = new Set([1, pages, page - 2, page - 1, page, page + 1, page + 2].filter((n) => n >= 1 && n <= pages));
    let html = `<button data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹</button>`;
    let last = 0;
    for (const n of [...nums].sort((a, b) => a - b)) {
      if (n - last > 1) html += `<button disabled>…</button>`;
      html += `<button data-p="${n}" class="${n === page ? "cur" : ""}">${n}</button>`;
      last = n;
    }
    html += `<button data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>›</button>`;
    node.innerHTML = html;
    node.querySelectorAll("button[data-p]:not([disabled])").forEach((b) => b.addEventListener("click", () => go(parseInt(b.dataset.p, 10))));
  }

  function searchPage() {
    navMeta();
    let st = readState();
    const input = document.getElementById("q");
    const grid = document.getElementById("grid");
    let seq = 0;

    async function load(push) {
      writeState(st, push);
      input.value = st.q;
      pills(document.getElementById("status"), STATUSES.map((s) => [s, s === "any" ? "全部" : STATUS_ZH[s]]), st.status, (v) => { st.status = v; st.page = 1; load(true); });
      pills(document.getElementById("skill"), [["any", "全部"], ...Object.keys(SKILL_LABELS).map((k) => [k, `${SKILL_ZH[k]}`])], st.skill, (v) => { st.skill = v; st.page = 1; load(true); });
      const [sortKey, sortDir] = st.sort.split(/_(?=asc$|desc$)/);
      const sortNode = document.getElementById("sort");
      sortNode.innerHTML = SORTS.map(([k, label]) => `<button class="pill${k === sortKey ? " active" : ""}" data-v="${k}">${label}${k === sortKey ? `<span class="dir">${sortDir === "desc" ? "▼" : "▲"}</span>` : ""}</button>`).join("");
      sortNode.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        const k = b.dataset.v;
        const dir = k === sortKey ? (sortDir === "desc" ? "asc" : "desc") : (["title", "artist"].includes(k) ? "asc" : "desc");
        st.sort = `${k}_${dir}`; st.page = 1; load(true);
      }));

      const mine = ++seq;
      const p = new URLSearchParams({ q: st.q, status: st.status, skill: st.skill, sort: st.sort, page: st.page });
      let data;
      try { data = await getJSON(`/api/beatmapsets/search?${p}`); } catch (e) { grid.innerHTML = `<div class="empty">搜索失败（${esc(e.message)}）</div>`; return; }
      if (mine !== seq) return;
      document.getElementById("chips").innerHTML = data.filters.map((f) => `<span class="chip">${esc(f)}</span>`).join("");
      document.getElementById("count").textContent = `${data.total} 组谱面`;
      grid.innerHTML = "";
      if (!data.beatmapsets.length) grid.innerHTML = `<div class="empty">没有符合条件的谱面</div>`;
      data.beatmapsets.forEach((s) => grid.appendChild(card(s)));
      pager(document.getElementById("pager"), data.page, data.pages, (n) => { st.page = n; load(true); window.scrollTo({ top: 0, behavior: "smooth" }); });
    }

    let timer = null;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { st.q = input.value.trim(); st.page = 1; load(false); }, 300); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); st.q = input.value.trim(); st.page = 1; load(true); } });
    document.getElementById("help").addEventListener("click", () => document.getElementById("syntax").classList.toggle("open"));
    window.addEventListener("popstate", () => { st = readState(); load(false); });
    load(false);
  }

  // ---- beatmapset page ----------------------------------------------------------------------------

  function densitySvg(bins, colour) {
    const max = Math.max(1, ...bins), n = bins.length, w = 1000, h = 100;
    const pts = bins.map((v, i) => `${(i / (n - 1)) * w},${h - (v / max) * (h - 4)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polygon points="0,${h} ${pts} ${w},${h}" fill="${colour}" fill-opacity=".35" stroke="${colour}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function setPage() {
    navMeta();
    const setId = parseInt(location.pathname.split("/").filter(Boolean)[1], 10);
    getJSON(`/api/beatmapsets/${setId}`).then((set) => {
      document.title = `${set.artist} - ${set.title} · proj7k 7K 谱面库`;
      const header = document.getElementById("header");
      cover(header, set.id, "cover@2x.jpg");
      const pick = (id) => render(set, set.beatmaps.find((b) => String(b.id) === String(id)) || set.beatmaps[set.beatmaps.length - 1]);
      window.addEventListener("hashchange", () => pick(location.hash.slice(1)));
      pick(location.hash.slice(1));
    }).catch(() => { document.getElementById("main").innerHTML = `<div class="empty">找不到这组谱面</div>`; });
  }

  function render(set, cur) {
    const picker = document.getElementById("picker");
    picker.innerHTML = set.beatmaps.map((b) => `<button class="${b.id === cur.id ? "cur" : ""}" data-id="${b.id}" title="${esc(b.version)} · 官方 ${fmt(b.official_sr)} / 引擎 ${fmt(b.engine_sr)}"><span class="dot" style="background:${starColour(b.official_sr ?? b.engine_sr)}"></span></button>`).join("");
    picker.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { history.replaceState(null, "", `#${b.dataset.id}`); render(set, set.beatmaps.find((x) => String(x.id) === b.dataset.id)); }));
    document.getElementById("diff-name").innerHTML = `${esc(cur.version)} <small>谱师 ${esc(set.creator)}</small>`;
    document.getElementById("t").textContent = set.title_unicode || set.title;
    document.getElementById("a").textContent = set.artist_unicode || set.artist;
    document.getElementById("m").innerHTML = `<span class="status ${esc(set.status)}">${esc(STATUS_ZH[set.status] || set.status)}</span>${set.ranked_date ? `<span>${esc(set.ranked_date.slice(0, 10))}</span>` : ""}`;
    document.getElementById("actions").innerHTML = cur.id > 0
      ? `<a class="btn primary" href="https://osu.ppy.sh/beatmapsets/${set.id}#mania/${cur.id}" target="_blank" rel="noopener">在 osu! 官网打开</a><a class="btn" href="osu://b/${cur.id}">osu!direct</a>`
      : `<span class="btn" title="这张谱没有 osu! 的谱面 id（未上传）">本地谱面</span>`;

    const delta = cur.official_sr == null ? null : cur.engine_sr - cur.official_sr;
    document.getElementById("sr").innerHTML = `
      <div class="sr-compare">
        <div class="sr-box o"><div class="lbl">官方 SR</div><div class="num">${fmt(cur.official_sr)}</div><div class="sub">${cur.official_sr_source ? `来源：${esc({ "osu-api": "osu! API", manifest: "标杆清单", lazer: "osu!lazer 曲库" }[cur.official_sr_source] || cur.official_sr_source)}` : "暂无"}</div></div>
        <div class="sr-box e"><div class="lbl">引擎 SR</div><div class="num">${fmt(cur.engine_sr)}</div><div class="sub">段位 ${esc(cur.dan)}${delta == null ? "" : ` · 偏差 <span class="delta ${delta >= 0 ? "pos" : "neg"}">${delta >= 0 ? "+" : ""}${fmt(delta)}</span>`}</div></div>
      </div>
      <div class="stats">
        <div><span>长度</span><b>${mmss(cur.length_s)}</b></div>
        <div><span>BPM</span><b>${fmt(cur.bpm, 0)}</b></div>
        <div><span>物件</span><b>${cur.note_count}</b></div>
        <div><span>长条</span><b>${cur.ln_count} <small>(${fmt((100 * cur.ln_count) / Math.max(1, cur.note_count), 0)}%)</small></b></div>
        <div><span>OD</span><b>${fmt(cur.od, 1)}</b></div>
        <div><span>HP</span><b>${fmt(cur.hp, 1)}</b></div>
      </div>`;

    const skills = Object.entries(cur.skills);
    const top = Math.max(...skills.map(([, s]) => s.stars), 1);
    document.getElementById("skills").innerHTML = skills.map(([k, s]) => `
      <span class="n${k === cur.dominant_skill ? " dom" : ""}" title="${esc(SKILL_ZH[k])}">${esc(SKILL_LABELS[k])}</span>
      <div class="track"><div class="fill" style="width:${(100 * s.stars) / top}%;background:${k === cur.dominant_skill ? "var(--accent)" : "var(--engine)"}"></div></div>
      <span class="v">${fmt(s.stars)}</span><span class="p" title="主导度">${fmt(100 * s.dominance, 0)}%</span>`).join("");
    document.getElementById("density").innerHTML = densitySvg(cur.density, "var(--engine)");

    const table = document.getElementById("all");
    table.innerHTML = `<tr><th>难度</th><th class="num">官方</th><th class="num">引擎</th><th class="num">偏差</th><th>段位</th><th>主技能</th></tr>` +
      set.beatmaps.map((b) => {
        const d = b.official_sr == null ? null : b.engine_sr - b.official_sr;
        return `<tr data-id="${b.id}" class="${b.id === cur.id ? "cur" : ""}"><td>${esc(b.version)}</td><td class="num">${starPill(b.official_sr, "o")}</td><td class="num">${starPill(b.engine_sr, "e")}</td>
          <td class="num delta ${d == null ? "" : d >= 0 ? "pos" : "neg"}">${d == null ? "—" : (d >= 0 ? "+" : "") + fmt(d)}</td><td>${esc(b.dan)}</td><td>${esc(SKILL_ZH[b.dominant_skill] || b.dominant_skill)}</td></tr>`;
      }).join("");
    table.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => { history.replaceState(null, "", `#${tr.dataset.id}`); render(set, set.beatmaps.find((x) => String(x.id) === tr.dataset.id)); window.scrollTo({ top: 0, behavior: "smooth" }); }));

    document.getElementById("info").innerHTML = `
      <span>来源</span><b>${esc(set.source) || "—"}</b>
      <span>标签</span><b>${esc(set.tags) || "—"}</b>
      <span>谱面 id</span><b>${cur.id > 0 ? cur.id : "—（本地）"}</b>
      <span>MD5</span><b style="font-family:var(--mono)">${esc(cur.checksum)}</b>
      <span>引擎版本</span><b style="font-family:var(--mono)">${esc(cur.engine_version)}</b>`;
  }

  return { searchPage, setPage, starColour };
})();
