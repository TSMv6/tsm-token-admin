/* TSM Token Manager - front end (no framework, no external scripts). */
"use strict";

const S = {
  me: null, data: null, tab: "tokens", filter: "all", search: "",
  sort: { key: "issued", dir: -1 }, actSearch: "", actFilter: "", manifest: null,
};

// ---------------------------------------------------------------- utilities
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (strings, ...vals) => strings.reduce((a, s, i) => a + s + (i < vals.length ? vals[i] : ""), "");

async function api(op, body) {
  const opts = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-TSM-Request": "1" },
  };
  const r = await fetch(`/api?op=${op}`, { credentials: "same-origin", ...opts });
  let j = {};
  try { j = await r.json(); } catch (_) { /* non-JSON */ }
  if (!r.ok) { const e = new Error(j.error || `Request failed (${r.status})`); e.status = r.status; e.data = j; throw e; }
  return j;
}

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), bad ? 6000 : 3200);
}

async function copy(text, label = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch (_) {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove(); toast(label);
  }
}

const todayISO = () => (S.data && S.data.today) || new Date().toISOString().slice(0, 10);
function daysUntil(d) {
  if (!d) return null;
  return Math.round((Date.parse(d + "T00:00:00Z") - Date.parse(todayISO() + "T00:00:00Z")) / 86400000);
}
function fmtDate(d) {
  if (!d) return "";
  const t = Date.parse(d.length <= 10 ? d + "T12:00:00Z" : d);
  if (isNaN(t)) return d;
  return new Date(t).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function fmtWhen(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
const avatar = (login) => login && !/^(import|local)/.test(login) && S.me.mode !== "local"
  ? `https://avatars.githubusercontent.com/${encodeURIComponent(login)}?s=80` : "favicon.svg";

const EXPIRING_DAYS = 30;
function displayStatus(t) {
  if (t.status === "active") { const d = daysUntil(t.expires); if (d !== null && d <= EXPIRING_DAYS) return "expiring"; }
  return t.status;
}
const STATUS_LABEL = { active: "Active", expiring: "Expiring soon", revoked: "Revoked", expired: "Expired" };
const badge = (st) => `<span class="badge ${st}">${STATUS_LABEL[st] || st}</span>`;

const ICON = {
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20V9m0 0-4.5 4.5M12 9l4.5 4.5M5 4h14"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
  sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8m0-4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16m0 4v-4h-4"/></svg>',
  gh: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>',
};

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    S.me = await api("me");
  } catch (e) {
    if (e.status === 401) return renderLogin(e.data && e.data.mode);
    $("#app").innerHTML = `<div class="boot">Could not reach the server: ${esc(e.message)}</div>`;
    return;
  }
  await reload();
  applyHash();
  checkManifest();
}

async function reload() {
  try {
    S.data = await api("state");
  } catch (e) {
    if (e.status === 401) return renderLogin();
    S.data = S.data || { tokens: [], managers: [], owners: [], audit: [] };
    toast(e.message, true);
  }
  render();
}

async function checkManifest() {
  try { S.manifest = await api("manifest_status"); }
  catch (e) { S.manifest = { error: e.message }; }
  renderTopbar();
  if (S.tab === "data") renderMain();
}

// ---------------------------------------------------------------- login
function renderLogin() {
  const denied = new URLSearchParams(location.search).get("denied");
  $("#app").innerHTML = h`
  <div class="login-wrap">
    <div class="card login">
      <img class="logo" src="favicon.svg" alt="">
      <h1>TSM Token Manager</h1>
      <p>Issue and revoke TSM model access tokens.<br>Approved managers only.</p>
      ${denied ? `<div class="banner bad" style="text-align:left">GitHub account <b>${esc(denied)}</b> is not an approved manager. Ask the owner to add you, then sign in again.</div>` : ""}
      <a class="btn github" href="/api?op=login">${ICON.gh} Sign in with GitHub</a>
      <div class="foot">Access is limited to GitHub accounts approved by the TSM owner.</div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- layout
function render() {
  $("#app").innerHTML = h`
    <header class="topbar"><div class="topbar-inner" id="topbar"></div>
      <nav class="tabs" id="tabs"></nav></header>
    <main id="main"></main>`;
  renderTopbar(); renderTabs(); renderMain();
}

function manifestPill() {
  const m = S.manifest;
  if (!m) return `<span class="pill neutral"><span class="dot"></span>Checking manifest…</span>`;
  if (m.error) return `<span class="pill bad clickable" data-goto="data"><span class="dot"></span>Manifest check failed</span>`;
  if (m.in_sync) return `<span class="pill ok clickable" data-goto="data" title="Every revoked token is in the live manifest"><span class="dot"></span>Manifest in sync</span>`;
  if ((m.repo_missing || []).length) return `<span class="pill bad clickable" data-goto="data"><span class="dot"></span>Manifest out of sync</span>`;
  return `<span class="pill warn clickable" data-goto="data" title="Repo updated; waiting for Vercel to deploy"><span class="dot"></span>Deploying…</span>`;
}

function renderTopbar() {
  const tb = $("#topbar"); if (!tb) return;
  const me = S.me;
  tb.innerHTML = h`
    <div class="brand"><img src="favicon.svg" alt=""> TSM Token Manager <small>model access tokens</small></div>
    ${me.mode === "local" ? `<span class="pill local" title="${esc(me.registry)}">LOCAL COPY</span>` : ""}
    <div class="spacer"></div>
    ${manifestPill()}
    <div class="userchip">
      ${me.avatar ? `<img src="${esc(me.avatar)}" alt="">` : ""}
      <div><div>${esc(me.name || me.login)}</div><div class="role">${esc(me.role)}</div></div>
    </div>
    ${me.mode === "local" ? "" : `<a class="btn ghost sm" href="/api?op=logout">Sign out</a>`}`;
  tb.querySelectorAll("[data-goto]").forEach((el) => el.onclick = () => go(el.dataset.goto));
}

function renderTabs() {
  const d = S.data;
  const activeMgrs = d.managers.filter((m) => !m.removed_at).length + d.owners.length;
  const tabs = [["tokens", "Tokens", d.tokens.length], ["activity", "Activity", d.audit.length],
    ["managers", "Managers", activeMgrs], ["data", "Data & sync", null]];
  $("#tabs").innerHTML = tabs.map(([k, l, n]) =>
    `<button class="tab ${S.tab === k ? "active" : ""}" data-tab="${k}">${l}${n !== null ? `<span class="count">${n}</span>` : ""}</button>`).join("");
  $("#tabs").querySelectorAll(".tab").forEach((b) => b.onclick = () => go(b.dataset.tab));
}

function go(tab) {
  S.tab = tab; renderTabs(); renderMain();
  try { history.replaceState(null, "", tab === "tokens" ? location.pathname : "#" + tab); } catch (_) { /* ignore */ }
  if (tab === "data") checkManifest();
}

// Deep links: #activity, #managers, #data, #token=tsm-2026-0012
function applyHash() {
  const hsh = decodeURIComponent(location.hash.slice(1));
  if (hsh.startsWith("token=")) { const [jti, email] = hsh.slice(6).split("|"); S.tab = "tokens"; render(); openToken(jti, email); return; }
  if (["activity", "managers", "data"].includes(hsh)) S.tab = hsh;
  render();
}

function renderMain() {
  const problems = (S.me.problems || []).length
    ? `<div class="banner warn"><b>Setup incomplete:</b><ul>${S.me.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>` : "";
  const main = $("#main");
  main.innerHTML = problems + ({ tokens: tokensView, activity: activityView, managers: managersView, data: dataView }[S.tab])();
  ({ tokens: bindTokens, activity: bindActivity, managers: bindManagers, data: bindData }[S.tab])(main);
}

// ---------------------------------------------------------------- tokens tab
function tokenCounts() {
  const c = { all: 0, active: 0, expiring: 0, revoked: 0, expired: 0 };
  for (const t of S.data.tokens) {
    c.all++; const st = displayStatus(t); c[st]++; if (st === "expiring") c.active++;
  }
  return c;
}

function filteredTokens() {
  const q = S.search.trim().toLowerCase();
  let rows = S.data.tokens.filter((t) => {
    const st = displayStatus(t);
    if (S.filter === "active" && !(st === "active" || st === "expiring")) return false;
    if (S.filter === "expiring" && st !== "expiring") return false;
    if (S.filter === "revoked" && st !== "revoked") return false;
    if (S.filter === "expired" && st !== "expired") return false;
    if (!q) return true;
    return [t.name, t.email, t.jti, t.issued_by, t.revoked_by, t.modified_by, t.notes].some((v) => (v || "").toLowerCase().includes(q));
  });
  const { key, dir } = S.sort;
  const val = (t) => key === "status" ? displayStatus(t) : key === "changed" ? (t.modified_at || t.issued || "") : (t[key] || "");
  rows.sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * dir || (a.jti < b.jti ? 1 : -1));
  return rows;
}

function tokensView() {
  const c = tokenCounts();
  const stat = (k, label, cls, hint) => `<div class="card stat ${cls} ${S.filter === k ? "sel" : ""}" data-filter="${k}">
      <div class="lbl">${label}</div><div class="val">${c[k]}</div><div class="hint">${hint}</div></div>`;
  const th = (k, l, cls = "") => `<th data-sort="${k}" class="${cls}">${l}${S.sort.key === k ? `<span class="arr">${S.sort.dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`;
  const rows = filteredTokens();
  return h`
    <div class="stats">
      ${stat("active", "Active", "ok", "can run TSM models")}
      ${stat("expiring", "Expiring soon", "warn", `within ${EXPIRING_DAYS} days`)}
      ${stat("revoked", "Revoked", "bad", "blocked via manifest")}
      ${stat("expired", "Expired", "", "past expiry date")}
    </div>
    <div class="toolbar">
      <input class="search" id="search" placeholder="Search name, email, token id, manager…" value="${esc(S.search)}">
      <select class="sel" id="filter">
        ${["all", "active", "expiring", "revoked", "expired"].map((k) => `<option value="${k}" ${S.filter === k ? "selected" : ""}>${k === "all" ? "All tokens" : STATUS_LABEL[k]}</option>`).join("")}
      </select>
      <div class="spacer"></div>
      <a class="btn" href="/api?op=download_tokens">${ICON.down} Download CSV</a>
      <button class="btn primary" id="btn-issue">${ICON.plus} Issue token</button>
    </div>
    <div class="card table-wrap">
      <table class="grid">
        <thead><tr>${th("name", "User")}${th("jti", "Token ID")}${th("issued", "Issued")}${th("expires", "Expires")}${th("status", "Status")}${th("changed", "Last change", "hide-sm")}</tr></thead>
        <tbody>
        ${rows.length ? rows.map(tokenRow).join("") : `<tr><td colspan="6" class="empty">No tokens match.</td></tr>`}
        </tbody>
      </table>
    </div>
    <p class="faint" style="margin-top:10px;font-size:12.5px">Showing ${rows.length} of ${S.data.tokens.length}. Click a row to view, copy, extend or revoke.</p>`;
}

function tokenRow(t) {
  const st = displayStatus(t);
  const d = daysUntil(t.expires);
  const expHint = st === "revoked" ? "" : d === null ? "" : d < 0 ? `${-d} days ago` : d === 0 ? "today" : `in ${d} days`;
  let change = "";
  if (t.revoked) change = `Revoked by ${esc(t.revoked_by || "?")}<div class="by">${fmtDate(t.revoked)}</div>`;
  else if (t.modified_at) change = `Modified by ${esc(t.modified_by)}<div class="by">${fmtWhen(t.modified_at)}</div>`;
  else change = `<span class="faint">—</span>`;
  return h`<tr class="row" data-jti="${esc(t.jti)}" data-email="${esc(t.email)}">
    <td><div class="who">${esc(t.name || "—")}</div><div class="who-sub">${esc(t.email)}</div></td>
    <td class="mono nowrap">${esc(t.jti)}${shared(t).length ? `<div class="by" style="color:var(--warn)" title="This token id is also used by ${esc(shared(t).map((x) => x.email).join(", "))}">shared id</div>` : ""}</td>
    <td class="nowrap">${fmtDate(t.issued)}<div class="by">by ${esc(t.issued_by || "?")}</div></td>
    <td class="nowrap">${fmtDate(t.expires)}<div class="by">${expHint}</div></td>
    <td>${badge(st)}</td>
    <td class="hide-sm">${change}</td></tr>`;
}

function bindTokens(root) {
  const s = $("#search", root);
  s.oninput = () => { S.search = s.value; const pos = s.selectionStart; renderMain(); const n = $("#search"); n.focus(); n.setSelectionRange(pos, pos); };
  $("#filter", root).onchange = (e) => { S.filter = e.target.value; renderMain(); };
  root.querySelectorAll("[data-filter]").forEach((el) => el.onclick = () => { S.filter = S.filter === el.dataset.filter ? "all" : el.dataset.filter; renderMain(); });
  root.querySelectorAll("th[data-sort]").forEach((el) => el.onclick = () => {
    const k = el.dataset.sort; S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (k === "name" || k === "jti" ? 1 : -1) }; renderMain();
  });
  root.querySelectorAll("tr.row").forEach((tr) => tr.onclick = () => openToken(tr.dataset.jti, tr.dataset.email));
  $("#btn-issue", root).onclick = issueDialog;
}

// ---------------------------------------------------------------- token drawer
function emailText(t) {
  return `Hello ${t.name || ""},

Here is your personal TSM access token (valid until ${t.expires}). One token works for all TSM models.

1. In QGIS open TSM Panel > General Configuration, paste the token into "TSM Token" and click OK.
   (Or save it as a single line in:  %USERPROFILE%\\.tsm\\token.txt )

${t.token}

Token ID: ${t.jti}
Please keep this token to yourself. Contact us if you need it extended or replaced.

${S.data.contact || "TSM Model Development Team"}`;
}

// Other people on the same token id whose token still works (expired ones no longer matter).
const shared = (t) => S.data.tokens.filter((x) => x.jti === t.jti && x.email !== t.email && x.status !== "expired");
function openToken(jti, email) {
  const t = S.data.tokens.find((x) => x.jti === jti && (!email || x.email.toLowerCase() === email.toLowerCase()))
    || S.data.tokens.find((x) => x.jti === jti);
  if (!t) return;
  const st = displayStatus(t);
  const others = shared(t);
  const hist = S.data.audit.filter((a) => a.jti === jti && (!others.length || !a.email || a.email.toLowerCase() === t.email.toLowerCase())).slice().reverse();
  closeDrawer();
  const ov = document.createElement("div"); ov.className = "drawer-overlay"; ov.onclick = closeDrawer;
  const dr = document.createElement("aside"); dr.className = "drawer";
  dr.innerHTML = h`
    <div class="drawer-head">
      <div style="flex:1"><h2>${esc(t.name || t.email)}</h2><div class="muted">${esc(t.email)}</div></div>
      ${badge(st)}<button class="x" aria-label="Close">×</button>
    </div>
    <div class="drawer-body">
      ${others.length ? `<div class="banner warn"><b>Shared token ID.</b> ${esc(t.jti)} was also issued to ${others.map((o) => esc(o.name || o.email)).join(", ")}. Revoking it blocks all of them. To separate them, issue this person a new token and revoke the old one.</div>` : ""}
      <dl class="kv">
        <dt>Token ID</dt><dd class="mono">${esc(t.jti)}</dd>
        <dt>Models</dt><dd>${t.tool === "*" || !t.tool ? "All TSM models" : esc(t.tool)}</dd>
        <dt>Issued</dt><dd>${fmtDate(t.issued)} <span class="muted">by ${esc(t.issued_by || "?")}</span></dd>
        <dt>Expires</dt><dd>${fmtDate(t.expires)}</dd>
        ${t.modified_at ? `<dt>Last modified</dt><dd>${fmtWhen(t.modified_at)} <span class="muted">by ${esc(t.modified_by)}</span></dd>` : ""}
        ${t.revoked ? `<dt>Revoked</dt><dd>${fmtDate(t.revoked)} <span class="muted">by ${esc(t.revoked_by || "?")}</span></dd>` : ""}
        ${t.notes ? `<dt>Notes</dt><dd>${esc(t.notes)}</dd>` : ""}
      </dl>
      <div class="section-title">Token</div>
      <div class="tokenbox">${esc(t.token)}</div>
      <div class="copyrow">
        <button class="btn sm" data-copy="token">${ICON.copy} Copy token</button>
        <button class="btn sm" data-copy="email">${ICON.copy} Copy email message</button>
        <button class="btn sm ghost" data-copy="link">${ICON.copy} Copy link</button>
      </div>
      <div class="section-title">History</div>
      ${hist.length ? `<ul class="timeline">${hist.map((a) => `<li><span class="act ${esc(a.action)}">${esc(a.action)}</span> by <b>${esc(a.actor)}</b>
          <div class="t">${fmtWhen(a.time)}</div>${a.details ? `<div class="muted">${esc(a.details)}</div>` : ""}</li>`).join("")}</ul>`
        : `<p class="muted">No recorded activity yet (issued before this app${t.issued_by ? ` by ${esc(t.issued_by)}` : ""}).</p>`}
    </div>
    <div class="drawer-foot">
      <button class="btn" data-act="edit">Edit / extend</button>
      <div class="spacer"></div>
      ${t.revoked ? `<button class="btn" data-act="restore">Restore access</button>` : `<button class="btn danger" data-act="revoke">Revoke</button>`}
    </div>`;
  document.body.append(ov, dr);
  $(".x", dr).onclick = closeDrawer;
  dr.querySelector('[data-copy="token"]').onclick = () => copy(t.token, "Token copied");
  dr.querySelector('[data-copy="email"]').onclick = () => copy(emailText(t), "Email message copied");
  dr.querySelector('[data-copy="link"]').onclick = () => copy(`${location.origin}/#token=${encodeURIComponent(t.jti + (shared(t).length ? "|" + t.email : ""))}`, "Link copied");
  dr.querySelector('[data-act="edit"]').onclick = () => editDialog(t);
  const rv = dr.querySelector('[data-act="revoke"]'); if (rv) rv.onclick = () => revokeDialog(t);
  const rs = dr.querySelector('[data-act="restore"]'); if (rs) rs.onclick = () => restoreDialog(t);
}
function closeDrawer() { document.querySelectorAll(".drawer, .drawer-overlay").forEach((e) => e.remove()); }

// ---------------------------------------------------------------- dialogs
function modal({ title, body, okText = "Save", okClass = "primary", wide = false, onOk }) {
  const root = $("#modal-root");
  root.innerHTML = h`<div class="overlay"><div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
      <div class="modal-head"><h2>${title}</h2><button class="x" aria-label="Close">×</button></div>
      <div class="modal-body">${body}<div class="error-text hidden" id="m-err"></div></div>
      <div class="modal-foot"><button class="btn" data-cancel>Cancel</button>
        ${onOk ? `<button class="btn ${okClass}" data-ok>${okText}</button>` : ""}</div></div></div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".x").onclick = close;
  root.querySelector("[data-cancel]").onclick = close;
  root.querySelector(".overlay").onclick = (e) => { if (e.target.classList.contains("overlay")) close(); };
  const ok = root.querySelector("[data-ok]");
  if (ok) ok.onclick = async () => {
    const err = $("#m-err"); err.classList.add("hidden");
    ok.disabled = true; const t = ok.textContent; ok.textContent = "Working…";
    try { const keep = await onOk(root); if (!keep) close(); }
    catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    finally { if (ok.isConnected) { ok.disabled = false; ok.textContent = t; } }
  };
  const first = root.querySelector("input, textarea"); if (first) first.focus();
  return { root, close };
}
const val = (root, id) => (root.querySelector("#" + id).value || "").trim();

function manifestNote(r) {
  if (!r || !r.manifest) return;
  if (r.manifest.ok === false) toast("Saved, but the public manifest was not updated: " + r.manifest.error + " — use Data & sync › Sync manifest.", true);
}

function issueDialog() {
  modal({
    title: "Issue a token", okText: "Issue token",
    body: h`
      <div class="field"><label for="f-name">Full name</label><input id="f-name" autocomplete="off"></div>
      <div class="field"><label for="f-email">Email</label><input id="f-email" type="email" autocomplete="off"></div>
      <div class="field"><label for="f-exp">Expires</label><input id="f-exp" type="date" value="${esc(S.data.default_expiry)}" min="${esc(todayISO())}">
        <div class="help">The token stops working after this date. It can be extended later.</div></div>
      <div class="field"><label for="f-notes">Notes <span class="faint">(optional)</span></label><textarea id="f-notes" placeholder="Agency, project, request reference…"></textarea></div>
      <p class="faint" style="margin:0;font-size:12.5px">Next token ID: <span class="mono">${esc(S.data.next_jti)}</span> · valid for all TSM models.</p>`,
    onOk: async (root) => {
      const r = await api("issue", { name: val(root, "f-name"), email: val(root, "f-email"), expires: val(root, "f-exp"), notes: val(root, "f-notes") });
      manifestNote(r);
      await reload();
      showIssued(r.token, "Token issued");
      return true;
    },
  });
}

function showIssued(t, title) {
  modal({
    title: `${title} — ${esc(t.jti)}`, wide: true,
    body: h`<p>Send this token to <b>${esc(t.name)}</b> &lt;${esc(t.email)}&gt;. It is also saved in the token list.</p>
      <div class="tokenbox">${esc(t.token)}</div>
      <div class="copyrow"><button class="btn primary sm" id="c-tok">${ICON.copy} Copy token</button>
        <button class="btn sm" id="c-mail">${ICON.copy} Copy email message</button></div>`,
  });
  $("#c-tok").onclick = () => copy(t.token, "Token copied");
  $("#c-mail").onclick = () => copy(emailText(t), "Email message copied");
  $("#modal-root [data-cancel]").textContent = "Done";
}

function editDialog(t) {
  modal({
    title: `Edit ${esc(t.jti)}`, okText: "Save changes",
    body: h`
      <div class="field"><label for="f-name">Full name</label><input id="f-name" value="${esc(t.name)}"></div>
      <div class="field"><label for="f-email">Email</label><input id="f-email" type="email" value="${esc(t.email)}"></div>
      <div class="field"><label for="f-exp">Expires</label><input id="f-exp" type="date" value="${esc(t.expires)}" min="${esc(todayISO())}">
        <div class="help">Changing the email or expiry creates a <b>new token string</b> (same token ID). Send the new one to the user; the old string keeps working only until its original expiry.</div></div>
      <div class="field"><label for="f-notes">Notes</label><textarea id="f-notes">${esc(t.notes)}</textarea></div>`,
    onOk: async (root) => {
      const r = await api("update", { jti: t.jti, row_email: t.email, name: val(root, "f-name"), email: val(root, "f-email"), expires: val(root, "f-exp"), notes: val(root, "f-notes") });
      manifestNote(r);
      await reload();
      closeDrawer();
      if (r.resigned) showIssued(r.token, "New token string");
      else { toast("Saved"); openToken(t.jti, r.token.email); }
      return r.resigned;
    },
  });
}

function revokeDialog(t) {
  modal({
    title: `Revoke ${esc(t.jti)}?`, okText: "Revoke token", okClass: "danger solid",
    body: h`${shared(t).length ? `<div class="banner warn">This token ID is shared — ${shared(t).map((o) => esc(o.name || o.email)).join(", ")} will be blocked too.</div>` : ""}<p><b>${esc(t.name || t.email)}</b> will be blocked from running TSM models the next time a model checks online (within about 5 minutes of the manifest deploying). Offline users stop within 7 days.</p>
      <div class="field"><label for="f-reason">Reason <span class="faint">(recorded in the activity log)</span></label><input id="f-reason" placeholder="e.g. left the agency, duplicate token"></div>`,
    onOk: async (root) => {
      const r = await api("revoke", { jti: t.jti, row_email: t.email, reason: val(root, "f-reason") });
      manifestNote(r); await reload(); toast(`${t.jti} revoked`); openToken(t.jti, t.email); checkManifest();
    },
  });
}

function restoreDialog(t) {
  modal({
    title: `Restore ${esc(t.jti)}?`, okText: "Restore access",
    body: h`<p>Removes <span class="mono">${esc(t.jti)}</span> from the revoked list so <b>${esc(t.name || t.email)}</b> can run models again with the same token (until ${fmtDate(t.expires)}).</p>
      <div class="field"><label for="f-reason">Reason <span class="faint">(optional)</span></label><input id="f-reason"></div>`,
    onOk: async (root) => {
      const r = await api("restore", { jti: t.jti, row_email: t.email, reason: val(root, "f-reason") });
      manifestNote(r); await reload(); toast(`${t.jti} restored`); openToken(t.jti, t.email); checkManifest();
    },
  });
}

// ---------------------------------------------------------------- activity tab
function activityView() {
  const q = S.actSearch.toLowerCase();
  const rows = S.data.audit.slice().reverse().filter((a) =>
    (!S.actFilter || a.action === S.actFilter) &&
    (!q || [a.actor, a.jti, a.email, a.details, a.action].some((v) => (v || "").toLowerCase().includes(q))));
  const actions = [...new Set(S.data.audit.map((a) => a.action))].sort();
  return h`
    <div class="toolbar">
      <input class="search" id="a-search" placeholder="Search manager, token id, email, details…" value="${esc(S.actSearch)}">
      <select class="sel" id="a-filter"><option value="">All actions</option>
        ${actions.map((a) => `<option ${S.actFilter === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
      <div class="spacer"></div>
      <a class="btn" href="/api?op=download_audit">${ICON.down} Download activity CSV</a>
    </div>
    <div class="card table-wrap"><table class="grid">
      <thead><tr><th class="nosort">When</th><th class="nosort">Manager</th><th class="nosort">Action</th><th class="nosort">Token</th><th class="nosort hide-sm">Details</th></tr></thead>
      <tbody>${rows.length ? rows.map((a) => h`<tr class="${a.jti ? "row" : ""}" data-jti="${esc(a.jti)}" data-email="${esc(a.email)}">
        <td class="nowrap">${fmtWhen(a.time)}</td>
        <td class="nowrap">${esc(a.actor)}</td>
        <td><span class="act ${esc(a.action)}">${esc(a.action)}</span></td>
        <td>${a.jti ? `<span class="mono">${esc(a.jti)}</span><div class="by">${esc(a.email)}</div>` : ""}</td>
        <td class="hide-sm muted">${esc(a.details)}</td></tr>`).join("")
        : `<tr><td colspan="5" class="empty">No activity recorded yet.</td></tr>`}</tbody>
    </table></div>`;
}

function bindActivity(root) {
  const s = $("#a-search", root);
  s.oninput = () => { S.actSearch = s.value; const p = s.selectionStart; renderMain(); const n = $("#a-search"); n.focus(); n.setSelectionRange(p, p); };
  $("#a-filter", root).onchange = (e) => { S.actFilter = e.target.value; renderMain(); };
  root.querySelectorAll("tr.row").forEach((tr) => tr.onclick = () => { if (tr.dataset.jti) openToken(tr.dataset.jti, tr.dataset.email); });
}

// ---------------------------------------------------------------- managers tab
function mgrNums(login) {
  const L = (login || "").toLowerCase();
  const issued = S.data.tokens.filter((t) => (t.issued_by || "").toLowerCase() === L).length;
  const revoked = S.data.tokens.filter((t) => (t.revoked_by || "").toLowerCase() === L).length;
  const last = S.data.audit.filter((a) => (a.actor || "").toLowerCase() === L).map((a) => a.time).sort().pop();
  return `<div class="nums">Issued <b>${issued}</b> · Revoked <b>${revoked}</b>${last ? ` · last active ${fmtDate(last)}` : ""}</div>`;
}

function managersView() {
  const d = S.data, isOwner = S.me.role === "owner";
  const active = d.managers.filter((m) => !m.removed_at);
  const removed = d.managers.filter((m) => m.removed_at);
  const card = (login, name, meta, removable) => h`<div class="card mgr">
      <img src="${avatar(login)}" alt="">
      <div style="flex:1;min-width:0"><div class="nm">${esc(name || login)}</div>
        <div class="meta">@${esc(login)}</div><div class="meta">${meta}</div>${mgrNums(login)}</div>
      ${removable && isOwner ? `<button class="btn ghost sm danger" data-remove="${esc(login)}">Remove</button>` : ""}</div>`;
  return h`
    <div class="two-col">
      <div class="stack">
        <div>
          <div class="section-title" style="margin-top:0">Owner</div>
          <div class="mgr-grid">${d.owners.map((o) => card(o, o, "Owner · manages the manager list", false)).join("")}</div>
        </div>
        <div>
          <div class="section-title">Managers (${active.length})</div>
          ${active.length ? `<div class="mgr-grid">${active.map((m) => card(m.login, m.name, `Added ${fmtDate(m.added_at)} by ${esc(m.added_by)}`, true)).join("")}</div>`
            : `<div class="card empty">No managers yet.${isOwner ? " Add one with the form." : ""}</div>`}
        </div>
        ${removed.length ? `<div><div class="section-title">Former managers</div><div class="card table-wrap"><table class="grid">
          <thead><tr><th class="nosort">GitHub login</th><th class="nosort">Added</th><th class="nosort">Removed</th></tr></thead><tbody>
          ${removed.map((m) => `<tr><td>${esc(m.name || m.login)} <span class="muted">@${esc(m.login)}</span></td>
            <td>${fmtDate(m.added_at)} <span class="by">by ${esc(m.added_by)}</span></td>
            <td>${fmtDate(m.removed_at)} <span class="by">by ${esc(m.removed_by)}</span></td></tr>`).join("")}</tbody></table></div></div>` : ""}
      </div>
      <div class="card card-pad">
        <h3>Add a manager</h3>
        <p class="sub">Managers sign in with their own GitHub account and can issue, edit, extend, revoke and restore tokens and download the list. Every action is recorded under their GitHub login.</p>
        ${isOwner ? h`
          <div class="field"><label for="m-login">GitHub username</label><input id="m-login" placeholder="e.g. octocat" autocomplete="off"></div>
          <div class="field"><label for="m-name">Display name <span class="faint">(optional)</span></label><input id="m-name" placeholder="e.g. Jane Doe, District 4"></div>
          <button class="btn primary" id="m-add">${ICON.plus} Add manager</button>
          <div class="error-text hidden" id="m-err"></div>`
        : `<div class="banner info" style="margin:0">Only the owner can add or remove managers.</div>`}
      </div>
    </div>`;
}

function bindManagers(root) {
  const add = $("#m-add", root);
  if (add) add.onclick = async () => {
    const err = $("#m-err"); err.classList.add("hidden"); add.disabled = true;
    try { await api("manager_add", { login: val(root, "m-login"), name: val(root, "m-name") }); await reload(); toast("Manager added"); }
    catch (e) { err.textContent = e.message; err.classList.remove("hidden"); add.disabled = false; }
  };
  root.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => modal({
    title: `Remove manager ${esc(b.dataset.remove)}?`, okText: "Remove", okClass: "danger solid",
    body: `<p>They will lose access immediately. Tokens they issued stay valid and their history is kept.</p>`,
    onOk: async () => { await api("manager_remove", { login: b.dataset.remove }); await reload(); toast("Manager removed"); },
  }));
}

// ---------------------------------------------------------------- data tab
function dataView() {
  const m = S.manifest, isOwner = S.me.role === "owner", local = S.me.mode === "local";
  const li = (ok, text) => `<li><span class="pill ${ok === true ? "ok" : ok === false ? "bad" : "warn"}"><span class="dot"></span>${ok === true ? "OK" : ok === false ? "Mismatch" : "Pending"}</span>${text}</li>`;
  let status = `<p class="muted">Checking…</p>`;
  if (m && m.error) status = `<div class="banner bad">${esc(m.error)}</div>`;
  else if (m) {
    const ids = (a) => a && a.length ? `: <span class="mono">${a.map(esc).join(", ")}</span>` : "";
    status = `<ul class="sync-list">
      ${li(true, `Registry: <b>${m.registry_revoked.length}</b> revoked token(s)`)}
      ${m.repo_error ? li(false, `Manifest repo: ${esc(m.repo_error)}`) : li(!m.repo_missing.length, `Manifest file${local ? "" : " in GitHub"} ${m.repo_missing.length ? "is missing" + ids(m.repo_missing) : "lists all of them"}${m.repo_extra.length ? ` <span class="faint">(also lists ${m.repo_extra.length} id(s) not revoked here: ${m.repo_extra.map(esc).join(", ")})</span>` : ""}`)}
      ${m.live_error ? li(false, `Live manifest: ${esc(m.live_error)}`) : li(m.live_missing.length ? (m.repo_missing && m.repo_missing.length ? false : null) : true,
        `Live manifest (what the models read) ${m.live_missing.length ? "is not yet showing" + ids(m.live_missing) + " — Vercel usually deploys within a minute" : "is up to date"}`)}
    </ul>`;
  }
  return h`
    <div class="two-col">
      <div class="stack">
        <div class="card card-pad">
          <h3>Public manifest</h3>
          <p class="sub">Revoked token IDs are published to <span class="mono">${esc(S.me.manifest || "")}</span>, which Vercel serves at auth.tsm-nextgen.com. Models check it at start-up.</p>
          ${status}
          <button class="btn" id="d-check">${ICON.sync} Re-check</button>
          <button class="btn" id="d-sync">Sync manifest now</button>
        </div>
        ${local ? h`<div class="card card-pad">
          <h3>Publish local changes</h3>
          <p class="sub">You are using the <b>local copy</b>. Changes are saved to files on this PC (<span class="mono">${esc(S.me.registry)}</span>). When you are back online, publish them to GitHub (commit, pull, push for the registry and manifest repos).</p>
          <button class="btn primary" id="d-publish">Publish to GitHub</button>
          <pre class="out hidden" id="d-out"></pre></div>` : ""}
      </div>
      <div class="stack">
        <div class="card card-pad">
          <h3>Download</h3>
          <p class="sub">Full token list (including token strings and who issued, modified and revoked each one) and the activity log, as CSV.</p>
          <a class="btn" href="/api?op=download_tokens">${ICON.down} Tokens CSV</a>
          <a class="btn" href="/api?op=download_audit">${ICON.down} Activity CSV</a>
        </div>
        <div class="card card-pad">
          <h3>Replace token list from CSV</h3>
          <p class="sub">Upload a tokens CSV (this app's download, or the original <span class="mono">keys/issued_tokens.csv</span>) to <b>replace</b> the token list. Each token is checked against the TSM key; you'll see a preview before anything changes.</p>
          ${isOwner ? `<input type="file" id="d-file" accept=".csv,text/csv"><div id="d-preview"></div>`
            : `<div class="banner info" style="margin:0">Only the owner can replace the token list.</div>`}
        </div>
      </div>
    </div>`;
}

function bindData(root) {
  $("#d-check", root).onclick = () => { S.manifest = null; renderMain(); checkManifest(); };
  $("#d-sync", root).onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await api("manifest_sync", {}); toast(r.manifest.ok ? (r.manifest.changed ? "Manifest updated; Vercel is deploying" : "Manifest already in sync") : r.manifest.error, !r.manifest.ok); }
    catch (er) { toast(er.message, true); }
    checkManifest();
  };
  const pub = $("#d-publish", root);
  if (pub) pub.onclick = async () => {
    pub.disabled = true; const out = $("#d-out"); out.classList.remove("hidden"); out.textContent = "Publishing…";
    try { const r = await api("publish", {}); out.textContent = r.output || "Nothing to publish."; toast("Published"); }
    catch (e) { out.textContent = e.message; toast("Publish failed", true); }
    pub.disabled = false; checkManifest();
  };
  const f = $("#d-file", root);
  if (f) f.onchange = async () => {
    const file = f.files[0]; if (!file) return;
    const text = await file.text();
    const box = $("#d-preview"); box.innerHTML = `<p class="muted">Checking ${esc(file.name)}…</p>`;
    try {
      const { preview: p } = await api("import", { csv: text });
      const list = (a) => a.length ? `<span class="mono">${a.slice(0, 12).map(esc).join(", ")}${a.length > 12 ? ` … +${a.length - 12}` : ""}</span>` : "none";
      box.innerHTML = h`<div class="banner ${p.removed.length ? "warn" : "info"}" style="margin-top:12px">
          <b>${p.rows}</b> valid token(s) in ${esc(file.name)}.<br>
          New: ${list(p.added)}<br>Changed: ${list(p.changed)}<br>
          <b>Removed from the list</b>: ${list(p.removed)}
          ${p.problems.length ? `<ul>${p.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
        <button class="btn primary" id="d-apply" ${p.rows ? "" : "disabled"}>Replace token list</button>
        <button class="btn" id="d-cancel">Cancel</button>`;
      $("#d-cancel").onclick = () => { box.innerHTML = ""; f.value = ""; };
      $("#d-apply").onclick = async (e) => {
        e.target.disabled = true;
        try { const r = await api("import", { csv: text, apply: true }); manifestNote(r); await reload(); S.tab = "data"; renderMain(); toast("Token list replaced"); checkManifest(); }
        catch (er) { toast(er.message, true); e.target.disabled = false; }
      };
    } catch (e) { box.innerHTML = `<div class="banner bad" style="margin-top:12px">${esc(e.message)}</div>`; }
  };
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#modal-root").innerHTML = ""; closeDrawer(); } });
boot();
