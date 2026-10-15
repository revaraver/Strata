"use strict";
const $ = id => document.getElementById(id);
let selected = null, detail = null, pane = "input", records = [], state = null, operating = false, stamp = "", actionError = "";
let apiKey = sessionStorage.getItem("strata.monitor.key") || "";
document.documentElement.dataset.theme = localStorage.getItem("strata.theme") || "dark";
const seconds = n => typeof n === "number" ? `${n.toFixed(2)} s` : "—";
const speed = n => typeof n === "number" ? n.toFixed(1) : "—";
const stateText = s => ({completed:"已完成", error:"错误", disconnected:"连接断开", queued:"排队中", loading:"加载中", reading:"预填充", generating:"生成中", streaming:"输出中", running:"运行中"}[s] || s);
const active = r => !["completed", "error", "disconnected"].includes(r.state);
function text(id, value) { const el = $(id); if (el.textContent !== String(value)) el.textContent = value; }
async function api(path, body) {
  const response = await fetch(path, {cache:"no-store", headers:{...(apiKey ? {Authorization:`Bearer ${apiKey}`} : {}), ...(body !== undefined ? {"Content-Type":"application/json"} : {})}, ...(body !== undefined ? {method:"POST", body:JSON.stringify(body)} : {})});
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401) $("auth").hidden = false;
    throw new Error(value.error?.message || `HTTP ${response.status}`);
  }
  return value;
}
function notice(message) { $("error").hidden = !message; text("error", message || ""); }
function list() {
  const query = $("search").value.toLowerCase(), filter = $("filter").value;
  const visible = records.filter(r => `${r.id} ${r.path} ${r.model}`.toLowerCase().includes(query) && (filter === "all" || (filter === "active" ? active(r) : r.state === filter)));
  const nextStamp = JSON.stringify([visible, selected]);
  if (stamp === nextStamp) return;
  stamp = nextStamp;
  const focusId = document.activeElement?.dataset.request;
  $("requests").replaceChildren();
  if (!visible.length) {
    const empty = document.createElement("div"); empty.className = "empty";
    empty.textContent = records.length ? "没有匹配的请求。" : "暂无请求，API 调用会自动显示在这里。";
    $("requests").append(empty);
  }
  for (const r of visible) {
    const button = document.createElement("button"); button.className = `request${selected === r.id ? " selected" : ""}`;
    button.dataset.request = r.id; button.setAttribute("aria-label", `请求 ${r.id}`); button.setAttribute("aria-pressed", String(selected === r.id));
    const row = document.createElement("div"); row.className = "row";
    const time = document.createElement("span"); time.textContent = new Date(r.started_at * 1000).toLocaleTimeString();
    const tag = document.createElement("span"); tag.className = `tag ${r.state}`; tag.textContent = stateText(r.state);
    row.append(time, tag);
    const endpoint = document.createElement("div"); endpoint.className = "endpoint"; endpoint.textContent = `POST ${r.path}`;
    const meta = document.createElement("div"); meta.className = "small";
    meta.textContent = `${r.id} · ${seconds(r.wallclock_s)} · ${r.stream ? "流式响应" : "JSON 响应"}${r.http_status ? ` · HTTP ${r.http_status}` : ""}`;
    button.append(row, endpoint, meta);
    button.onclick = async () => { selected = r.id; detail = null; list(); await showDetail(); };
    $("requests").append(button);
    if (focusId === r.id) button.focus({preventScroll:true});
  }
}
function content() {
  if (!detail) return;
  let value = detail[pane] || (pane === "response" && detail.stream ? "流式响应请查看“输出”和请求错误。" : "暂无内容。");
  if (pane === "output") { try { value = JSON.stringify(JSON.parse(value), null, 2); } catch (_) {} }
  text("content", value);
  const note = pane === "output" && detail.error ? "保留原始模型输出供排查；API 返回了错误。" : pane === "input" ? "原始请求体。" : pane === "output" ? "模型回答；JSON 已格式化以便阅读。" : pane === "reasoning" ? "启用思考时的独立思考内容。" : "API 响应体。";
  text("content-note", note + (detail[`${pane}_truncated`] ? "监视器只保留前 256K 字符；实际 API 响应未缩短。" : ""));
}
async function showDetail() {
  if (!selected) return;
  const id = selected;
  const value = await api(`/api/requests?id=${encodeURIComponent(id)}`);
  if (selected !== id) return;
  detail = value; $("selection").hidden = false; $("no-selection").hidden = true;
  text("detail-title", `${value.id} · ${value.path}`);
  text("detail-status", stateText(value.state)); $("detail-status").className = `tag ${value.state}`;
  text("detail-time", new Date(value.started_at * 1000).toLocaleString());
  text("detail-wall", seconds(value.wallclock_s ?? ((Date.now() / 1000) - value.started_at)));
  text("detail-load", seconds(value.load_s)); text("detail-queue", seconds(value.queue_s)); text("detail-first", seconds(value.first_token_s));
  text("detail-prompt", value.usage?.prompt_tokens ?? value.usage?.input_tokens ?? "—");
  text("detail-tokens", value.usage?.completion_tokens ?? value.usage?.output_tokens ?? "—");
  text("detail-speed", speed(value.timings?.predicted_per_second)); text("detail-format", value.response_format || "文本");
  $("detail-error").hidden = !value.error; text("detail-error", value.error?.message || ""); content();
}
async function refresh() {
  try {
    const [status, history] = await Promise.all([api("/v1/status"), api("/api/requests")]);
    state = status; records = history.requests;
    status.loaded = history.loaded; status.auto_load = history.auto_load;
    if (selected && !records.some(r => r.id === selected)) {
      selected = detail = null; $("selection").hidden = true; $("no-selection").hidden = false;
    }
    const inflight = Math.max(status.activity.in_flight, records.filter(active).length);
    const loading = records.some(r => r.state === "loading");
    text("loaded", loading ? "正在加载…" : inflight ? "使用中" : status.loaded ? "已加载" : "未加载");
    $("model-state").className = `value ${inflight || loading ? "busy" : status.loaded ? "loaded" : ""}`;
    text("model", status.model); text("count", records.length);
    text("active", inflight ? `${inflight} 条运行／排队请求` : status.auto_load ? "下一条请求到达时自动加载" : "没有进行中的请求");
    const last = records.find(r => !active(r));
    text("wall", last ? seconds(last.wallclock_s) : "—"); text("speed", speed(last?.timings?.predicted_per_second));
    $("load").disabled = operating || Boolean(inflight) || status.loaded; $("unload").disabled = operating || Boolean(inflight) || !status.loaded;
    list(); if (selected) await showDetail();
    text("updated", `实时 · 更新于 ${new Date().toLocaleTimeString()}`);
    if (!operating) notice(actionError);
  } catch (error) { notice(error.message); text("updated", "连接断开，正在重试"); $("load").disabled = $("unload").disabled = true; }
}
async function control(load) {
  actionError = "";
  operating = true; $("load").disabled = $("unload").disabled = true;
  text("loaded", load ? "正在加载…" : "正在卸载…");
  try { await api(load ? "/load" : "/unload", {}); notice(""); }
  catch (error) { actionError = error.message; notice(actionError); }
  finally { operating = false; await refresh(); }
}
$("load").onclick = () => control(true); $("unload").onclick = () => control(false);
$("key").onclick = () => { $("auth").hidden = !$("auth").hidden; if (!$("auth").hidden) $("api-key").focus(); };
$("auth").onsubmit = async event => { event.preventDefault(); apiKey = $("api-key").value.trim(); sessionStorage.setItem("strata.monitor.key", apiKey); $("auth").hidden = true; await refresh(); };
$("theme").onclick = () => { const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = theme; localStorage.setItem("strata.theme", theme); };
$("search").oninput = list; $("filter").onchange = list;
document.querySelectorAll("[data-pane]").forEach(button => { button.onclick = () => { pane = button.dataset.pane; document.querySelectorAll("[data-pane]").forEach(b => b.setAttribute("aria-selected", String(b === button))); content(); }; });
$("copy").onclick = async () => { try { await navigator.clipboard.writeText($("content").textContent); text("copy", "已复制"); setTimeout(() => text("copy", "复制"), 1500); } catch (_) { notice("无法访问剪贴板，请选中文本后复制。"); } };
async function poll() { await refresh(); setTimeout(poll, 2000); }
poll();
