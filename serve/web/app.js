// serve/web/app.js - the Strata web app: Chat, Monitor, About. No framework, no network beyond this server.
// The Monitor tab rebuilds PR #22's dashboard idea (code-martin) on the server's own /metrics.
"use strict";

const $ = (id) => document.getElementById(id);
const SPRITE = "web/sprite.svg";
const icon = (name, cls = "st-icon") => `<svg class="${cls}" aria-hidden="true"><use href="${SPRITE}#i-${name}"/></svg>`;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString(undefined, {maximumFractionDigits: d, minimumFractionDigits: d}));
const kfmt = (n) => (n == null ? "–" : n >= 1000 ? `${fmt(n / 1000, n >= 10000 ? 0 : 1)}k` : fmt(n));
// a context size: 32768 -> "32K" (powers of two), else like kfmt
const ctxfmt = (n) => (n && n % 1024 === 0 ? `${fmt(n / 1024)}K` : kfmt(n));
const gb = (b, d = 1) => (b == null ? "–" : fmt(b / 1073741824, d));   // memory: binary GB, as Windows shows it

const store = {
  get(k, d) { try { const v = localStorage.getItem("strata." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("strata." + k, JSON.stringify(v)); } catch (e) { /* private mode: in memory only */ } },
};

// ------------------------------------------------------------------ toasts
function toast(kind, title, text = "", ms = 3500, action = null) {
  const names = {info: "info", success: "check", warn: "warning", error: "error"};
  const el = document.createElement("div");
  el.className = `st-toast st-toast--${kind}`;
  el.innerHTML = `${icon(names[kind] || "info")}<div><div class="st-toast__title"></div><div class="t-text"></div></div>`;
  el.querySelector(".st-toast__title").textContent = title;
  el.querySelector(".t-text").textContent = text;
  if (action) {
    const b = document.createElement("button");
    b.className = "st-btn st-btn--secondary";
    b.style.height = "32px";
    b.style.marginLeft = "auto";
    b.textContent = action.label;
    b.onclick = () => { action.run(); el.remove(); };
    el.appendChild(b);
  }
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {                                   // http on another host: no async clipboard
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  if (btn) {
    const use = btn.querySelector("use");
    use.setAttribute("href", `${SPRITE}#i-check`);
    setTimeout(() => use.setAttribute("href", `${SPRITE}#i-copy`), 1500);
  }
  toast("success", "已复制到剪贴板", "", 1800);
}

// ------------------------------------------------------------------ theme and tabs
// the system's theme until the user picks one (only a click is saved)
function setTheme(t, save) {
  document.documentElement.dataset.theme = t;
  if (save) try { localStorage.setItem("strata.theme", t); } catch (e) { /* ignore */ }
  $("theme-icon").setAttribute("href", `${SPRITE}#i-${t === "dark" ? "sun" : "moon"}`);
  $("dark-toggle").setAttribute("aria-checked", String(t === "dark"));
}
const flipTheme = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
$("theme-btn").onclick = flipTheme;
$("dark-toggle").onclick = flipTheme;
setTheme(document.documentElement.dataset.theme || "light", false);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  let saved = null;
  try { saved = localStorage.getItem("strata.theme"); } catch (err) { /* ignore */ }
  if (!saved) setTheme(e.matches ? "dark" : "light", false);
});

let tab = "chat";
function showTab(name) {
  tab = ["chat", "images", "monitor", "about"].includes(name) ? name : "chat";
  for (const b of document.querySelectorAll(".st-tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  for (const v of ["chat", "images", "monitor", "about"]) $(`view-${v}`).hidden = v !== tab;
  if (tab === "images") $("img-prompt").focus();
  if (location.hash.slice(1) !== tab) history.replaceState(null, "", tab === "chat" ? location.pathname : `#${tab}`);
  if (tab === "chat") $("input").focus();
  if (tab === "monitor") loadMcp();
  if (tab === "about") loadConfig();
  if (lastMetrics) render(lastMetrics);
}
for (const b of document.querySelectorAll(".st-tab")) b.onclick = () => showTab(b.dataset.tab);
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

// ------------------------------------------------------------------ server access
function headers(json = false) {
  const h = {};
  const key = store.get("apikey", "");
  if (key) h.Authorization = "Bearer " + key;
  if (json) h["Content-Type"] = "application/json";
  return h;
}
$("api-key").value = store.get("apikey", "");
$("api-key").onchange = () => { store.set("apikey", $("api-key").value.trim()); toast("success", "API 密钥已保存", "仅保存在当前浏览器。"); };

let health = {model: "strata", images: false, max_context: 0};
async function loadHealth() {
  try {
    health = await (await fetch("health")).json();
    $("attach-btn").title = health.images ? "添加文本文件或图片，也可拖入文件"
                                          : "添加文本文件，也可拖入文件";
    $("chat-empty-sub").textContent = `${health.model} 在本机运行，对话不会发送到外部服务。`;
    $("tab-btn-images").hidden = !health.image_generation;
    if (location.hash === "#images" && health.image_generation) showTab("images");
  } catch (e) {
    setTimeout(loadHealth, 2000);
  }
}

// ------------------------------------------------------------------ Monitor
const METRICS = [
  {key: "speed", label: "生成速度", icon: "gauge", unit: "t/s", series: "tok_s"},
  {key: "gpu", label: "GPU 使用率", icon: "gpu", unit: "%", series: "gpu_util", max: 100},
  {key: "vram", label: "显存", icon: "layers", unit: "GB", series: "gpu_mem_used"},
  {key: "temp", label: "GPU 温度", icon: "thermometer", unit: "°C", series: "gpu_temp", tone: "warn"},
  {key: "power", label: "功耗", icon: "bolt", unit: "W", series: "gpu_power"},
  {key: "pcie", label: "PCIe", icon: "link", unit: "", series: "gpu_pcie_rx_mb", tone: "info"},
  {key: "cpu", label: "CPU", icon: "cpu", unit: "%", series: "cpu", max: 100},
  {key: "disk", label: "磁盘读取", icon: "disk", unit: "MB/s", series: "disk_read_mb", tone: "info"},
];
$("metrics").innerHTML = METRICS.map((m) => `
  <div class="st-card metric-card"><div class="st-metric">
    <span class="st-metric__label">${icon(m.icon, "st-icon st-icon--sm")}${esc(m.label)}</span>
    ${m.key === "speed" ? `<div class="speed-values">
      <div><span class="st-metric__value" id="mv-speed">-</span><span class="st-metric__sub" id="ms-speed">解码</span></div>
      <div class="speed-prefill"><span class="st-metric__value" id="mv-prefill">-</span><span class="st-metric__sub" id="ms-prefill">预填充</span></div>
    </div>` : `<span class="st-metric__value" id="mv-${m.key}">–</span>
    <span class="st-metric__sub" id="ms-${m.key}"></span>`}
    <svg class="st-metric__spark" id="sp-${m.key}" viewBox="0 0 100 32" preserveAspectRatio="none"${m.tone ? ` data-tone="${m.tone}"` : ""}>
      <path class="area" fill="currentColor" opacity=".12"/><path class="line" fill="none" stroke="currentColor"
      stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
      ${m.key === "speed" ? `<g id="sp-prefill" class="speed-prefill"><path class="area" fill="currentColor" opacity=".12"/>
        <path class="line" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"
        stroke-linecap="round" vector-effect="non-scaling-stroke"/></g>` : ""}</svg>
  </div></div>`).join("");

function spark(id, values, max) {
  const svg = $(id);
  const v = (values || []).map((x) => (x == null ? 0 : x));
  if (v.length < 2) { svg.querySelector(".line").setAttribute("d", ""); svg.querySelector(".area").setAttribute("d", ""); return; }
  const top = Math.max(max || 0, ...v, 1);
  const pts = v.map((x, i) => [(i / (v.length - 1)) * 100, 30 - (x / top) * 26]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join("");
  svg.querySelector(".line").setAttribute("d", line);
  svg.querySelector(".area").setAttribute("d", `${line}L100,32L0,32Z`);
}
function setMetric(key, value, unit, sub) {
  $(`mv-${key}`).innerHTML = value == null ? "–" : `${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ""}`;
  $(`ms-${key}`).textContent = sub || "";
}

let lastMetrics = null, metricsFailures = 0, keyWarned = false, mcpTick = 0;
let reqShowAll = false;   // the Monitor's request table: the last 12, or every one the server keeps (issue #35)
async function poll() {
  try {
    const r = await fetch(reqShowAll ? "metrics?requests=all" : "metrics", {headers: headers()});
    if (r.status === 401) {
      setPill("error", "需要 API 密钥");
      if (!keyWarned) { keyWarned = true; toast("warn", "需要 API 密钥", "请在“关于与设置 → 网页设置”中填写 API 密钥。", 6000); }
    } else if (r.ok) {
      lastMetrics = await r.json();
      metricsFailures = 0;
      render(lastMetrics);
    } else {
      throw new Error(`HTTP ${r.status}`);
    }
  } catch (e) {
    if (++metricsFailures === 3) setPill("error", "无法连接服务");
  }
  if (tab === "monitor" && ++mcpTick % 10 === 0) loadMcp();       // server states change rarely: every 10 s
  setTimeout(poll, 1000);
}

function setPill(state, text) {
  $("pill").dataset.state = state === "error" ? "queued" : state;
  $("pill-text").textContent = text;
}

function render(m) {
  const live = m.live || {}, hw = m.hardware || {}, st = m.hardware_static || {}, eng = m.engine || {}, h = m.history || {};
  const last = (m.requests || [])[0];
  // the header pill
  if (live.state === "reading") {
    const pct = live.prompt_total ? Math.round((100 * live.prompt_read) / live.prompt_total) : null;
    setPill("reading", pct != null ? `预填充 · ${pct}%` : "正在预填充");
  } else if (live.state === "generating") {
    setPill("generating", `生成中 · ${fmt(live.tok_s, 1)} token/s`);
  } else {
    setPill("idle", "空闲");
  }
  if (live.queued > 0) setPill("queued", `${live.queued} 条排队`);
  if (tab === "monitor") renderMonitor(live, hw, st, eng, h, last, m.requests || [], m.totals, m.requests_kept);
  if (tab === "monitor") renderConvCache(m.conversation_cache);
  if (tab === "about") renderAbout(eng, hw, st);
  renderVramCard(m);
}

// #596: the conversation cache - the prompt's state the engine keeps between requests (always), and the whole
// conversations it parks in RAM when "--conversation-cache-mib N" is in the run config's args (opt-in)
function since(t) {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 60 ? "刚刚" : s < 3600 ? `${fmt(s / 60)} 分钟前` : `${fmt(s / 3600, 1)} 小时前`;
}
function renderConvCache(c) {
  $("cc-card").hidden = !c;
  if (!c) return;                                  // an older server
  const pct = (a, b) => (b ? `${Math.min(100, (100 * a) / b)}%` : "0%");
  $("cc-bars").hidden = !c.enabled;
  if (c.enabled) {
    $("cc-slots-text").textContent = `${fmt(c.parked)} / ${fmt(c.slots)}`;
    $("cc-slots-bar").style.width = pct(c.parked, c.slots);
    const budget = c.budget_mib * 1048576;
    $("cc-mem-text").textContent = `${gb(c.bytes)} / ${gb(budget)} GB`;
    $("cc-mem-bar").style.width = pct(c.bytes, budget);
  }
  $("cc-sum").textContent = c.requests ? `${fmt(c.requests)} 条请求中有 ${fmt(c.requests_reused)} 条复用了前缀` : "";
  const share = c.prompt_tokens ? `（占输入 token 的 ${fmt((100 * c.reused_tokens) / c.prompt_tokens)}%）` : "";
  const event = c.last_event ? `${c.last_event === "parked" ? "已保存" : "已恢复"} ${fmt(c.last_tokens)} token，${since(c.last_at)}` : null;
  facts($("cc-facts"), [
    ["上次请求", c.last_prompt != null ? `${fmt(c.last_prompt)} 个输入 token 中复用 ${fmt(c.last_reused || 0)} 个` : null],
    ["启动以来复用", c.requests ? `${fmt(c.reused_tokens)} tokens${share}` : null],
    ["保存／恢复次数", c.enabled ? `${fmt(c.parks)} / ${fmt(c.restores)}${c.evictions ? ` · 淘汰 ${fmt(c.evictions)} 次` : ""}` : null],
    ["最近切换", c.enabled ? event : null],
  ]);
  $("cc-note").textContent = c.enabled
    ? "继续已缓存的对话时恢复其状态，避免全量预填充；缓存数量或内存预算不足时淘汰旧状态。缓存只保留在 RAM 中，服务退出后清空。"
    : "引擎保留最近一次对话；若需切换多个会话，请在“关于与设置 → 并行与对话缓存”中设置 RAM 缓存预算。";
}

// ------------------------------------------------------------------ 显存模式浮窗（#533 的界面）
const VF = {fast: store.get("vram_fast_mib", 4096), mid: store.get("vram_mid_mib", 32768), zero: store.get("vram_zero_mib", 45056), last: null, restore: null, busy: false};
$("vf-fast-mib").value = VF.fast;
$("vf-mid-mib").value = VF.mid;
$("vf-fast-mib").onchange = () => { VF.fast = Math.max(0, parseInt($("vf-fast-mib").value || "0", 10));
  $("vf-fast-mib").value = VF.fast; store.set("vram_fast_mib", VF.fast); };
$("vf-mid-mib").onchange = () => { VF.mid = Math.max(0, parseInt($("vf-mid-mib").value || "0", 10));
  $("vf-mid-mib").value = VF.mid; store.set("vram_mid_mib", VF.mid); };
$("vf-zero-mib").value = VF.zero;
$("vf-zero-mib").onchange = () => { VF.zero = Math.max(0, parseInt($("vf-zero-mib").value || "0", 10));
  $("vf-zero-mib").value = VF.zero; store.set("vram_zero_mib", VF.zero); };
async function vfPost(path, body) {
  if (VF.busy) return;
  VF.busy = true; $("vram-float").querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    const r = await fetch(path, {method: "POST", headers: headers(true), body: JSON.stringify(body || {})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) toast("error", "操作失败", (j.error && j.error.message) || `HTTP ${r.status}`, 6000);
    else if (j.status === "busy") toast("warn", "模型正忙", "等当前请求结束后再试。");
    else if (j.status === "not loaded") toast("info", path.startsWith("unload") ? "已卸载" : "已记录", path.startsWith("unload") ? "权重已释放，对话继续保留。" : "这条预留会在下次加载时生效。");
    else toast("success", path === "unload" ? (j.session_kept ? "已卸载（对话已存内存）" : "已卸载")
               : path.startsWith("unload") ? "已完全卸载" : "显存已调整",
               j.vram_free_mib != null ? `现在空闲 ${fmt(j.vram_free_mib / 1024, 1)} GB` : (j.note || ""));
  } catch (e) { toast("error", "无法连接服务", String(e)); }
  VF.busy = false;
  $("vram-float").querySelectorAll("button").forEach(b => b.disabled = false);
}
$("vf-fast").onclick = () => vfPost("v1/vram", {reserve_mib: VF.fast});
$("vf-mid").onclick = () => vfPost("v1/vram", {reserve_mib: VF.mid});
$("vf-zero").onclick = () => vfPost("v1/vram", {reserve_mib: VF.zero});
$("vf-zero-offload").onclick = () => {   // 记住当前档位（高速/中速/自定义），下一条请求恢复它；没记住就回高速
  VF.restore = VF.last != null && VF.last !== VF.zero ? VF.last : VF.fast;
  vfPost("v1/vram", {reserve_mib: VF.zero, auto_restore: VF.restore}); };
$("vf-unload").onclick = () => vfPost("unload");
$("vf-full").onclick = () => vfPost("unload?full=1");
function renderVramCard(m) {
  const show = tab === "monitor" || tab === "about";
  $("vram-float").hidden = !show;
  if (!show) return;
  const live = m.live || {}, eng = m.engine || {}, v = eng.vram || {};
  const unloaded = live.state === "unloaded";
  const r = v.reserve_mib;
  if (r != null && r !== VF.zero) VF.last = r;   // 零速卸载要知道"之前的档位"是什么
  let mode = "默认", on = "";
  if (unloaded) mode = eng.retained_ram?.weights_bytes > 0 ? "已卸载到内存" : "完全卸载";
  else if (r != null && r === VF.fast) { mode = "高速"; on = "vf-fast"; }
  else if (r != null && r === VF.mid) { mode = "中速"; on = "vf-mid"; }
  else if (r != null && r === VF.zero) { mode = v.auto_restore != null ? "已卸载（下一条对话自动恢复）" : "零速"; on = v.auto_restore != null ? "vf-zero-offload" : "vf-zero"; }
  else if (r != null) mode = `自定义 ${r} MiB`;
  $("vf-mode").textContent = mode;
  $("vf-fast").dataset.on = on === "vf-fast" ? "1" : "0";
  $("vf-mid").dataset.on = on === "vf-mid" ? "1" : "0";
  $("vf-zero").dataset.on = on === "vf-zero" ? "1" : "0";
  $("vf-zero-offload").dataset.on = on === "vf-zero-offload" ? "1" : "0";
  const gbOf = (mib) => mib != null ? `${fmt(mib / 1024, 1)} GB` : null;
  facts($("vf-facts"), [
    ["空闲显存", gbOf(v.vram_free_mib ?? eng.vram_free_mib)],
    ["专家缓存", v.expert_cache_mib != null ? `${gbOf(v.expert_cache_mib)} / ${gbOf(v.expert_cache_full_mib)}` : null],
    ["当前预留", r != null ? `${fmt(r)} MiB` : null],
    ["内存权重", eng.retained_ram ? `${fmt(eng.retained_ram.weights_bytes / 2 ** 30, 1)} GiB` : null],
    ["已存对话", eng.retained_ram?.session_bytes ? `${fmt(eng.retained_ram.session_bytes / 2 ** 20)} MiB` : null],
  ]);
  const elastic = v.elastic || eng.vram_elastic;
  $("vf-note").textContent = !elastic
    ? "热切换需要引擎以 vram_elastic 启动（改 run config 后重启一次）；未开启时只有卸载可用。"
    : (eng.retained_ram?.error || "卸载：权重和对话保留在内存；完全卸载：释放权重，保留对话。下次请求自动加载。");
}

function renderTotals(t) {
  if (!t || !t.requests) return "";
  const since = new Date(t.since * 1000).toLocaleString([], {weekday: "short", hour: "2-digit", minute: "2-digit"});
  const read = t.prompt_tokens - t.reused;
  const pSpeed = t.prompt_ms > 0 && read > 0 ? `，${fmt(read / (t.prompt_ms / 1000))} token/s` : "";
  const oSpeed = t.decode_ms > 0 && t.output_tokens > 0 ? `，${fmt(t.output_tokens / (t.decode_ms / 1000), 1)} token/s` : "";
  return `从 ${since} 起：${fmt(t.requests)} 条请求 · 预填充 ${fmt(read)} token${pSpeed}（复用 ${fmt(t.reused)} token） · ` +
         `生成 ${fmt(t.output_tokens)} token${oSpeed}`;
}
function renderMonitor(live, hw, st, eng, h, last, requests, totals, kept) {
  // model state
  const on = live.queued > 0 ? "queued" : live.state;
  for (const b of document.querySelectorAll("#state-badges .st-badge")) b.classList.toggle("on", b.dataset.s === on || b.dataset.s === live.state);
  const prog = $("state-progress");
  let label = "等待请求", detail = "", pct = 0;
  if (live.state === "reading") {
    label = "正在预填充";
    prog.dataset.tone = "info";
    if (live.prompt_total) {
      pct = (100 * live.prompt_read) / live.prompt_total;
      detail = `${fmt(live.prompt_read)} / ${fmt(live.prompt_total)} tokens · ${fmt(pct)}%`;
    } else {
      detail = `${fmt(live.prompt_tokens)} tokens`;
    }
  } else if (live.state === "generating") {
    label = ({thinking: "思考中", answering: "回答中", generating: "生成中", reading: "预填充"}[live.phase] || "生成中");
    delete prog.dataset.tone;
    pct = live.max_tokens ? Math.min(100, (100 * live.generated) / live.max_tokens) : 0;
    detail = `${fmt(live.generated)} tokens · ${fmt(live.tok_s, 1)} tok/s`;
  } else if (last) {
    delete prog.dataset.tone;
    detail = `上次：${fmt(last.output_tokens)} token${last.decode_tok_s ? `，${fmt(last.decode_tok_s, 1)} token/s` : ""}`;
  }
  $("state-label").textContent = label;
  $("state-detail").textContent = detail;
  $("state-bar").style.width = `${pct}%`;

  // the eight cards
  const speed = live.state === "generating" ? live.tok_s : last ? last.decode_tok_s : null;
  setMetric("speed", speed == null ? null : fmt(speed, 1), "t/s",
            live.state === "generating" ? "当前解码" : last ? "上次解码" : "解码");
  const prefill = live.state !== "idle" ? live.prefill_tok_s_mean
                : last && last.prompt_ms > 0 ? Math.max(0, last.prompt_tokens - (last.reused || 0)) / (last.prompt_ms / 1000) : null;
  setMetric("prefill", prefill == null ? null : fmt(prefill), "t/s",
            live.state === "reading" ? "当前预填充" : live.state === "generating" ? "本次预填充" : last ? "上次预填充" : "预填充");
  spark("sp-speed", h.tok_s);
  spark("sp-prefill", h.prefill_tok_s_mean);
  // a model split across several cards (issue #112): the cards show their total / mean / hottest, and each card's own
  const per = (f) => (hw.gpus || []).map((g) => `GPU ${g.index} ${f(g)}`).join(" · ");
  const multi = (hw.gpus || []).length > 1;
  setMetric("gpu", hw.gpu_util == null ? null : fmt(hw.gpu_util), "%",
            multi ? per((g) => (g.util == null ? "–" : `${fmt(g.util)}%`)) : st.gpu_name || (st.gpu_note ? "暂无数据" : ""));
  spark("sp-gpu", h.gpu_util, 100);
  setMetric("vram", hw.gpu_mem_used == null ? null : gb(hw.gpu_mem_used), hw.gpu_mem_total ? `/ ${gb(hw.gpu_mem_total, 0)} GB` : "GB",
            multi ? per((g) => (g.mem_used == null ? "–" : `${gb(g.mem_used)} GB`))
                  : eng.expert_slots ? `缓存 ${fmt(eng.expert_slots)} 个专家` : (st.gpu_note ? "Windows AMD 暂不支持读取" : ""));
  spark("sp-vram", h.gpu_mem_used, hw.gpu_mem_total);
  setMetric("temp", hw.gpu_temp == null ? null : fmt(hw.gpu_temp), "°C",
            multi ? per((g) => (g.temp == null ? "–" : `${fmt(g.temp)}°`)) : "");
  spark("sp-temp", h.gpu_temp, 90);
  setMetric("power", hw.gpu_power == null ? null : fmt(hw.gpu_power), "W", hw.gpu_power_limit ? `功耗上限 ${fmt(hw.gpu_power_limit)} W` : "");
  spark("sp-power", h.gpu_power, hw.gpu_power_limit);
  const gen = hw.gpu_pcie_gen_max || hw.gpu_pcie_gen;
  setMetric("pcie", gen ? `Gen${gen}` : null, hw.gpu_pcie_width ? `x${hw.gpu_pcie_width}` : "",
            hw.gpu_pcie_rx_mb == null ? "" : `GPU 接收 ${fmt(hw.gpu_pcie_rx_mb, hw.gpu_pcie_rx_mb < 10 ? 1 : 0)} MB/s` +
            (hw.gpu_pcie_gen && gen && hw.gpu_pcie_gen < gen ? ` · 当前 Gen${hw.gpu_pcie_gen}` : ""));
  spark("sp-pcie", h.gpu_pcie_rx_mb);
  setMetric("cpu", hw.cpu == null ? null : fmt(hw.cpu), "%", st.threads ? `${st.cores ? `${st.cores} 核 · ` : ""}${st.threads} 线程` : "");
  spark("sp-cpu", h.cpu, 100);
  if (hw.disk_read_mb == null) {
    setMetric("disk", null, "", st.psutil ? "" : "需要 psutil（安装程序会安装）");
  } else {
    const big = hw.disk_read_mb >= 1000;
    setMetric("disk", big ? fmt(hw.disk_read_mb / 1024, 2) : fmt(hw.disk_read_mb, hw.disk_read_mb < 10 ? 1 : 0), big ? "GB/s" : "MB/s",
              hw.disk_write_mb == null ? "" : `写入 ${fmt(hw.disk_write_mb, 1)} MB/s`);
  }
  spark("sp-disk", h.disk_read_mb);

  // context fill: the running request, else the last one
  const ctx = eng.max_context || 0;
  let used = 0;
  if (live.state !== "idle") used = (live.prompt_tokens || 0) + (live.generated || 0);
  else if (last) used = (last.prompt_tokens || 0) + (last.output_tokens || 0);
  const frac = ctx ? Math.min(1, used / ctx) : 0;
  $("ctx-fill").setAttribute("stroke-dasharray", `${(235.6 * frac).toFixed(1)} 314.2`);
  $("ctx-fill").style.opacity = 235.6 * frac >= 3 ? "1" : "0";         // a near-zero arc would draw just its round cap
  $("ctx-pct").textContent = `${Math.round(frac * 100)}%`;
  $("ctx-sub").textContent = ctx ? `${kfmt(used)} / ${ctxfmt(ctx)}` : "–";
  const cacheBytes = (eng.expert_cache_mib || 0) * 1048576;
  $("slots-text").textContent = eng.expert_slots ? `${fmt(eng.expert_slots)} · ${gb(cacheBytes)} GB` : "–";
  $("slots-bar").style.width = hw.gpu_mem_total ? `${Math.min(100, (100 * cacheBytes) / hw.gpu_mem_total)}%` : "0%";
  $("ram-text").textContent = hw.ram_total ? `${gb(hw.ram_used)} / ${gb(hw.ram_total, 0)} GB` : "–";
  const ramPct = hw.ram_total ? (100 * hw.ram_used) / hw.ram_total : 0;
  $("ram-bar").style.width = `${ramPct}%`;
  if (ramPct > 92) $("ram-progress").dataset.tone = "danger"; else delete $("ram-progress").dataset.tone;
  $("temp-text").textContent = hw.gpu_temp == null ? "–" : `${fmt(hw.gpu_temp)} °C`;
  $("temp-bar").style.width = hw.gpu_temp == null ? "0%" : `${Math.min(100, hw.gpu_temp)}%`;

  // recent requests
  const body = $("req-body");
  if (!requests.length) {
    body.innerHTML = `<tr><td colspan="9" class="muted">暂无请求</td></tr>`;
  } else {
    const badge = {stop: ["", "完成"], length: ["", "达到输出上限"], cancel: ["st-badge--queued", "已停止"],
                   disconnect: ["st-badge--queued", "连接关闭"], error: ["st-badge--error", "错误"]};
    body.innerHTML = requests.slice(0, reqShowAll ? requests.length : 12).map((r) => {
      const [cls, text] = badge[r.finish] || ["", r.finish || "–"];
      const t = new Date(r.time * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
      const proj = r.projection == null ? "" : ` <span class="st-badge${r.projection ? " st-badge--reading" : ""}" title="实验性速度投影：${r.projection ? "开启" : "关闭"}">${r.projection ? "ESP" : "原模型"}</span>`;
      // #588: the VRAM share; the PCIe share (--pcie-frac) beside it when there is one
      const hit = r.hit_rate == null ? "–" : `${(r.hit_rate * 100).toFixed(1)}%` +
        (r.pcie_share ? ` <span class="muted" title="由 GPU 经 PCIe 读取或由其他 GPU 计算的路由专家">+${(r.pcie_share * 100).toFixed(1)}% PCIe</span>` : "");
      // Reused tokens are not prefetched again. Match the Monitor's existing Prefill metric.
      const fresh = r.prompt_tokens == null ? null : Math.max(0, r.prompt_tokens - (r.reused || 0));
      const prefillRate = fresh > 0 && r.prompt_ms > 0 ? fresh / (r.prompt_ms / 1000) : null;
      return `<tr><td>${esc(t)}</td><td><span class="st-badge ${cls}">${esc(text)}</span>${proj}</td><td class="num">${fmt(r.prompt_tokens)}</td>
        <td class="num">${fmt(r.reused)}</td><td class="num">${fmt(r.output_tokens)}</td><td class="num">${fmt(prefillRate)}</td><td class="num">${fmt(r.decode_tok_s, 1)}</td>
        <td class="num">${hit}</td><td class="num">${fmt(r.duration_s, 1)} s</td></tr>`;
    }).join("");
  }
  const all = $("req-all");
  kept = kept == null ? requests.length : kept;
  all.hidden = kept <= 12;
  all.textContent = reqShowAll ? "收起列表" : `显示全部（${kept}）`;
  $("req-wrap").classList.toggle("all", reqShowAll);
  $("req-totals").textContent = renderTotals(totals);
}

function facts(el, rows) {
  el.innerHTML = rows.filter((r) => r[1] != null && r[1] !== "").map(([k, v, copy]) =>
    `<dt>${esc(k)}</dt><dd>${copy ? `<code>${esc(v)}</code><button class="st-btn st-btn--icon" data-copy="${esc(v)}" aria-label="复制">${icon("copy")}</button>` : esc(v)}</dd>`).join("");
}
// INFO cvec=project:4-44[:singleL] | add:A-B | 0
function projectionText(c) {
  if (!c || c === "0" || c === 0) return null;
  const [mode, range, single] = String(c).split(":");
  const [a, b] = (range || "").split("-");
  return `${mode === "project" ? "投影" : "加法"}控制向量，层 ${a}～${b}` +
         `${single ? `（使用第 ${single.replace("single", "")} 层方向）` : ""}。可在“采样与思考”中按对话切换。模型包` +
         "将其描述为拒绝方向投影；实际速度需测量确认。";
}
function renderAbout(eng, hw, st) {
  const kv = {int8: "8 位", q4_0: "4 位（Hadamard 旋转）", fp16: "16 位"}[eng.kv] || eng.kv;
  facts($("facts-engine"), [
    ["模型", eng.model],
    ["引擎", eng.version ? `v${eng.version}` : "本地编译"],
    ["上下文容量", eng.max_context ? `${fmt(eng.max_context)} tokens` : null],
    ["KV 缓存", kv ? `${kv}${eng.kv_resident ? `；每层 ${fmt(eng.kv_resident)} 个位置驻留显存，其余保存在 RAM` : "；全部驻留显存"}` : null],
    ["显存中的专家", eng.expert_slots ? `${fmt(eng.expert_slots)} (${gb((eng.expert_cache_mib || 0) * 1048576)} GB)` : null],
    ["投机解码", eng.spec ? `MTP 最多 ${Math.max(0, (eng.mtp_max || eng.spec) - 1)} 个草稿 token${eng.lookup ? "；已启用提示词查找" : ""}` : null],
    ["图片识别", eng.images ? "开启" : "关闭"],
    ["实验性速度投影", projectionText(eng.cvec)],
  ]);
  facts($("facts-hw"), [
    ["GPU", st.gpu_name ? `${st.gpu_name}${hw.gpu_mem_total ? `, ${gb(hw.gpu_mem_total, 0)} GB` : ""}` : (st.gpu_note || "无法读取 NVML")],
    ["CPU", st.cpu_name ? `${st.cpu_name}${st.threads ? `, ${st.threads} 线程` : ""}` : null],
    ["系统内存", hw.ram_total ? `${gb(hw.ram_total, 0)} GB` : null],
  ]);
  const base = location.origin;
  facts($("facts-api"), [
    ["OpenAI API 地址", `${base}/v1`, true],
    ["Anthropic API 地址", base, true],
    ["模型名称", eng.model, true],
  ]);
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (b) copyText(b.dataset.copy, b);
});
$("req-all").addEventListener("click", () => { reqShowAll = !reqShowAll; if (lastMetrics) render(lastMetrics); });

// ------------------------------------------------------------------ MCP servers (GET /mcp)
// Tools from the MCP servers in the run config: the chat offers them to the model (opt-in per request,
// "strata_mcp": true, which only this page sends); the Monitor lists the servers and what they offer.
let mcpInfo = {servers: [], tools: 0}, mcpRetry = null;
async function loadMcp() {
  try {
    const r = await fetch("mcp", {headers: headers()});
    if (!r.ok) return;
    mcpInfo = await r.json();
  } catch (e) { return; /* an older server: no MCP */ }
  renderMcp();
  clearTimeout(mcpRetry);                          // right after the start, servers may still be starting (npx downloads)
  if ((mcpInfo.servers || []).some((s) => s.status === "starting")) mcpRetry = setTimeout(loadMcp, 3000);
}
const MCP_STATE = {ready: ["st-badge--generating", "已连接"], starting: ["st-badge--reading", "启动中"],
                   failed: ["st-badge--error", "失败"], stopped: ["st-badge--queued", "已停止"], idle: ["", "等待中"]};
function renderMcp() {
  const servers = mcpInfo.servers || [];
  $("mcp-card").hidden = !servers.length;
  $("mcp-row").hidden = !servers.length;
  const ready = servers.filter((s) => s.status === "ready" || s.status === "stopped");
  $("mcp-sum").textContent = servers.length ? `${fmt(mcpInfo.tools)} 个工具 · ${ready.length}/${servers.length} 个服务已连接` : "";
  $("mcp-row-sub").textContent = mcpInfo.tools ? `${fmt(mcpInfo.tools)} 个工具，来自 ${ready.map((s) => s.name).join(", ")}；模型可按需调用`
                                               : "尚未连接 MCP 服务，请查看监控页。";
  $("mcp-list").innerHTML = servers.map((s) => {
    const [cls, text] = MCP_STATE[s.status] || ["", s.status];
    const info = s.info && s.info.name ? ` · ${s.info.name}${s.info.version ? ` ${s.info.version}` : ""}` : "";
    return `<div class="mcp-server"><div class="mcp-server__head"><span class="st-badge ${cls}">${esc(text)}</span>` +
      `<strong>${esc(s.name)}</strong><span class="muted small">${esc(s.transport)} · ${fmt(s.tools.length)} 个工具${esc(info)}</span></div>` +
      (s.error ? `<div class="msg-error">${esc(s.error)}</div>` : "") +
      (s.tools.length ? `<div class="mcp-server__tools">${s.tools.map((t) => `<span class="chip" title="${esc(t.description || "")}">${esc(t.tool)}</span>`).join("")}</div>` : "") +
      `</div>`;
  }).join("");
}

// ------------------------------------------------------------------ Model settings (GET / POST /config, #564)
// A few documented keys of the run config (strata-<model>.json), for every client, from the next start on.  The
// server lists them, checks every value and keeps every other key of the file as it is.
const CFG_ZH = [
  {
    "key": "parallel",
    "label": "并行请求数",
    "group": "并行与对话缓存",
    "help": "同时生成的最大请求数；1 为单路，建议先用 2 或 4。每路有独立状态并占用额外显存。",
    "apply_mode": "restart",
    "kind": "number",
    "min": 1,
    "step": 1
  },
  {
    "key": "batch_mtp",
    "label": "并行 MTP 加速",
    "group": "并行与对话缓存",
    "help": "并行时启用 MTP；每路每轮验证一个草稿。单路时仍使用原来的 MTP 窗口。",
    "apply_mode": "restart",
    "kind": "bool"
  },
  {
    "key": "conversation_cache_mib",
    "label": "RAM 对话缓存预算（MiB）",
    "group": "并行与对话缓存",
    "help": "切换对话时保存完整状态的 RAM 上限，单位 MiB；32768 = 32 GiB，0 或留空关闭。按需占用，退出服务后清空。",
    "apply_mode": "restart",
    "kind": "number",
    "min": 0,
    "step": 1
  },
  {
    "key": "conversation_cache_slots",
    "label": "RAM 缓存对话数",
    "group": "并行与对话缓存",
    "help": "RAM 中最多停放的对话数，引擎默认 4，本机设置为 8。实际复用的会话切走后重新放到队尾；槽位或 RAM 预算不足时，从队首淘汰最久未用的普通快照。没有时间过期，显式固定的共享前缀保留。",
    "apply_mode": "restart",
    "kind": "number",
    "min": 1,
    "step": 1
  },
  {
    "key": "conversation_cache_min_free_mib",
    "label": "最低可用 RAM（MiB）",
    "group": "并行与对话缓存",
    "help": "保存／恢复缓存时要求的最低可用系统 RAM，单位 MiB；8192 = 8 GiB。",
    "apply_mode": "restart",
    "kind": "number",
    "min": 0,
    "step": 1
  },
  {
    "key": "sampling.temperature",
    "label": "采样温度",
    "group": "采样与思考",
    "help": "客户端未指定时的温度；0 为贪心采样。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": "any"
  },
  {
    "key": "sampling.top_p",
    "label": "Top-p 概率阈值",
    "group": "采样与思考",
    "help": "客户端未指定时的 Top-p，范围大于 0 且不超过 1。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": "any"
  },
  {
    "key": "sampling.top_k",
    "label": "Top-k 候选数量",
    "group": "采样与思考",
    "help": "客户端未指定时的 Top-k，范围 1～64。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 1,
    "step": 1
  },
  {
    "key": "sampling.min_p",
    "label": "Min-p 概率阈值",
    "group": "采样与思考",
    "help": "客户端未指定时的 Min-p，范围 0～1。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": "any"
  },
  {
    "key": "reasoning_budget_tokens",
    "label": "思考预算（token）",
    "group": "采样与思考",
    "help": "每条请求的思考 token 上限；0 或留空表示不限制，不代表关闭思考。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": 1
  },
  {
    "key": "fit_max_tokens",
    "label": "自动适配输出额度",
    "group": "采样与思考",
    "help": "当输出额度超过剩余上下文时，自动缩短额度，避免返回 HTTP 400。",
    "apply_mode": "hot",
    "kind": "bool"
  },
  {
    "key": "anthropic_thinking",
    "label": "Anthropic 默认思考行为",
    "group": "采样与思考",
    "help": "Anthropic 客户端未要求思考时：按模型默认，或仅在明确请求时思考。",
    "apply_mode": "hot",
    "kind": "enum",
    "choices": [
      "model",
      "on_request"
    ],
    "choice_labels": {
      "model": "按模型默认",
      "on_request": "仅在请求中启用"
    }
  },
  {
    "key": "effort_position",
    "label": "思考强度标记位置",
    "group": "采样与思考",
    "help": "思考强度标记放在提示词开头或末尾；放末尾可减少切换强度造成的前缀缓存失效。",
    "apply_mode": "restart",
    "kind": "enum",
    "choices": [
      "start",
      "end"
    ],
    "choice_labels": {
      "start": "提示词开头",
      "end": "提示词末尾"
    }
  },
  {
    "key": "aliases",
    "label": "模型别名",
    "group": "服务与资源",
    "help": "服务接受并列出的其他模型名称，用英文逗号分隔。",
    "apply_mode": "hot",
    "kind": "names"
  },
  {
    "key": "model_profiles",
    "label": "分身模型",
    "group": "分身模型",
    "help": "分身模型：共用当前已加载模型的其他名称，每个可单独设置思考开关、思考预算和思考强度；保存后热生效并列入 /v1/models。明确设置优先于客户端与共享设置；留空项沿用原行为。普通别名仍独立保留。",
    "apply_mode": "hot",
    "kind": "profiles"
  },
  {
    "key": "idle_unload_s",
    "label": "空闲卸载时间（秒）",
    "group": "服务与资源",
    "help": "空闲多少秒后卸载模型；0 或留空表示保持加载。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": "any"
  },
  {
    "key": "lazy_load",
    "label": "首次请求时加载",
    "group": "服务与资源",
    "help": "启动时暂不加载，由首次请求加载；仅支持纯文字配置。",
    "apply_mode": "restart",
    "kind": "bool"
  },
  {
    "key": "engine_silence_s",
    "label": "引擎静默超时（秒）",
    "group": "服务与资源",
    "help": "引擎连续无响应多少秒后终止请求；默认 300 秒，0 表示一直等待。",
    "apply_mode": "hot",
    "kind": "number",
    "min": 0,
    "step": "any"
  },
  {
    "key": "api_monitor",
    "label": "保留 API 请求记录",
    "group": "服务与资源",
    "help": "在内存中保存最近 100 条 API 请求的输入和输出，用于 API 监视器。",
    "apply_mode": "hot",
    "kind": "bool"
  },
  {
    "key": "open_browser",
    "label": "启动后打开网页",
    "group": "服务与资源",
    "help": "模型准备好后自动打开聊天网页。",
    "apply_mode": "restart",
    "kind": "bool"
  },
  {
    "key": "vram_reserve_mib",
    "label": "显存预留（MiB）",
    "group": "服务与资源",
    "help": "为桌面及其他程序预留的显存，单位 MiB；引擎默认 700，当前机器建议保留 4096。",
    "apply_mode": "restart",
    "kind": "number",
    "min": 0,
    "step": 1
  }
];
let cfgKeys = [];
function profileRow(p = {}) {
  const th = p.thinking || "", ef = p.reasoning_effort || "";
  return `<div class="cfg-profile-row">` +
    `<input class="st-input p-name" placeholder="分身名称，如 strata-compress" value="${esc(p.name || "")}" autocomplete="off">` +
    `<select class="st-input p-thinking"><option value=""${th === "" ? " selected" : ""}>思考继承</option><option value="off"${th === "off" ? " selected" : ""}>关闭思考</option><option value="on"${th === "on" ? " selected" : ""}>开启思考</option></select>` +
    `<input class="st-input p-budget" type="number" min="0" step="1" placeholder="思考预算（token），默认" value="${p.reasoning_budget_tokens ?? ""}">` +
    `<select class="st-input p-effort"><option value=""${ef === "" ? " selected" : ""}>强度默认</option><option value="low"${ef === "low" ? " selected" : ""}>低</option><option value="medium"${ef === "medium" ? " selected" : ""}>中</option><option value="high"${ef === "high" ? " selected" : ""}>高</option></select>` +
    `<button type="button" class="p-del" title="删除这个分身">删除</button></div>`;
}
async function loadConfig() {
  let r;
  try { r = await fetch("config", {headers: headers()}); } catch (e) { return; }
  if (!r.ok) { $("cfg-card").hidden = true; return; }
  const c = await r.json(), byKey = new Map((c.keys || []).map(k => [k.key, k]));
  const cache = lastMetrics?.conversation_cache || {}, engine = lastMetrics?.engine || {};
  const runtime = {parallel: engine.batch_slots || 1, conversation_cache_mib: cache.budget_mib,
                   conversation_cache_slots: cache.slots, conversation_cache_min_free_mib: engine.conversation_cache_min_free_mib};
  cfgKeys = CFG_ZH.map(meta => {
    const live = byKey.get(meta.key);
    return {...meta, ...live, label: meta.label, help: meta.help, group: meta.group,
            value: live ? live.value : runtime[meta.key] ?? null, unsupported: !live,
            apply_mode: c.hot_reload_supported ? live?.apply_mode || "restart" : "restart"};
  });
  cfgKeys.push(...(c.keys || []).filter(k => !CFG_ZH.some(m => m.key === k.key)));
  $("cfg-file").textContent = c.file || "";
  const stale = cfgKeys.some(k => k.unsupported) || !c.hot_reload_supported;
  const pending = new Set(c.restart_pending || []), notices = [];
  if (stale) notices.push("网页已更新；当前服务尚未加载热生效功能，保存仍需重启生效，灰色选项暂不可修改。请在方便时关闭 Strata 服务窗口并重新运行启动脚本，再刷新本页。");
  if (pending.size) notices.push(`已保存但尚未生效，需重启整个 Strata 服务：${[...pending].map(key => CFG_ZH.find(k => k.key === key)?.label || key).join("、")}。`);
  $("cfg-backend-note").hidden = !notices.length;
  $("cfg-backend-note").textContent = notices.join(" ");
  if ($("cfg-note")) $("cfg-note").textContent = c.note || "";
  let section = "";
  $("cfg-form").innerHTML = cfgKeys.map((k, i) => {
    const id = `cfg-${i}`, v = k.value, disabled = k.unsupported ? " disabled" : "";
    let input, heading = "";
    if (k.group && k.group !== section) {
      section = k.group;
      heading = `<h3 class="cfg-section">${esc(section)}</h3>`;
    }
    if (k.kind === "bool" || k.kind === "enum") {
      const opts = k.kind === "bool" ? [["true", "开启"], ["false", "关闭"]]
        : k.choices.map(x => [x, k.choice_labels?.[x] || x]);
      const cur = v == null ? "" : String(v);
      input = `<select class="st-input" id="${id}" data-setting="${esc(k.key)}"${disabled}><option value=""${cur === "" ? " selected" : ""}>默认</option>` +
        opts.map(([val, text]) => `<option value="${esc(val)}"${cur === String(val) ? " selected" : ""}>${esc(text)}</option>`).join("") + `</select>`;
    } else if (k.kind === "profiles") {
      input = `<div class="cfg-profiles" id="${id}" data-setting="${esc(k.key)}">${(Array.isArray(v) ? v : []).map(profileRow).join("")}` +
        `<button type="button" class="cfg-profile-add">添加分身</button></div>`;
    } else {
      const text = v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
      input = `<input class="st-input" id="${id}" data-setting="${esc(k.key)}" ${k.kind === "number" ? `type="number" step="${esc(k.step ?? "any")}" min="${esc(k.min ?? 0)}"` : 'type="text"'} ` +
        `value="${esc(text)}" placeholder="默认" autocomplete="off"${disabled}>`;
    }
    return heading + `<label for="${id}" title="${esc(k.help || "")}">${esc(k.label || k.key)}<code>${esc(k.key)}</code>` +
      `<span class="cfg-help">${esc(k.help || "")}</span>` +
      `<span class="cfg-help">${pending.has(k.key) ? "已保存，待重启生效" : k.apply_mode === "hot" ? "保存后热生效，用于后续请求" : "保存后需重启整个 Strata 服务"}</span></label>${input}`;
  }).join("");
  $("cfg-form").querySelectorAll(".cfg-profiles").forEach(box => box.addEventListener("click", e => {
    if (e.target.classList.contains("cfg-profile-add")) e.target.insertAdjacentHTML("beforebegin", profileRow());
    if (e.target.classList.contains("p-del")) e.target.closest(".cfg-profile-row").remove();
  }));
  $("cfg-card").hidden = false;
}
function configValue(k, el) {
  if (k.kind === "profiles") {
    const out = [...el.querySelectorAll(".cfg-profile-row")].map(r => {
      const name = r.querySelector(".p-name").value.trim();
      if (!name) throw new Error("分身模型：名称不能为空。");
      const p = {name};
      const th = r.querySelector(".p-thinking").value;
      if (th) p.thinking = th;
      const b = r.querySelector(".p-budget").value.trim();
      if (b !== "") {
        const n = Number(b);
        if (!Number.isInteger(n) || n < 0) throw new Error(`分身 ${name}：思考预算需要不小于 0 的整数。`);
        p.reasoning_budget_tokens = n;
      }
      const ef = r.querySelector(".p-effort").value;
      if (ef) p.reasoning_effort = ef;
      return p;
    });
    return out.length ? out : null;
  }
  const s = el.value.trim();
  if (s === "") return null;
  if (k.kind === "bool") return s === "true";
  if (k.kind === "number") {
    const value = Number(s);
    if (!Number.isFinite(value)) throw new Error(`${k.label || k.key}：请输入有效数值。`);
    return value;
  }
  return s;
}
function configSaveMessage(b) {
  if (!b.changed?.length) return "没有修改。";
  const names = keys => keys.map(key => CFG_ZH.find(k => k.key === key)?.label || key).join("、");
  // An older live backend does not apply anything hot; never claim it does based on frontend metadata.
  const hot = Array.isArray(b.hot_applied) ? b.hot_applied : [];
  const restart = b.restart_pending ?? b.restart_required ?? b.changed;
  const text = hot.length ? [`已热生效：${names(hot)}（后续请求生效）。`] : ["已保存设置。"];
  if (restart.length) text.push(`需重启整个 Strata 服务后生效：${names(restart)}。`);
  text.push(`原配置已备份为 ${b.file}.bak。`);
  return text.join(" ");
}
$("cfg-save").addEventListener("click", async () => {
  if (!$("cfg-form").reportValidity()) return;
  const button = $("cfg-save");
  try {
    const set = {};
    cfgKeys.forEach((k, i) => {
      if (k.unsupported) return;
      const v = configValue(k, $(`cfg-${i}`));
      const old = Array.isArray(k.value) && k.kind !== "profiles" ? k.value.join(", ") : k.value;
      if (JSON.stringify(v) !== JSON.stringify(old ?? null)) set[k.key] = v;
    });
    if (!Object.keys(set).length) { $("cfg-msg").textContent = "没有修改。"; return; }
    button.disabled = true;
    const r = await fetch("config", {method: "POST", headers: headers(true), body: JSON.stringify({set})});
    const b = await r.json();
    if (!r.ok) throw new Error((b.error || {}).message || `HTTP ${r.status}`);
    $("cfg-msg").textContent = configSaveMessage(b);
    await loadConfig();
  } catch (e) {
    $("cfg-msg").textContent = "";
    toast("error", "保存失败", String(e.message || e), 6000);
  } finally {
    button.disabled = false;
  }
});

// ------------------------------------------------------------------ Markdown (escaped first, then formatted)
function inline(s) {
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = esc(s)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, "$1<em>$2</em>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code class="inline">${esc(codes[+i])}</code>`);
}
function codeBlock(lang, code) {
  return `<div class="st-code"><div class="st-code__head"><span>${esc(lang || "代码")}</span>` +
    `<button class="st-btn st-btn--icon" data-code-copy aria-label="复制代码">${icon("copy")}</button></div>` +
    `<pre><code>${esc(code)}</code></pre></div>`;
}
function blocks(text) {
  const out = [], lines = text.split("\n");
  let para = [], list = null;
  const flushPara = () => { if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; };
  const flushList = () => { if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if (!l.trim()) { flushPara(); flushList(); continue; }
    if ((m = l.match(/^(#{1,6})\s+(.*)$/))) { flushPara(); flushList(); out.push(`<${m[1].length <= 2 ? "h3" : "h4"}>${inline(m[2])}</${m[1].length <= 2 ? "h3" : "h4"}>`); continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(l)) { flushPara(); flushList(); out.push("<hr>"); continue; }
    if ((m = l.match(/^>\s?(.*)$/))) { flushPara(); flushList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])) {
      flushPara(); flushList();
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      let html = `<table><thead><tr>${cells(l).map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>`;
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) html += `<tr>${cells(lines[i++]).map((c) => `<td>${c}</td>`).join("")}</tr>`;
      i--;
      out.push(html + "</tbody></table>");
      continue;
    }
    if ((m = l.match(/^\s*(?:[-*+]|(\d+)[.)])\s+(.*)$/))) {
      flushPara();
      const tag = m[1] ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = {tag, items: []}; }
      list.items.push(m[2]);
      continue;
    }
    if (list && /^\s{2,}\S/.test(l)) { list.items[list.items.length - 1] += " " + l.trim(); continue; }
    flushList();
    para.push(l);
  }
  flushPara(); flushList();
  return out.join("");
}
function markdown(text) {
  let html = "", rest = text;
  for (;;) {
    const m = rest.match(/(^|\n)```([^\n`]*)\n/);
    if (!m) { html += blocks(rest); break; }
    html += blocks(rest.slice(0, m.index));
    rest = rest.slice(m.index + m[0].length);
    const end = rest.match(/(^|\n)```[ \t]*(\n|$)/);
    if (!end) { html += codeBlock(m[2].trim(), rest); break; }         // still streaming
    html += codeBlock(m[2].trim(), rest.slice(0, end.index));
    rest = rest.slice(end.index + end[0].length);
  }
  return html;
}

// ------------------------------------------------------------------ Chat
const DEFAULTS = {thinking: "high", temperature: 0.6, top_p: 0.95, top_k: 20, max: "", seed: "", show: true, esp: true, mcp: true};
let settings = {...DEFAULTS, ...store.get("sampling", {})};
let messages = store.get("chat", []);
let attachments = [];                 // {name, url}
let busy = null;                      // {controller, msg}

function saveChat() {
  store.set("chat", messages.map((m) => ({...m, images: (m.images || []).map((i) => ({name: i.name})),
                                           files: (m.files || []).map((f) => ({name: f.name}))})));
}
function timeStr(t) { return new Date(t).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}); }

function msgEl(m, i) {
  const el = document.createElement("div");
  el.className = `st-msg st-msg--${m.role}`;
  el.dataset.i = i;
  if (m.role === "user") {
    if (m.files && m.files.length) {
      const wrap = document.createElement("div");
      wrap.className = "msg-images";
      for (const f of m.files) {
        const c = document.createElement("span");
        c.className = "chip";
        c.innerHTML = icon("attach", "st-icon st-icon--sm");
        c.append(f.name);
        wrap.appendChild(c);
      }
      el.appendChild(wrap);
    }
    if (m.images && m.images.length) {
      const wrap = document.createElement("div");
      wrap.className = "msg-images";
      for (const im of m.images) {
        if (im.url) { const img = document.createElement("img"); img.src = im.url; img.alt = im.name || "图片"; wrap.appendChild(img); }
        else { const c = document.createElement("span"); c.className = "chip"; c.innerHTML = icon("image", "st-icon st-icon--sm"); c.append(im.name || "图片"); wrap.appendChild(c); }
      }
      el.appendChild(wrap);
    }
    const b = document.createElement("div");
    b.className = "st-bubble";
    b.textContent = m.text;
    el.appendChild(b);
    const meta = document.createElement("div");
    meta.className = "st-msg__meta";
    meta.textContent = `你 · ${timeStr(m.time)}`;
    el.appendChild(meta);
  } else {
    el.innerHTML = `<details class="st-collapse think" hidden><summary>${icon("thinking", "st-icon st-icon--sm")}<span class="think-title"></span>` +
      `${icon("chevron", "st-icon st-icon--sm st-chev")}</summary><div class="st-collapse__body thinking"></div></details>` +
      `<div class="st-bubble"></div><div class="st-msg__meta"><span class="meta-text"></span>` +
      `<button class="st-btn st-btn--icon" data-msg-copy aria-label="复制回答" title="复制">${icon("copy")}</button></div>`;
    updateAssistant(el, m, false);
  }
  return el;
}
// One MCP tool call in the answer: a compact block (name, state, a one-line preview) that opens to the arguments and
// the result as the model read it.  Its body is built only while open: a result can be 20,000 characters.
const TOOL_STATE = {writing: ["st-badge--reading", "生成中"], running: ["st-badge--generating", "执行中"], done: ["", "完成"],
                    error: ["st-badge--error", "错误"], skipped: ["st-badge--queued", "未执行"]};
function toolHtml(t, k) {
  const [cls, label] = TOOL_STATE[t.state] || ["", t.state];
  const args = t.arguments == null ? "" : JSON.stringify(t.arguments, null, 2);
  const preview = t.result != null ? t.result : args.replace(/\s+/g, " ");
  let body = "";
  if (t.open) {
    body = `<div class="tool-call__label">调用参数</div><pre class="tool-call__pre">${esc(args || "（正在生成）")}</pre>`;
    if (t.result != null) {
      body += `<div class="tool-call__label">${t.ok ? "结果" : "错误"}${t.chars ? ` · ${fmt(t.chars)} 字符` : ""}` +
              `${t.truncated ? "，已按模型输入上限截断" : ""}</div><pre class="tool-call__pre">${esc(t.result)}</pre>`;
    }
  }
  return `<details class="st-collapse tool-call" data-tool="${k}" data-state="${esc(t.state)}"${t.open ? " open" : ""}>` +
    `<summary>${icon("tool", "st-icon st-icon--sm")}<span class="tool-call__name" title="${esc(t.name || "")}">${esc(t.tool || t.name || "tool")}</span>` +
    (t.server ? `<span class="muted small">${esc(t.server)}</span>` : "") +
    `<span class="tool-call__preview muted">${esc(preview.slice(0, 200))}</span>` +
    `<span class="st-badge ${cls}">${esc(label)}</span>${t.ms != null && t.state !== "skipped" ? `<span class="muted small">${fmt(t.ms / 1000, 1)} s</span>` : ""}` +
    `${icon("chevron", "st-icon st-icon--sm st-chev")}</summary><div class="st-collapse__body">${body}</div></details>`;
}
// the answer's text with the tool blocks where the model called them
function answerHtml(m) {
  if (!m.tools || !m.tools.length) return markdown(m.text || "");
  let html = "", pos = 0;
  m.tools.forEach((t, k) => {
    const at = Math.min(Math.max(t.at || 0, pos), m.text.length);
    if (at > pos) html += markdown(m.text.slice(pos, at));
    pos = at;
    html += toolHtml(t, k);
  });
  return html + markdown(m.text.slice(pos));
}
// a tool event from the stream (the `strata_mcp` field of a chunk)
function onTool(m, x) {
  if (x.event === "limit") { m.limit = x.max_rounds; return; }
  m.tools = m.tools || [];
  let t = m.tools.find((y) => y.id === x.id);
  if (!t) { t = {id: x.id, name: x.name, at: m.text.length, rat: m.reasoning.length, state: "writing"}; m.tools.push(t); }
  if (x.event === "call") {
    Object.assign(t, {name: x.name, server: x.server, tool: x.tool, arguments: x.arguments, round: x.round, state: "running"});
  } else if (x.event === "result") {
    Object.assign(t, {result: x.text, ok: x.ok, chars: x.chars, truncated: x.truncated, ms: x.ms,
                      state: x.skipped ? "skipped" : x.ok ? "done" : "error"});
  }
}
function updateAssistant(el, m, streaming) {
  const det = el.querySelector("details.think");
  if (m.reasoning) {
    det.hidden = false;
    const thinkingNow = streaming && !m.text;
    el.querySelector(".think-title").textContent = thinkingNow ? "正在思考…" :
      m.thinkSecs != null ? `思考耗时 ${fmt(m.thinkSecs, 1)} 秒` : "思考过程";
    const body = el.querySelector(".thinking");
    if (det.open || thinkingNow) body.textContent = m.reasoning;
    else body.dataset.pending = "1";
    // open while it streams (if wanted), closed once the answer starts - unless the user toggled it themselves
    if (thinkingNow && settings.show && !det.dataset.touched && !det.open) { det._auto = true; det.open = true; }
    if (!thinkingNow && det.open && !det.dataset.touched) { det._auto = true; det.open = false; }
  }
  const bubble = el.querySelector(".st-bubble");
  if (m.error) {
    bubble.innerHTML = `<div class="msg-error"></div>`;
    bubble.firstChild.textContent = m.error;
  } else if (!m.text && streaming && !(m.tools && m.tools.length)) {
    bubble.innerHTML = m.reasoning ? `<span class="muted cursor">生成中</span>` : `<span class="cursor"></span>`;
  } else {
    bubble.innerHTML = answerHtml(m);
    if (streaming) bubble.classList.add("cursor"); else bubble.classList.remove("cursor");
  }
  el.querySelector(".meta-text").textContent = m.meta || (streaming ? "" : m.stopped ? "已停止" : "");
  el.querySelector("[data-msg-copy]").hidden = streaming || !m.text;
}
function renderChat() {
  const chat = $("chat");
  chat.querySelectorAll(".st-msg").forEach((e) => e.remove());
  $("chat-empty").hidden = messages.length > 0;
  messages.forEach((m, i) => chat.appendChild(msgEl(m, i)));
  scrollDown(true);
}
function nearBottom() { const s = $("chat-scroll"); return s.scrollHeight - s.scrollTop - s.clientHeight < 120; }
function scrollDown(force) { const s = $("chat-scroll"); if (force || nearBottom()) s.scrollTop = s.scrollHeight; }

$("chat").addEventListener("click", (e) => {
  const cc = e.target.closest("[data-code-copy]");
  if (cc) { copyText(cc.closest(".st-code").querySelector("pre").textContent, cc); return; }
  const mc = e.target.closest("[data-msg-copy]");
  if (mc) { const i = +mc.closest(".st-msg").dataset.i; copyText(messages[i].text, mc); return; }
  // a tool block: its open state lives in the message (the answer is rebuilt while it streams), so the click sets it
  const sum = e.target.closest(".tool-call > summary");
  if (sum) {
    e.preventDefault();
    const el = sum.closest(".st-msg"), m = messages[+el.dataset.i], t = m && m.tools && m.tools[+sum.parentElement.dataset.tool];
    if (!t) return;
    t.open = !t.open;
    updateAssistant(el, m, !!busy && busy.msg === m);
  }
});
$("chat").addEventListener("toggle", (e) => {
  const d = e.target;
  if (d.tagName !== "DETAILS" || !d.classList.contains("think")) return;
  if (d._auto) { d._auto = false; return; }          // our own open/close, not the user's
  d.dataset.touched = "1";
  const body = d.querySelector(".thinking");
  if (d.open && body.dataset.pending) { body.textContent = messages[+d.closest(".st-msg").dataset.i].reasoning; delete body.dataset.pending; }
}, true);

function apiMessages() {
  const out = [];
  for (const m of messages) {
    if (m.role === "user") {
      const imgs = (m.images || []).filter((i) => i.url);
      const text = userText(m);
      out.push({role: "user", content: imgs.length ? [{type: "text", text},
        ...imgs.map((i) => ({type: "image_url", image_url: {url: i.url}}))] : text});
    } else if (!(busy && busy.msg === m)) {            // the answer being asked for now is not history yet
      out.push(...assistantMessages(m));
    }
  }
  return out;
}
// An answer that used MCP tools goes back as the model wrote it: per round the text before the calls, the calls and
// their results (as the model read them), then the rest - so the next question can build on what the tools found.
function assistantMessages(m) {
  const ran = (m.tools || []).filter((t) => t.round != null && t.result != null && t.state !== "skipped");
  // #1392: a turn with no answer text (only reasoning, a stop before the first content token, or an error) still goes
  // back as an assistant turn, so the history keeps alternating and a reasoning model sees that it already answered.
  // The server leaves such a turn out of the prompt itself (serve/frontend.py, #843); reasoning_content rides along.
  if (!ran.length) {
    const msg = {role: "assistant", content: m.text || ""};
    if (!m.text && m.reasoning) msg.reasoning_content = m.reasoning;
    return [msg];
  }
  const out = [];
  let pos = 0;
  for (const r of [...new Set(ran.map((t) => t.round))]) {
    const calls = ran.filter((t) => t.round === r);
    const at = Math.min(Math.max(pos, calls[0].at || 0), m.text.length);
    out.push({role: "assistant", content: m.text.slice(pos, at).trim(),
              tool_calls: calls.map((t) => ({id: t.id, type: "function", function: {name: t.name, arguments: JSON.stringify(t.arguments || {})}}))});
    for (const t of calls) out.push({role: "tool", tool_call_id: t.id, content: t.result});
    pos = at;
  }
  const rest = m.text.slice(pos).trim();
  if (rest) out.push({role: "assistant", content: rest});
  return out;
}

function setBusy(on) {
  $("stop-btn").hidden = !on;
  $("send-btn").disabled = on;
  $("composer-hint").textContent = on ? "" : "Shift+Enter 换行";
}

async function send() {
  const text = $("input").value.trim();
  if ((!text && !attachments.length) || busy) return;
  messages.push({role: "user", text, images: attachments.filter((a) => a.kind !== "file"),
                 files: attachments.filter((a) => a.kind === "file"), time: Date.now()});
  attachments = [];
  renderAttachments();
  $("input").value = "";
  autosize();
  const m = {role: "assistant", text: "", reasoning: "", time: Date.now()};
  messages.push(m);
  renderChat();
  const el = $("chat").lastElementChild;
  const controller = new AbortController();
  busy = {controller, msg: m};
  setBusy(true);

  const body = {model: health.model, messages: apiMessages(), stream: true,
                reasoning_effort: settings.thinking};
  if (settings.temperature > 0) {
    Object.assign(body, {temperature: +settings.temperature, top_p: +settings.top_p, top_k: +settings.top_k});
  } else {
    body.temperature = 0;
  }
  if (settings.seed) body.seed = +settings.seed;
  if (settings.max) body.max_tokens = +settings.max;
  if (projectionLoaded()) body.experimental_speed_projection = !!settings.esp;
  if (settings.mcp !== false && mcpInfo.tools > 0) body.strata_mcp = true;   // this server may run MCP tools for it

  let firstAt = null, thinkStart = null, usage = null, frame = 0;
  const paint = () => { frame = 0; updateAssistant(el, m, true); scrollDown(); };
  try {
    const r = await fetch("v1/chat/completions", {method: "POST", headers: headers(true), body: JSON.stringify(body),
                                                   signal: controller.signal});
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).error.message || msg; } catch (e) { /* not json */ }
      if (r.status === 401) msg = "请在“关于与设置 → 网页设置”中填写 API 密钥。";
      throw new Error(msg);
    }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line.startsWith("data:")) continue;              // ": keep-alive" comments while a long prompt is read
        const data = line.slice(5).trim();
        if (data === "[DONE]") continue;
        let j;
        try { j = JSON.parse(data); } catch (e) { continue; }
        if (j.error) throw new Error(j.error.message || "引擎返回错误");
        if (j.usage) usage = j.usage;
        if (j.strata_mcp) onTool(m, j.strata_mcp);
        const d = (j.choices && j.choices[0] && j.choices[0].delta) || {};
        const lastTool = m.tools && m.tools.length ? m.tools[m.tools.length - 1] : null;   // a new round after a tool
        if (d.reasoning_content) {
          if (!firstAt) firstAt = performance.now();
          if (!thinkStart) thinkStart = performance.now();
          if (lastTool && m.reasoning && lastTool.rat === m.reasoning.length) m.reasoning += "\n\n";
          m.reasoning += d.reasoning_content;
        }
        if (d.content) {
          if (!firstAt) firstAt = performance.now();
          if (thinkStart && m.thinkSecs == null) m.thinkSecs = (performance.now() - thinkStart) / 1000;
          if (lastTool && m.text && lastTool.at === m.text.length) m.text += "\n\n";
          m.text += d.content;
        }
        if (!frame) frame = requestAnimationFrame(paint);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") m.stopped = true;
    else { m.error = e.message || String(e); toast("error", "请求失败", m.error, 6000); }
  }
  if (thinkStart && m.thinkSecs == null) m.thinkSecs = (performance.now() - thinkStart) / 1000;
  const n = usage ? usage.completion_tokens : null;
  if (n && firstAt) {
    const secs = (performance.now() - firstAt) / 1000;
    m.meta = `${fmt(n)} tokens${secs > 0.25 ? ` · ${fmt(n / secs, 1)} tok/s` : ""}${m.stopped ? " · 已停止" : ""}` +
             (projectionLoaded() ? (settings.esp ? " · 投影开启" : " · 投影关闭") : "");
  } else if (m.stopped) {
    m.meta = "已停止";
  }
  for (const t of m.tools || []) if (t.state === "writing" || t.state === "running") { t.state = "skipped"; t.ms = null; }
  const ran = (m.tools || []).filter((t) => t.state === "done" || t.state === "error").length;
  if (ran) m.meta = `${m.meta ? `${m.meta} · ` : ""}${ran} 次工具调用`;
  if (m.limit) m.meta = `${m.meta || ""} · 达到 ${m.limit} 轮工具调用上限（mcp.max_rounds）`;
  busy = null;
  setBusy(false);
  if (frame) cancelAnimationFrame(frame);
  updateAssistant(el, m, false);
  saveChat();
  scrollDown();
}

$("composer").onsubmit = (e) => { e.preventDefault(); send(); };
$("stop-btn").onclick = () => { if (busy) busy.controller.abort(); };
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, innerHeight * 0.4)}px`; }
$("input").addEventListener("input", autosize);

$("new-btn").onclick = () => {
  if (busy) { toast("warn", "仍在生成", "请先停止当前回答。"); return; }
  if (!messages.length) return;
  const backup = messages;
  messages = [];
  saveChat();
  renderChat();
  toast("info", "新建对话", "已清空上一段对话。", 6000, {label: "撤销", run: () => { messages = backup; saveChat(); renderChat(); }});
};
$("export-btn").onclick = () => {
  if (!messages.length) { toast("info", "暂无可导出的对话"); return; }
  const tools = (m) => (m.tools || []).filter((t) => t.result != null).map((t) =>
    `<details><summary>工具 ${t.server ? `${t.server} / ` : ""}${t.tool || t.name}${t.ok ? "" : "（错误）"}</summary>\n\n` +
    `\`\`\`json\n${JSON.stringify(t.arguments || {}, null, 2)}\n\`\`\`\n\n\`\`\`\n${t.result}\n\`\`\`\n\n</details>\n\n`).join("");
  const md = messages.map((m) => m.role === "user" ? `## 你\n\n${m.text}\n` :
    `## ${health.model}\n\n${m.reasoning ? `<details><summary>思考过程</summary>\n\n${m.reasoning}\n\n</details>\n\n` : ""}${tools(m)}${m.text || m.error || ""}\n`).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([md], {type: "text/markdown"}));
  a.download = `strata-chat-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};

// pictures and text files: the attach button, dropping them on the chat, or pasting a picture (issue #30)
const TEXT_EXT = /\.(txt|md|markdown|rst|tex|py|pyi|ipynb|js|mjs|cjs|ts|tsx|jsx|vue|svelte|json|jsonl|csv|tsv|log|ya?ml|toml|ini|cfg|conf|env|xml|html?|css|scss|less|c|cc|cpp|cxx|h|hh|hpp|cu|cuh|rs|go|java|kt|kts|swift|rb|php|pl|lua|r|jl|scala|sql|sh|bash|zsh|fish|ps1|psm1|bat|cmd|diff|patch|gradle|cmake|mk|dockerfile|gitignore|proto|graphql)$/i;
const MAX_TEXT_FILE = 512 * 1024;
function isTextFile(f) {
  return f.type.startsWith("text/") || /json|xml|javascript|yaml|toml|x-sh|x-python/.test(f.type) ||
         TEXT_EXT.test(f.name) || /(^|[\\/])(makefile|dockerfile|readme|license)$/i.test(f.name);
}
function addFiles(files) {
  for (const f of files) {
    if (f.type.startsWith("image/")) {
      if (!health.images) { toast("warn", "未启用图片识别", "当前模型配置只支持文字。"); continue; }
      if (f.size > 20e6) { toast("warn", "图片过大", `${f.name} 超过 20 MB。`); continue; }
      const r = new FileReader();
      r.onload = () => { attachments.push({kind: "image", name: f.name || "粘贴的图片", url: r.result}); renderAttachments(); };
      r.readAsDataURL(f);
      continue;
    }
    if (!isTextFile(f)) { toast("warn", "不是文本文件", `${f.name}：请添加文本文件（代码、笔记、日志、数据）${health.images ? "或图片" : ""}。`); continue; }
    if (f.size > MAX_TEXT_FILE) { toast("warn", "文件过大", `${f.name} 超过 512 KB。`); continue; }
    const r = new FileReader();
    r.onload = () => {
      const text = String(r.result);
      if (text.includes("\u0000")) { toast("warn", "不是文本文件", `${f.name} 似乎是二进制文件。`); return; }
      attachments.push({kind: "file", name: f.name, text});
      renderAttachments();
    };
    r.readAsText(f);
  }
}
// a file's text in the message, fenced with more backticks than it contains itself
function fileBlock(f) {
  const longest = Math.max(2, ...(f.text.match(/`+/g) || []).map((s) => s.length));
  const fence = "`".repeat(longest + 1);
  return `File: ${f.name}\n${fence}\n${f.text}\n${fence}`;
}
function userText(m) {
  const files = (m.files || []).filter((f) => f.text != null);
  return [m.text, ...files.map(fileBlock)].filter((s) => s).join("\n\n");
}
function renderAttachments() {
  const box = $("attachments");
  box.hidden = !attachments.length;
  box.innerHTML = "";
  attachments.forEach((a, i) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.innerHTML = icon(a.kind === "file" ? "attach" : "image", "st-icon st-icon--sm");
    c.append(a.name);
    const x = document.createElement("button");
    x.type = "button"; x.className = "st-btn st-btn--icon"; x.setAttribute("aria-label", "移除");
    x.innerHTML = icon("trash");
    x.onclick = () => { attachments.splice(i, 1); renderAttachments(); };
    c.appendChild(x);
    box.appendChild(c);
  });
}
$("attach-btn").onclick = () => $("file").click();
// drop files on the chat or the message box
for (const id of ["chat", "composer"]) {
  const el = $(id);
  el.addEventListener("dragover", (e) => {
    if (![...(e.dataTransfer || {}).types || []].includes("Files")) return;
    e.preventDefault();
    $("composer").classList.add("dragging");
  });
  el.addEventListener("dragleave", () => $("composer").classList.remove("dragging"));
  el.addEventListener("drop", (e) => {
    $("composer").classList.remove("dragging");
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    addFiles(e.dataTransfer.files);
    $("input").focus();
  });
}
$("file").onchange = () => { addFiles($("file").files); $("file").value = ""; };
$("input").addEventListener("paste", (e) => {
  if (!health.images) return;
  const files = [...(e.clipboardData || {}).files || []].filter((f) => f.type.startsWith("image/"));
  if (files.length) { e.preventDefault(); addFiles(files); }
});

// ------------------------------------------------------------------ the sampling drawer
function openDrawer(open) {
  $("drawer").dataset.open = String(open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("scrim").hidden = !open;
  if (open) { loadDrawer(); loadShared(); loadMcp(); }
}
function loadDrawer(s = settings) {
  for (const b of $("s-thinking").children) b.setAttribute("aria-checked", String(b.dataset.v === s.thinking));
  $("s-temp").value = s.temperature; $("s-topp").value = s.top_p; $("s-topk").value = s.top_k;
  $("s-max").value = s.max; $("s-seed").value = s.seed;
  $("s-show").setAttribute("aria-checked", String(!!s.show));
  $("s-esp").setAttribute("aria-checked", String(s.esp !== false));
  $("esp-row").hidden = !projectionLoaded();
  $("s-mcp").setAttribute("aria-checked", String(s.mcp !== false));
  $("s-share").setAttribute("aria-checked", String(sharedOn));
  outputs();
}
// "Use for other apps too": the server keeps these settings as every client's defaults (GET/POST /settings)
let sharedOn = false;
async function loadShared() {
  try {
    const r = await fetch("settings", {headers: headers()});
    if (r.ok) sharedOn = !!(await r.json()).shared;
  } catch (e) { /* an older server: the switch just stays off */ }
  $("s-share").setAttribute("aria-checked", String(sharedOn));
}
function sharedDefaults(s) {
  const d = {reasoning_effort: s.thinking, temperature: +s.temperature};
  if (+s.temperature > 0) Object.assign(d, {top_p: +s.top_p, top_k: +s.top_k});
  if (s.seed) d.seed = +s.seed;
  if (s.max) d.max_tokens = +s.max;
  if (projectionLoaded()) d.experimental_speed_projection = s.esp !== false;
  return d;
}
async function saveShared(on, s) {
  const r = await fetch("settings", {method: "POST", headers: headers(true),
                                      body: JSON.stringify({defaults: on ? sharedDefaults(s) : null})});
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).error.message || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  sharedOn = !!(await r.json()).shared;
}
// the engine was started with the experimental-speed-projection control vector (INFO cvec=...)
function projectionLoaded() {
  const c = lastMetrics && lastMetrics.engine ? lastMetrics.engine.cvec : 0;
  return !!c && c !== "0";
}
function outputs() {
  const t = +$("s-temp").value;
  $("o-temp").textContent = t === 0 ? "0 · 贪心采样" : t.toFixed(2);
  $("o-topp").textContent = (+$("s-topp").value).toFixed(2);
  $("o-topk").textContent = $("s-topk").value;
  const sel = [...$("s-thinking").children].find((b) => b.getAttribute("aria-checked") === "true");
  $("o-thinking").textContent = sel ? {none: "直接回答", low: "简短思考", medium: "适度思考", high: "充分思考（默认）"}[sel.dataset.v] : "";
  for (const id of ["s-topp", "s-topk"]) $(id).disabled = t === 0;
}
for (const b of $("s-thinking").children) b.onclick = () => { for (const x of $("s-thinking").children) x.setAttribute("aria-checked", String(x === b)); outputs(); };
for (const id of ["s-temp", "s-topp", "s-topk"]) $(id).oninput = outputs;
$("s-show").onclick = () => $("s-show").setAttribute("aria-checked", String($("s-show").getAttribute("aria-checked") !== "true"));
$("s-esp").onclick = () => $("s-esp").setAttribute("aria-checked", String($("s-esp").getAttribute("aria-checked") !== "true"));
$("s-mcp").onclick = () => $("s-mcp").setAttribute("aria-checked", String($("s-mcp").getAttribute("aria-checked") !== "true"));
$("s-share").onclick = () => $("s-share").setAttribute("aria-checked", String($("s-share").getAttribute("aria-checked") !== "true"));
$("s-reset").onclick = () => loadDrawer(DEFAULTS);
$("s-apply").onclick = async () => {
  const sel = [...$("s-thinking").children].find((b) => b.getAttribute("aria-checked") === "true");
  settings = {thinking: sel ? sel.dataset.v : "high", temperature: +$("s-temp").value, top_p: +$("s-topp").value,
              top_k: +$("s-topk").value, max: $("s-max").value.trim(), seed: $("s-seed").value.trim(),
              show: $("s-show").getAttribute("aria-checked") === "true",
              esp: $("s-esp").getAttribute("aria-checked") === "true",
              mcp: $("s-mcp").getAttribute("aria-checked") === "true"};
  store.set("sampling", settings);
  const share = $("s-share").getAttribute("aria-checked") === "true";
  openDrawer(false);
  if (share || sharedOn) {
    try {
      await saveShared(share, settings);
      toast("success", "采样设置已保存", share ? "其他 API 客户端从下一条请求开始使用这些默认设置。"
                                               : "其他客户端恢复使用各自的设置。");
    } catch (e) {
      toast("error", "已保存在浏览器，但共享设置保存失败", e.message, 6000);
    }
    return;
  }
  toast("success", "采样设置已保存", settings.temperature === 0 ? "已启用贪心采样。" : "");
};
$("sampling-btn").onclick = () => openDrawer(true);
$("drawer-close").onclick = () => openDrawer(false);
$("scrim").onclick = () => openDrawer(false);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("drawer").dataset.open === "true") openDrawer(false); });

// ------------------------------------------------------------------ start
setBusy(false);
renderChat();
const startQuestion = new URLSearchParams(location.search).get("q");   // /?q=... starts a chat (a shortcut)
if (startQuestion) history.replaceState(null, "", location.pathname + location.hash);
loadHealth().then(loadMcp).then(() => { if (startQuestion) { $("input").value = startQuestion; send(); } });
showTab(location.hash.slice(1) || "chat");
poll();

// ------------------------------------------------------------------ images (POST /v1/images/generations)
$("img-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const prompt = $("img-prompt").value.trim();
  if (!prompt) return;
  const body = {prompt, size: $("img-size").value, response_format: "b64_json"};
  if ($("img-seed").value !== "") body.seed = Number($("img-seed").value);
  $("img-go").disabled = true;
  const t0 = performance.now();
  $("img-hint").textContent = "Generating\u2026 (a minute or more on a small GPU)";
  try {
    const r = await fetch("v1/images/generations", {method: "POST", headers: headers(true), body: JSON.stringify(body)});
    const j = await r.json();
    if (!r.ok) throw new Error((j.error && j.error.message) || r.statusText);
    for (const d of j.data) {
      const fig = document.createElement("figure");
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + d.b64_json;
      img.alt = prompt;
      const cap = document.createElement("figcaption");
      cap.textContent = `${j.size}, seed ${d.seed}, ${((performance.now() - t0) / 1000).toFixed(0)} s: ${prompt}`;
      fig.append(img, cap);
      $("img-results").prepend(fig);
    }
    $("img-hint").textContent = "Done. Right-click an image to save it.";
  } catch (e) {
    $("img-hint").textContent = "";
    toast("error", "No image", String(e.message || e), 8000);
  } finally {
    $("img-go").disabled = false;
  }
};
