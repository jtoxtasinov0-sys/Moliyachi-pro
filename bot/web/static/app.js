"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const state = {
  me: null,
  view: "home",
  period: "month",
  metric: "qty",
  dash: null,
  entries: { status: "", workerId: "", offset: 0, items: [] },
  workers: [],
  models: [],
};
const charts = {};

const CUR = { UZS: "so'm", USD: "$", KRW: "won" };
const STATUS = { pending: "Kutilmoqda", approved: "Tasdiqlangan", rejected: "Rad etilgan" };
const WD = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"];
const WD_LONG = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"];
const MON = ["Yan", "Fev", "Mar", "Apr", "May", "Iyn", "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek"];
const MON_LONG = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];
const PALETTE = ["#0f1f57", "#2b4fc9", "#5b7cf0", "#93aaf7", "#c6d3fb", "#e8972a", "#12a26b", "#9aa3bd"];

// --- Yordamchilar -----------------------------------------------------------

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtNum(n) {
  const v = Math.round(Number(n || 0) * 100) / 100;
  const [i, d] = String(Math.abs(v)).split(".");
  return (v < 0 ? "-" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (d ? "," + d : "");
}
function fmtShort(n) {
  const a = Math.abs(n);
  if (a >= 1e9) return fmtNum(n / 1e9).replace(/,(\d)\d*/, ",$1") + " mlrd";
  if (a >= 1e6) return fmtNum(n / 1e6).replace(/,(\d)\d*/, ",$1") + " mln";
  if (a >= 1e3) return fmtNum(n / 1e3).replace(/,(\d)\d*/, ",$1") + " ming";
  return fmtNum(n);
}
const money = (amt, cur) => `${fmtNum(amt)} ${CUR[cur] || cur || ""}`.trim();
function moneyList(pay) {
  const parts = Object.entries(pay || {}).filter(([, v]) => v).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : "";
}
// Asosiy valyuta: summasi eng katta bo'lgani (aks holda birinchi yoqilgani)
function mainCurrency(pay) {
  const entries = Object.entries(pay || {}).filter(([, v]) => v);
  if (!entries.length) return (state.me && state.me.currencies[0]) || "UZS";
  entries.sort((a, b) => b[1] - a[1]);
  return entries[0][0];
}
const parseDate = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

function dateLabel(iso) {
  const d = parseDate(iso);
  const today = parseDate(state.dash ? state.dash.today : new Date().toISOString().slice(0, 10));
  const diff = Math.round((today - d) / 864e5);
  const base = `${d.getDate()}-${MON_LONG[d.getMonth()]}`;
  if (diff === 0) return `Bugun · ${base}`;
  if (diff === 1) return `Kecha · ${base}`;
  return `${base}, ${WD_LONG[(d.getDay() + 6) % 7]}`;
}

function haptic(kind) {
  try {
    if (!tg || !tg.HapticFeedback) return;
    if (kind === "ok" || kind === "error" || kind === "warning") tg.HapticFeedback.notificationOccurred(kind === "ok" ? "success" : kind);
    else tg.HapticFeedback.selectionChanged();
  } catch (_) { /* eski Telegram */ }
}

let toastTimer;
function toast(text, isErr) {
  const el = $("toast");
  el.textContent = text;
  el.className = "toast" + (isErr ? " err" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
  haptic(isErr ? "error" : "ok");
}

function confirmBox(text) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) tg.showConfirm(text, resolve);
    else resolve(window.confirm(text));
  });
}

async function api(path, opts = {}) {
  const headers = { "X-Telegram-Init-Data": (tg && tg.initData) || "" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* bo'sh javob */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Xato (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function fail(err) {
  console.error(err);
  toast(err.message || "Xatolik yuz berdi", true);
}

// --- Navigatsiya -------------------------------------------------------------

function show(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((el) => (el.hidden = el.id !== `view-${view}`));
  document.querySelectorAll("#nav > button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  window.scrollTo({ top: 0 });
  haptic();
  if (view === "home") loadDashboard();
  if (view === "entries") loadEntries(true);
  if (view === "workers") loadWorkers();
  if (view === "models") loadModels();
}

// --- Asosiy (dashboard) ------------------------------------------------------

async function loadDashboard() {
  try {
    state.dash = await api(`/api/dashboard?period=${state.period}`);
    renderDashboard();
  } catch (e) { fail(e); }
}

function renderDashboard() {
  const d = state.dash;
  const isOwner = state.me.is_owner;
  const cur = mainCurrency(d.totals.pay);
  const amount = d.totals.pay[cur] || 0;

  $("heroLabel").textContent = isOwner ? "Ish haqi fondi" : "Ish haqingiz";
  $("heroAmount").innerHTML = `${fmtNum(amount)}<small>${esc(CUR[cur] || cur)}</small>`;
  const others = Object.entries(d.totals.pay).filter(([c, v]) => c !== cur && v).map(([c, v]) => "+ " + money(v, c));
  $("heroExtra").textContent = others.join("  ");
  $("heroQty").textContent = fmtNum(d.totals.qty);
  $("heroDays").textContent = d.totals.days;
  $("heroAvg").textContent = d.totals.days ? fmtNum(Math.round(d.totals.qty / d.totals.days)) : "0";

  // O'tgan davr bilan taqqoslash (shu kungacha)
  const prevLabel = { day: "kechaga", week: "o'tgan haftaga", month: "o'tgan oyga", year: "o'tgan yilga" }[d.period];
  const delta = $("heroDelta");
  if (d.prev.qty > 0) {
    const pct = Math.round(((d.totals.qty - d.prev.qty) / d.prev.qty) * 100);
    delta.className = "delta " + (pct >= 0 ? "up" : "down");
    delta.innerHTML = `<b>${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct)}%</b> ${prevLabel} nisbatan`;
  } else if (d.totals.qty > 0) {
    delta.className = "delta up";
    delta.innerHTML = `<b>▲ yangi</b> ${prevLabel} nisbatan`;
  } else {
    delta.innerHTML = "";
  }
  const s = parseDate(d.start), e = parseDate(d.end);
  $("heroRange").textContent = d.start === d.end
    ? `${s.getDate()}-${MON_LONG[s.getMonth()]} ${s.getFullYear()}`
    : `${s.getDate()}-${MON_LONG[s.getMonth()]} — ${e.getDate()}-${MON_LONG[e.getMonth()]} ${e.getFullYear()}`;

  // Tasdiq kutayotganlar
  $("pendingCard").hidden = !(isOwner && d.pending > 0);
  $("pendingCount").textContent = d.pending;

  renderTrend();
  renderModels();
  if (isOwner) renderRanking(cur);
}

function renderTrend() {
  const d = state.dash;
  const points = d.series.points;
  const isMonth = d.series.bucket === "month";
  const cur = mainCurrency(d.totals.pay);
  const metric = state.metric;
  const values = points.map((p) => (metric === "qty" ? p.qty : p.pay[cur] || 0));
  const labels = points.map((p) => {
    if (isMonth) return MON[Number(p.key.slice(5, 7)) - 1];
    const dt = parseDate(p.key);
    return d.period === "month" ? String(dt.getDate()) : WD[(dt.getDay() + 6) % 7];
  });
  const todayKey = isMonth ? d.today.slice(0, 7) : d.today;
  const future = points.map((p) => p.key > todayKey);

  $("chartTitle").textContent = { day: "Oxirgi 7 kun", week: "Hafta kunlari", month: "Kunlar bo'yicha", year: "Oylar bo'yicha" }[d.period];
  $("trendEmpty").hidden = values.some((v) => v > 0);

  const ctx = $("trendChart").getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 220);
  grad.addColorStop(0, "#3d63e0");
  grad.addColorStop(1, "#0f1f57");
  const colors = points.map((p, i) => (p.key === todayKey ? "#e8972a" : future[i] ? "#dfe5f4" : grad));

  if (charts.trend) charts.trend.destroy();
  charts.trend = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 8, borderSkipped: false, maxBarThickness: 26 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0a1640",
          padding: 10,
          cornerRadius: 12,
          displayColors: false,
          titleFont: { family: "Manrope", weight: "700" },
          bodyFont: { family: "Manrope", weight: "700", size: 14 },
          callbacks: {
            title: (items) => {
              const p = points[items[0].dataIndex];
              if (isMonth) return MON_LONG[Number(p.key.slice(5, 7)) - 1];
              const dt = parseDate(p.key);
              return `${dt.getDate()}-${MON_LONG[dt.getMonth()]}, ${WD_LONG[(dt.getDay() + 6) % 7]}`;
            },
            label: (item) => {
              const p = points[item.dataIndex];
              const pay = moneyList(p.pay);
              return metric === "qty" ? `${fmtNum(p.qty)} dona${pay ? " · " + pay : ""}` : money(item.raw, cur);
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: "#6b7591", font: { family: "Manrope", weight: "600", size: 11 }, autoSkip: true, maxRotation: 0 } },
        y: { beginAtZero: true, border: { display: false }, grid: { color: "#eef1f8" }, ticks: { color: "#9aa3bd", font: { family: "Manrope", size: 11 }, maxTicksLimit: 5, precision: 0, callback: (v) => fmtShort(v) } },
      },
    },
  });
}

function renderModels() {
  const items = state.dash.by_model;
  const total = items.reduce((s, m) => s + m.qty, 0);
  $("donutTotal").textContent = fmtShort(total);
  $("modelCount").textContent = items.length ? `${items.length} ta model` : "";

  // 6 tadan ko'p bo'lsa qolganlari «Boshqa»
  let rows = items.slice(0, 6);
  if (items.length > 6) rows.push({ name: "Boshqa", qty: items.slice(6).reduce((s, m) => s + m.qty, 0) });
  const data = rows.length ? rows.map((r) => r.qty) : [1];
  const colors = rows.length ? rows.map((_, i) => PALETTE[i % PALETTE.length]) : ["#eef1f8"];

  if (charts.model) charts.model.destroy();
  charts.model = new Chart($("modelChart"), {
    type: "doughnut",
    data: { labels: rows.map((r) => r.name), datasets: [{ data, backgroundColor: colors, borderWidth: 3, borderColor: "#fff", hoverOffset: 6 }] },
    options: {
      cutout: "70%",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: rows.length > 0,
          backgroundColor: "#0a1640", cornerRadius: 12, padding: 10,
          bodyFont: { family: "Manrope", weight: "700" },
          callbacks: { label: (item) => ` ${item.label}: ${fmtNum(item.raw)} dona` },
        },
      },
    },
  });

  $("modelLegend").innerHTML = rows.length
    ? rows.map((r, i) => `<li><span class="dot" style="background:${colors[i]}"></span><span class="name">${esc(r.name)}</span><span>${fmtNum(r.qty)}</span><span class="pct">${Math.round((r.qty / total) * 100)}%</span></li>`).join("")
    : `<li class="muted">Ma'lumot yo'q</li>`;
}

function renderRanking(cur) {
  const items = state.dash.by_worker || [];
  $("workersCard").hidden = false;
  $("workerCount").textContent = items.length ? `${items.length} ta ishchi` : "";
  const max = Math.max(1, ...items.map((w) => w.qty));
  const medals = ["🥇", "🥈", "🥉"];
  $("workerRank").innerHTML = items.length
    ? items.map((w, i) => `
      <li>
        <div class="avatar">${esc(initials(w.name))}${i < 3 ? `<span class="medal">${medals[i]}</span>` : ""}</div>
        <div><div class="name">${esc(w.name)}</div><div class="bar"><i style="width:${Math.max(4, (w.qty / max) * 100)}%"></i></div></div>
        <div class="val">${fmtNum(w.qty)} ta<small>${esc(moneyList(w.pay) || "—")}</small></div>
      </li>`).join("")
    : `<li class="muted" style="display:block">Bu davrda ish topshirilmagan</li>`;
}

// --- Yozuvlar ----------------------------------------------------------------

async function loadEntries(reset) {
  const en = state.entries;
  if (reset) {
    en.offset = 0;
    en.items = [];
    $("entryList").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  }
  const q = new URLSearchParams({ limit: 30, offset: en.offset });
  if (en.status) q.set("status", en.status);
  if (en.workerId) q.set("worker_id", en.workerId);
  try {
    const data = await api(`/api/entries?${q}`);
    en.items = en.items.concat(data.items);
    en.offset += data.items.length;
    $("moreBtn").hidden = !data.has_more;
    renderEntries();
  } catch (e) { fail(e); }
}

function renderEntries() {
  const items = state.entries.items;
  if (!items.length) {
    $("entryList").innerHTML = `<div class="empty"><span class="big">🗂</span>Yozuvlar topilmadi</div>`;
    return;
  }
  let html = "";
  let lastDate = null;
  for (const e of items) {
    if (e.date !== lastDate) {
      html += `<div class="date-head">${esc(dateLabel(e.date))}</div>`;
      lastDate = e.date;
    }
    html += entryCard(e);
  }
  $("entryList").innerHTML = html;
}

function entryCard(e) {
  const isOwner = state.me.is_owner;
  const total = e.total != null ? money(e.total, e.currency) : "";
  const unit = e.unit_price != null ? money(e.unit_price, e.currency) : "";
  let actions = "";
  if (isOwner && e.status === "pending") {
    actions += `<button class="act ok" data-act="approve" data-id="${e.id}">✓ Tasdiqlash</button><button class="act no" data-act="reject" data-id="${e.id}">Rad etish</button>`;
  }
  actions += `<button class="act del" data-act="delete" data-id="${e.id}" aria-label="O'chirish">🗑</button>`;
  return `
    <div class="entry" id="entry-${e.id}">
      <div class="entry-main">
        <div class="model-badge">${esc(e.model)}</div>
        <div class="entry-info">
          <b>${fmtNum(e.qty)} dona</b>
          ${isOwner ? `<small>👷 ${esc(e.worker.name)}</small>` : ""}<small>${unit ? esc(unit) + " / dona" : "narx yo'q"}</small>
        </div>
        <div class="entry-right">
          <b>${esc(total)}</b>
          <span class="status ${e.status}">${STATUS[e.status] || e.status}</span>
        </div>
      </div>
      ${e.raw_text ? `<div class="entry-raw">«${esc(e.raw_text.length > 160 ? e.raw_text.slice(0, 160) + "…" : e.raw_text)}»</div>` : ""}
      <div class="entry-actions">${actions}</div>
    </div>`;
}

async function entryAction(act, id) {
  try {
    if (act === "delete") {
      if (!(await confirmBox(`#${id} yozuvni o'chirasizmi?`))) return;
      await api(`/api/entries/${id}`, { method: "DELETE" });
      state.entries.items = state.entries.items.filter((e) => e.id !== id);
      toast("O'chirildi");
    } else {
      const updated = await api(`/api/entries/${id}/${act}`, { method: "POST" });
      const keep = !state.entries.status || state.entries.status === updated.status;
      state.entries.items = state.entries.items.flatMap((e) => (e.id === id ? (keep ? [updated] : []) : [e]));
      toast(act === "approve" ? "Tasdiqlandi" : "Rad etildi");
    }
    renderEntries();
  } catch (e) { fail(e); }
}

async function fillWorkerFilter() {
  if (!state.me.is_owner) return;
  try {
    if (!state.workers.length) state.workers = (await api("/api/workers")).items;
    const sel = $("workerFilter");
    sel.innerHTML = `<option value="">👷 Barcha ishchilar</option>` + state.workers.map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join("");
    sel.value = state.entries.workerId;
    sel.hidden = state.workers.length === 0;
  } catch (e) { fail(e); }
}

// --- Ishchilar ---------------------------------------------------------------

async function loadWorkers() {
  if (state.workers.length) renderWorkers();
  else $("workerList").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    state.workers = (await api("/api/workers")).items;
    renderWorkers();
  } catch (e) { fail(e); }
}

function renderWorkers() {
  const items = state.workers;
  if (!items.length) {
    $("workerList").innerHTML = `<div class="empty"><span class="big">👷</span>Hali ishchi yo'q.<br>«+ Qo'shish» tugmasini bosing.</div>`;
    return;
  }
  $("workerList").innerHTML = items.map((w) => {
    const tag = !w.is_active ? '<span class="tag off">O\'chirilgan</span>'
      : w.linked ? '<span class="tag ok">Ulangan</span>' : '<span class="tag wait">Havola kutilmoqda</span>';
    return `
      <button class="row-card${w.is_active ? "" : " off"}" data-worker="${w.id}">
        <div class="avatar">${esc(initials(w.name))}</div>
        <div class="info"><b>${esc(w.name)}</b>${tag}</div>
        <div class="side">${fmtNum(w.month_qty)} ta<small>${esc(moneyList(w.month_pay) || "shu oy")}</small></div>
      </button>`;
  }).join("");
}

function shareInvite(w) {
  const text = `${w.name}, Moliyachi botiga ulanish uchun havolani bosing:`;
  const url = `https://t.me/share/url?url=${encodeURIComponent(w.invite_link)}&text=${encodeURIComponent(text)}`;
  if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
  else window.open(url, "_blank");
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Nusxalandi");
  } catch (_) {
    toast("Nusxalab bo'lmadi — havolani bosib turing", true);
  }
}

function workerSheet(w) {
  const invite = w.invite_link
    ? `<div class="field"><label>Taklif havolasi (bir martalik)</label><div class="link-box">${esc(w.invite_link)}</div></div>
       <button class="primary" id="wShare">📤 Ishchiga yuborish</button>
       <button class="secondary" id="wCopy">Nusxalash</button>`
    : w.is_active ? `<button class="secondary" id="wInvite">🔗 Yangi taklif havolasi</button>` : "";
  openSheet(`
    <h3>${esc(w.name)}</h3>
    <div class="stat-pair">
      <div><b>${fmtNum(w.month_qty)} ta</b><small>shu oy topshirgan</small></div>
      <div><b>${esc(moneyList(w.month_pay) || "—")}</b><small>shu oy ish haqi</small></div>
    </div>
    ${invite}
    <button class="${w.is_active ? "danger" : "secondary"}" id="wToggle">${w.is_active ? "⛔ O'chirish (botdan foydalana olmaydi)" : "✅ Qayta faollashtirish"}</button>
    <button class="secondary" id="wEntries">🗂 Yozuvlarini ko'rish</button>
  `);
  if ($("wShare")) $("wShare").onclick = () => shareInvite(w);
  if ($("wCopy")) $("wCopy").onclick = () => copyText(w.invite_link);
  if ($("wInvite")) $("wInvite").onclick = async () => {
    try {
      const upd = await api(`/api/workers/${w.id}/invite`, { method: "POST" });
      replaceWorker(upd);
      workerSheet(upd);
    } catch (e) { fail(e); }
  };
  $("wToggle").onclick = async () => {
    if (w.is_active && !(await confirmBox(`${w.name} o'chirilsinmi? U botdan foydalana olmaydi.`))) return;
    try {
      const upd = await api(`/api/workers/${w.id}/toggle`, { method: "POST" });
      replaceWorker(upd);
      closeSheet();
      toast(upd.is_active ? "Faollashtirildi" : "O'chirildi");
    } catch (e) { fail(e); }
  };
  $("wEntries").onclick = () => {
    closeSheet();
    state.entries.workerId = String(w.id);
    state.entries.status = "";
    syncChips();
    show("entries");
  };
}

function replaceWorker(upd) {
  state.workers = state.workers.map((x) => (x.id === upd.id ? upd : x));
  renderWorkers();
}

function addWorkerSheet() {
  openSheet(`
    <h3>Yangi ishchi</h3>
    <div class="field"><label>Ismi</label><input class="input" id="wName" maxlength="100" placeholder="Masalan: Alisher" autocomplete="off"></div>
    <button class="primary" id="wSave">Qo'shish</button>
  `);
  const input = $("wName");
  setTimeout(() => input.focus(), 250);
  $("wSave").onclick = async () => {
    const name = input.value.trim();
    if (!name) return input.focus();
    $("wSave").disabled = true;
    try {
      const w = await api("/api/workers", { method: "POST", body: { name } });
      state.workers.unshift(w);
      renderWorkers();
      toast("Qo'shildi — havolani ishchiga yuboring");
      workerSheet(w);
    } catch (e) { fail(e); $("wSave").disabled = false; }
  };
}

// --- Modellar ----------------------------------------------------------------

async function loadModels() {
  if (state.models.length) renderModelList();
  else $("modelList").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    state.models = (await api("/api/models")).items;
    renderModelList();
  } catch (e) { fail(e); }
}

function renderModelList() {
  const items = state.models;
  if (!items.length) {
    $("modelList").innerHTML = `<div class="empty"><span class="big">🧵</span>Hali model yo'q${state.me.is_owner ? ".<br>«+ Qo'shish» tugmasini bosing." : ""}</div>`;
    return;
  }
  $("modelList").innerHTML = items.map((m) => `
    <${state.me.is_owner ? "button" : "div"} class="row-card${m.is_active ? "" : " off"}" data-model="${m.id}">
      <div class="model-badge icon">🧵</div>
      <div class="info"><b>${esc(m.name)}</b>${m.is_active ? "" : '<span class="tag off">O\'chirilgan</span>'}</div>
      <div class="side">${m.price != null ? esc(money(m.price, m.currency)) : '<span class="muted">narx yo\'q</span>'}<small>bir dona</small></div>
    </${state.me.is_owner ? "button" : "div"}>`).join("");
}

function modelSheet(m) {
  const isNew = !m;
  m = m || { name: "", price: null, currency: state.me.currencies[0], is_active: true };
  const curOptions = state.me.currencies.concat(state.me.currencies.includes(m.currency) ? [] : [m.currency])
    .map((c) => `<option value="${c}" ${c === m.currency ? "selected" : ""}>${CUR[c] || c}</option>`).join("");
  openSheet(`
    <h3>${isNew ? "Yangi model" : esc(m.name)}</h3>
    <div class="field"><label>Model nomi</label><input class="input" id="mName" maxlength="100" value="${esc(m.name)}" placeholder="Masalan: A-12" autocomplete="off"></div>
    <div class="field"><label>Bir dona uchun ish haqi</label>
      <div class="field-row">
        <input class="input" id="mPrice" inputmode="decimal" value="${m.price != null ? m.price : ""}" placeholder="Masalan: 5000">
        <select class="select" id="mCur" style="margin:0">${curOptions}</select>
      </div>
    </div>
    ${isNew ? "" : `<label class="switch-row"><div>Faol<small>O'chirilgan model yangi yozuvlarda taklif qilinmaydi</small></div><span class="switch"><input type="checkbox" id="mActive" ${m.is_active ? "checked" : ""}><i></i></span></label>`}
    <button class="primary" id="mSave">${isNew ? "Qo'shish" : "Saqlash"}</button>
    ${isNew ? "" : '<p class="muted small" style="margin-top:12px">Yangi narx shu paytdan keyingi yozuvlarga qo\'llanadi.</p>'}
  `);
  if (isNew) setTimeout(() => $("mName").focus(), 250);
  $("mSave").onclick = async () => {
    const body = { name: $("mName").value.trim(), price: $("mPrice").value.trim() || null, currency: $("mCur").value };
    if (!body.name) return $("mName").focus();
    if (!isNew) body.is_active = $("mActive").checked;
    $("mSave").disabled = true;
    try {
      const saved = isNew
        ? await api("/api/models", { method: "POST", body })
        : await api(`/api/models/${m.id}`, { method: "PATCH", body });
      state.models = isNew ? state.models.concat(saved).sort((a, b) => a.name.localeCompare(b.name)) : state.models.map((x) => (x.id === saved.id ? saved : x));
      renderModelList();
      closeSheet();
      toast("Saqlandi");
    } catch (e) { fail(e); $("mSave").disabled = false; }
  };
}

// --- Sozlamalar --------------------------------------------------------------

function settingsSheet() {
  const me = state.me;
  const curRows = ["UZS", "USD", "KRW"].map((c) => `
    <label class="switch-row"><div>${c} <span class="muted">(${CUR[c]})</span></div>
      <span class="switch"><input type="checkbox" data-cur="${c}" ${me.currencies.includes(c) ? "checked" : ""}><i></i></span></label>`).join("");
  openSheet(`
    <h3>Sozlamalar</h3>
    <label class="switch-row"><div>Ishchi yozuvlarini tasdiqlash<small>Yoqilgan bo'lsa, ishchi kiritgani siz tasdiqlagandan keyin hisobotga tushadi</small></div>
      <span class="switch"><input type="checkbox" id="sApproval" ${me.require_approval ? "checked" : ""}><i></i></span></label>
    <div class="field"><label>Valyutalar</label></div>
    ${curRows}
  `);
  const save = async (body, input) => {
    try {
      const res = await api("/api/settings", { method: "PATCH", body });
      Object.assign(state.me, res);
      haptic();
    } catch (e) {
      fail(e);
      if (input) input.checked = !input.checked;
    }
  };
  $("sApproval").onchange = (ev) => save({ require_approval: ev.target.checked }, ev.target);
  document.querySelectorAll("[data-cur]").forEach((inp) => {
    inp.onchange = () => {
      const cur = [...document.querySelectorAll("[data-cur]")].filter((x) => x.checked).map((x) => x.dataset.cur);
      save({ currencies: cur }, inp);
    };
  });
}

// --- Pastki oyna -------------------------------------------------------------

function openSheet(html) {
  $("sheetBody").innerHTML = html;
  $("sheet").hidden = false;
  $("sheetBackdrop").hidden = false;
  if (tg && tg.BackButton) tg.BackButton.show();
}
function closeSheet() {
  $("sheet").hidden = true;
  $("sheetBackdrop").hidden = true;
  if (tg && tg.BackButton) tg.BackButton.hide();
}

function syncChips() {
  document.querySelectorAll("#statusChips button").forEach((b) => b.classList.toggle("on", b.dataset.status === state.entries.status));
}

// --- Boshlash ----------------------------------------------------------------

function bind() {
  document.querySelectorAll("#nav > button").forEach((b) => (b.onclick = () => show(b.dataset.view)));

  $("periodSeg").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.period === state.period) return;
    state.period = b.dataset.period;
    document.querySelectorAll("#periodSeg button").forEach((x) => x.classList.toggle("on", x === b));
    haptic();
    loadDashboard();
  };
  $("metricSeg").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.metric === state.metric) return;
    state.metric = b.dataset.metric;
    document.querySelectorAll("#metricSeg button").forEach((x) => x.classList.toggle("on", x === b));
    haptic();
    renderTrend();
  };
  $("pendingCard").onclick = () => {
    state.entries.status = "pending";
    state.entries.workerId = "";
    syncChips();
    show("entries");
  };

  $("statusChips").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    state.entries.status = b.dataset.status;
    syncChips();
    haptic();
    loadEntries(true);
  };
  $("workerFilter").onchange = (ev) => {
    state.entries.workerId = ev.target.value;
    loadEntries(true);
  };
  $("moreBtn").onclick = () => loadEntries(false);
  $("entryList").onclick = (ev) => {
    const b = ev.target.closest("[data-act]");
    if (b) entryAction(b.dataset.act, Number(b.dataset.id));
  };

  $("workerList").onclick = (ev) => {
    const b = ev.target.closest("[data-worker]");
    if (!b) return;
    const w = state.workers.find((x) => x.id === Number(b.dataset.worker));
    if (w) workerSheet(w);
  };
  $("addWorkerBtn").onclick = addWorkerSheet;

  $("modelList").onclick = (ev) => {
    if (!state.me.is_owner) return;
    const b = ev.target.closest("[data-model]");
    if (!b) return;
    const m = state.models.find((x) => x.id === Number(b.dataset.model));
    if (m) modelSheet(m);
  };
  $("addModelBtn").onclick = () => modelSheet(null);
  $("settingsBtn").onclick = settingsSheet;
  $("sheetBackdrop").onclick = closeSheet;

  // O'rtadagi tugma: chatga qaytib ovozli xabar yuborish
  $("fab").onclick = () => {
    haptic("ok");
    if (tg && tg.initData) tg.close();
    else toast("Ovozli xabarni bot chatida yuboring");
  };

  if (tg && tg.BackButton) tg.BackButton.onClick(closeSheet);
}

function fatal(text) {
  const el = $("fatal");
  el.innerHTML = `<div class="big">🔒</div><div>${esc(text)}</div>`;
  el.hidden = false;
}

async function init() {
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#eef2fb");
      tg.setBackgroundColor("#eef2fb");
      if (tg.setBottomBarColor) tg.setBottomBarColor("#eef2fb");
    } catch (_) { /* eski Telegram */ }
  }
  bind();
  try {
    state.me = await api("/api/me");
  } catch (e) {
    fatal(e.status === 401 || e.status === 403 ? e.message : "Serverga ulanib bo'lmadi. Keyinroq qayta oching.");
    return;
  }
  const me = state.me;
  $("hello").textContent = `Salom, ${me.name.split(" ")[0]}!`;
  $("settingsBtn").hidden = !me.is_owner;
  $("addModelBtn").hidden = !me.is_owner;
  $("navWorkers").hidden = !me.is_owner;
  $("navHelp").hidden = me.is_owner;
  if (!me.is_owner) {
    $("navModelsLabel").textContent = "Narxlar";
    $("modelsTitle").textContent = "Narxlar";
    // Ishchida «Ishchilar» o'rniga «Narxlar» va «Yordam»
    const nav = $("nav");
    nav.insertBefore(document.querySelector('[data-view="models"]'), $("navWorkers"));
  }
  await fillWorkerFilter();
  show("home");
}

init();
