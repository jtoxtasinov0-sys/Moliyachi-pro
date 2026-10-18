"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const state = {
  me: null,
  view: "home",
  period: "month",
  metric: "qty",
  dash: null,
  // type: "" — hammasi, expense | income | debt | work (ishchida doim work)
  entries: { type: "", status: "", workerId: "", offset: 0, items: [] },
  home: { personal: null, feed: [] },
  workers: [],
  models: [],
  jobs: [],
  wallet: { tab: "money", period: "month", data: null, entries: { offset: 0, items: [] }, debts: null },
  advice: null,
  // home — bosh sahifadagi qisqa ko'rinish, data — «Mijozlar» bo'limi
  clients: { period: "month", data: null, home: null, byId: {}, feed: { offset: 0, items: [] } },
};
const charts = {};

const CUR = { UZS: "so'm", USD: "$", KRW: "won" };
const STATUS = { pending: "Kutilmoqda", approved: "Tasdiqlangan", rejected: "Rad etilgan" };
const WD = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"];
const WD_LONG = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"];
const MON = ["Yan", "Fev", "Mar", "Apr", "May", "Iyn", "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek"];
const MON_LONG = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];
const PALETTE = ["#0f1f57", "#2b4fc9", "#5b7cf0", "#93aaf7", "#c6d3fb", "#e8972a", "#12a26b", "#9aa3bd"];
const CHART_TITLES = { day: "Oxirgi 7 kun", week: "Hafta kunlari", month: "Kunlar bo'yicha", year: "Oylar bo'yicha" };
const PREV_LABELS = { day: "kechaga", week: "o'tgan haftaga", month: "o'tgan oyga", year: "o'tgan yilga" };
const SKELETONS = '<div class="skeleton"></div><div class="skeleton"></div>';

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

function rangeLabel(startIso, endIso) {
  const s = parseDate(startIso), e = parseDate(endIso);
  return startIso === endIso
    ? `${s.getDate()}-${MON_LONG[s.getMonth()]} ${s.getFullYear()}`
    : `${s.getDate()}-${MON_LONG[s.getMonth()]} — ${e.getDate()}-${MON_LONG[e.getMonth()]} ${e.getFullYear()}`;
}

function dateLabel(iso) {
  const d = parseDate(iso);
  const todayIso = (state.dash && state.dash.today) || (state.wallet.data && state.wallet.data.today);
  const today = todayIso ? parseDate(todayIso) : new Date(new Date().toDateString());
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

// Brauzerda: botning /web buyrug'i bergan shaxsiy kalit (?key=...) saqlab qo'yiladi.
// Kalit manzilda ham qoladi: iPhone'da «Ekranga qo'shish» qilingan ilova Safari xotirasini
// ko'rmaydi va aynan shu manzil bilan ochiladi. Server kalitni o'zi uzaytirib turadi.
const WEB_KEY = "moliyachi_web_key";
const keyExpiry = (k) => Number((k || "").split(".")[1]) || 0;
function rememberKey(key) {
  try { localStorage.setItem(WEB_KEY, key); } catch (_) { /* xotira yopiq */ }
  const params = new URLSearchParams(location.search);
  if (params.get("key") === key) return;
  params.set("key", key);
  history.replaceState(null, "", location.pathname + "?" + params.toString() + location.hash);
}
function webKey() {
  const fromUrl = new URLSearchParams(location.search).get("key") || "";
  let saved = "";
  try { saved = localStorage.getItem(WEB_KEY) || ""; } catch (_) { /* xotira yopiq */ }
  // Ikkalasidan qaysi biri kechroq tugasa — o'shani olamiz
  const key = keyExpiry(saved) > keyExpiry(fromUrl) ? saved : fromUrl;
  if (key) rememberKey(key);
  return key;
}
const inTelegram = Boolean(tg && tg.initData);
let browserKey = inTelegram ? "" : webKey();

function authHeaders() {
  const headers = { "X-Telegram-Init-Data": (tg && tg.initData) || "" };
  if (browserKey) headers["X-Web-Token"] = browserKey;
  return headers;
}

async function api(path, opts = {}) {
  const headers = authHeaders();
  let body;
  if (opts.blob) {
    headers["Content-Type"] = opts.blob.type.split(";")[0];
    body = opts.blob;
  } else if (opts.form) {
    body = opts.form; // multipart: Content-Type ni brauzer o'zi qo'yadi
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, { method: opts.method || "GET", headers, body });
  const renewed = res.headers.get("X-Web-Token-New");
  if (renewed && browserKey) { browserKey = renewed; rememberKey(renewed); }
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
  // Egada «Modellar» — «Jamoa» bo'limining ichida
  const navView = view === "models" && state.me && state.me.is_owner ? "workers" : view;
  document.querySelectorAll(".view").forEach((el) => (el.hidden = el.id !== `view-${view}`));
  document.querySelectorAll("#nav > button").forEach((b) => b.classList.toggle("on", b.dataset.view === navView));
  window.scrollTo({ top: 0 });
  haptic();
  if (view === "home") loadDashboard();
  if (view === "entries") loadEntries(true);
  if (view === "wallet") loadWallet();
  if (view === "clients") loadClients();
  if (view === "workers") loadWorkers();
  if (view === "models") loadModels();
}

// --- Grafiklar ---------------------------------------------------------------

const TOOLTIP = {
  backgroundColor: "#0a1640",
  padding: 10,
  cornerRadius: 12,
  displayColors: false,
  titleFont: { family: "Manrope", weight: "700" },
  bodyFont: { family: "Manrope", weight: "700", size: 14 },
};

// Ustunli grafik: bugungi ustun — to'q sariq, kelajak — och kulrang
function drawBars(key, canvasId, points, values, opts) {
  const isMonth = opts.bucket === "month";
  const labels = points.map((p) => {
    if (isMonth) return MON[Number(p.key.slice(5, 7)) - 1];
    const dt = parseDate(p.key);
    return opts.period === "month" ? String(dt.getDate()) : WD[(dt.getDay() + 6) % 7];
  });
  const todayKey = isMonth ? opts.today.slice(0, 7) : opts.today;

  const ctx = $(canvasId).getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 220);
  grad.addColorStop(0, "#3d63e0");
  grad.addColorStop(1, "#0f1f57");
  const colors = points.map((p) => (p.key === todayKey ? "#e8972a" : p.key > todayKey ? "#dfe5f4" : grad));

  if (charts[key]) charts[key].destroy();
  charts[key] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 8, borderSkipped: false, maxBarThickness: 26 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...TOOLTIP,
          callbacks: {
            title: (items) => {
              const p = points[items[0].dataIndex];
              if (isMonth) return MON_LONG[Number(p.key.slice(5, 7)) - 1];
              const dt = parseDate(p.key);
              return `${dt.getDate()}-${MON_LONG[dt.getMonth()]}, ${WD_LONG[(dt.getDay() + 6) % 7]}`;
            },
            label: (item) => opts.label(points[item.dataIndex], item.raw),
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

// Halqa grafik + yonidagi ro'yxat. rows: [{name, value}] — 6 tadan ko'pi «Boshqa»ga
function drawDonut(key, canvasId, legendId, items, fmtValue, fmtLegend) {
  const rows = items.slice(0, 6);
  if (items.length > 6) rows.push({ name: "Boshqa", value: items.slice(6).reduce((s, r) => s + r.value, 0) });
  const total = rows.reduce((s, r) => s + r.value, 0);
  const colors = rows.length ? rows.map((_, i) => PALETTE[i % PALETTE.length]) : ["#eef1f8"];

  if (charts[key]) charts[key].destroy();
  charts[key] = new Chart($(canvasId), {
    type: "doughnut",
    data: { labels: rows.map((r) => r.name), datasets: [{ data: rows.length ? rows.map((r) => r.value) : [1], backgroundColor: colors, borderWidth: 3, borderColor: "#fff", hoverOffset: 6 }] },
    options: {
      cutout: "70%",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { ...TOOLTIP, enabled: rows.length > 0, callbacks: { label: (item) => ` ${item.label}: ${fmtValue(item.raw)}` } },
      },
    },
  });

  $(legendId).innerHTML = rows.length
    ? rows.map((r, i) => `<li><span class="dot" style="background:${colors[i]}"></span><span class="name">${esc(r.name)}</span><span>${fmtLegend(r.value)}</span><span class="pct">${Math.round((r.value / total) * 100)}%</span></li>`).join("")
    : `<li class="muted">Ma'lumot yo'q</li>`;
  return total;
}

// --- Asosiy (dashboard) ------------------------------------------------------

async function loadDashboard() {
  if (state.me.is_owner) loadHomeMoney();
  try {
    state.dash = await api(`/api/dashboard?period=${state.period}`);
    renderDashboard();
  } catch (e) { fail(e); }
}

// --- Asosiy: pullarim holati (egada) -----------------------------------------

async function loadHomeMoney() {
  const h = state.home;
  if (!h.personal) {
    $("mDebtList").innerHTML = SKELETONS;
    $("mFeed").innerHTML = SKELETONS;
  }
  try {
    const [personal, debts, feed, clients] = await Promise.all([
      api(`/api/personal?period=${state.period}`),
      api("/api/debts"),
      api("/api/activity?limit=8"),
      api(`/api/clients?period=${state.period}`),
    ]);
    h.personal = personal;
    h.feed = feed.items;
    state.wallet.debts = debts;
    state.clients.home = clients;
    rememberClients(clients);
    renderHomeMoney();
    renderHomeClients();
  } catch (e) { fail(e); }
}

function renderHomeMoney() {
  const d = state.home.personal;
  const exp = d.totals.expense || {};
  const inc = d.totals.income || {};
  const all = {};
  for (const src of [exp, inc]) for (const [c, v] of Object.entries(src)) all[c] = (all[c] || 0) + v;
  const cur = mainCurrency(all);
  const net = (c) => (inc[c] || 0) - (exp[c] || 0);

  const main = net(cur);
  $("mNet").innerHTML = `${main > 0 ? "+" : ""}${fmtNum(main)}<small>${esc(CUR[cur] || cur)}</small>`;
  $("mNetExtra").textContent = Object.keys(all).filter((c) => c !== cur && net(c))
    .map((c) => (net(c) > 0 ? "+ " : "− ") + money(Math.abs(net(c)), c)).join("  ");
  setMoneyStat("mIncome", inc);
  setMoneyStat("mExpense", exp);
  $("mRange").textContent = rangeLabel(d.start, d.end);
  renderHomeDebts();
  renderHomeFeed();
}

function renderHomeDebts() {
  const d = state.wallet.debts;
  if (!d || !state.home.personal) return;
  setMoneyStat("mOwed", d.owed_to_me);
  setMoneyStat("mOwe", d.i_owe);
  const open = d.people.filter((p) => !p.settled);
  $("mDebtList").innerHTML = open.length
    ? open.slice(0, 5).map((p) => {
        const b = balanceInfo(p);
        return `
          <button class="mini-entry tap" data-person="${esc(p.key)}">
            <div class="avatar">${esc(initials(p.name))}</div>
            <div class="info"><b>${esc(p.name)}</b><small>${b.label}</small></div>
            <div class="amt ${b.cls}">${esc(b.text)}</div>
          </button>`;
      }).join("") + (open.length > 5 ? `<p class="muted small" style="margin:10px 0 0">yana ${open.length - 5} kishi…</p>` : "")
    : `<p class="muted">✅ Ochiq qarz yo'q. Yozish uchun botga ayting: «Abbosdan 100 ming won oldim».</p>`;
}

function renderHomeFeed() {
  const items = state.home.feed;
  $("mFeed").innerHTML = items.length
    ? items.map((e) => feedRow(e, { date: true })).join("")
    : `<p class="muted">Hali yozuv yo'q. Botga ovozli yoki matnli xabar yuboring: «bozorga 50 ming won», «oylik tushdi 3 million», «Valiga 50 dollar qarz berdim».</p>`;
}

// Bitta harakat qatori: xarajat, daromad, qarz yoki ish yozuvi
function feedRow(e, opts = {}) {
  let icon, iconCls = "", title, amt, amtCls = "";
  const sub = opts.date ? [dateLabel(e.date)] : [];
  if (e.type === "income" || e.type === "expense") {
    const inc = e.type === "income";
    icon = inc ? "💰" : "💸";
    iconCls = inc ? "in" : "";
    title = e.category;
    amt = (inc ? "+" : "−") + money(e.amount, e.currency);
    amtCls = inc ? "pos" : "";
    sub.push(inc ? "Daromad" : "Xarajat");
    if (e.note) sub.push(e.note);
  } else if (e.type === "debt") {
    const took = e.direction === "in";
    icon = took ? "📥" : "📤";
    iconCls = took ? "in" : "out";
    title = e.stated
      ? (took ? `Siz ${e.person}ga qarzdorsiz` : `${e.person} sizga qarzdor`)
      : (took ? `${e.person}dan olindi` : `${e.person}ga berildi`);
    amt = money(e.amount, e.currency);
    sub.push("Qarz");
    if (e.note) sub.push(e.note);
  } else if (e.type === "client") {
    icon = CLIENT_ICON[e.kind] || "❔";
    iconCls = e.kind === "payment" ? "in" : e.kind === "return" ? "out" : "";
    title = opts.noWho ? CLIENT_KIND[e.kind] : `${e.client.name} · ${CLIENT_KIND[e.kind] || ""}`;
    amt = money(e.amount, e.currency);
    amtCls = e.kind === "payment" ? "pos" : "";
    if (!opts.noWho) sub.push("Mijoz");
    if (e.kind !== "payment") {
      sub.push([e.product, qtyText(e)].filter(Boolean).join(" — "));
      if (e.unit_price != null) sub.push(money(e.unit_price, e.currency) + (e.price_unit === "piece" ? " / dona" : " / komplekt"));
    }
    if (e.note) sub.push(e.note);
  } else {
    icon = "🧵";
    title = `${e.model} · ${fmtNum(e.qty)} dona`;
    amt = e.total != null ? money(e.total, e.currency) : "";
    sub.push(e.worker.name, e.job, STATUS[e.status] || e.status);
  }
  const del = opts.del ? `<button class="mini-del" data-del-feed="${e.type}:${e.id}" aria-label="O'chirish">🗑</button>` : "";
  return `
    <div class="mini-entry">
      <div class="mini-icon ${iconCls}">${icon}</div>
      <div class="info"><b>${esc(title)}</b><small>${esc(sub.filter(Boolean).join(" · "))}</small></div>
      <div class="amt ${amtCls}">${esc(amt)}</div>
      ${del}
    </div>`;
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
  const prevLabel = PREV_LABELS[d.period];
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
  $("heroRange").textContent = rangeLabel(d.start, d.end);

  // Egada ishchi ham, ish yozuvi ham bo'lmasa — ishxona hisoboti kerak emas
  $("workSection").hidden = isOwner && !d.has_team;

  // Tasdiq kutayotganlar
  $("pendingCard").hidden = !(isOwner && d.pending > 0);
  $("pendingCount").textContent = d.pending;

  renderTrend();
  renderModels();
  if (isOwner) renderRanking(cur);
  renderJobStats();
}

function renderTrend() {
  const d = state.dash;
  const points = d.series.points;
  const cur = mainCurrency(d.totals.pay);
  const metric = state.metric;
  const values = points.map((p) => (metric === "qty" ? p.qty : p.pay[cur] || 0));

  $("chartTitle").textContent = CHART_TITLES[d.period];
  $("trendEmpty").hidden = values.some((v) => v > 0);
  drawBars("trend", "trendChart", points, values, {
    bucket: d.series.bucket,
    period: d.period,
    today: d.today,
    label: (p, v) => {
      const pay = moneyList(p.pay);
      return metric === "qty" ? `${fmtNum(p.qty)} dona${pay ? " · " + pay : ""}` : money(v, cur);
    },
  });
}

function renderModels() {
  const items = state.dash.by_model;
  $("modelCount").textContent = items.length ? `${items.length} ta model` : "";
  const total = drawDonut("model", "modelChart", "modelLegend", items.map((m) => ({ name: m.name, value: m.qty })), (v) => `${fmtNum(v)} dona`, fmtNum);
  $("donutTotal").textContent = fmtShort(total);
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

// Kasblar bo'yicha: har bir bosqichda (kroy, brsovka, tikuv...) qancha ish va ish haqi
const JOB_ICONS = [["kroy", "✂️"], ["brsov", "🔪"], ["tikuv", "🧵"], ["tort", "👞"], ["pardoz", "✨"]];
const jobIcon = (name) => (JOB_ICONS.find(([k]) => name.toLowerCase().startsWith(k)) || [, "🏷"])[1];

function renderJobStats() {
  const items = (state.dash.by_job || []).filter((j) => j.name);
  $("jobsStatCard").hidden = !state.me.is_owner || !items.length;
  if (!items.length) return;
  const max = Math.max(1, ...items.map((j) => j.qty));
  $("jobRank").innerHTML = items.map((j) => `
    <li>
      <div class="avatar job">${jobIcon(j.name)}</div>
      <div><div class="name">${esc(j.name)}</div><div class="bar"><i style="width:${Math.max(4, (j.qty / max) * 100)}%"></i></div></div>
      <div class="val">${fmtNum(j.qty)} ta<small>${esc(moneyList(j.pay) || "—")}</small></div>
    </li>`).join("");
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
  const isWork = en.type === "work";
  if (isWork && en.status) q.set("status", en.status);
  if (isWork && en.workerId) q.set("worker_id", en.workerId);
  if (!isWork && en.type) q.set("kind", en.type);
  try {
    const data = await api(`${isWork ? "/api/entries" : "/api/activity"}?${q}`);
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
    html += isWorkItem(e) ? entryCard(e) : `<div class="entry">${feedRow(e, { del: true })}</div>`;
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
          ${isOwner ? `<small>👷 ${esc(e.worker.name)}${e.job ? " · " + esc(e.job) : ""}</small>` : e.job ? `<small>🏷 ${esc(e.job)}</small>` : ""}<small>${unit ? esc(unit) + " / dona" : "narx yo'q"}</small>
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
      state.entries.items = state.entries.items.filter((e) => !(isWorkItem(e) && e.id === id));
      toast("O'chirildi");
    } else {
      const updated = await api(`/api/entries/${id}/${act}`, { method: "POST" });
      const keep = !state.entries.status || state.entries.status === updated.status;
      state.entries.items = state.entries.items.flatMap((e) => (isWorkItem(e) && e.id === id ? (keep ? [updated] : []) : [e]));
      toast(act === "approve" ? "Tasdiqlandi" : "Rad etildi");
    }
    renderEntries();
  } catch (e) { fail(e); }
}

const isWorkItem = (e) => !e.type || e.type === "work";

const DELETE_URL = { expense: "/api/personal/", income: "/api/personal/", debt: "/api/debts/", client: "/api/clients/entries/" };

async function deleteFeedItem(type, id) {
  if (!(await confirmBox("Yozuv o'chirilsinmi?"))) return;
  try {
    await api(DELETE_URL[type] + id, { method: "DELETE" });
    state.entries.items = state.entries.items.filter((e) => !(e.type === type && e.id === id));
    renderEntries();
    toast("O'chirildi");
  } catch (e) { fail(e); }
}

async function fillWorkerFilter() {
  if (!state.me.is_owner) return;
  try {
    if (!state.workers.length) state.workers = (await api("/api/workers")).items;
    const sel = $("workerFilter");
    sel.innerHTML = `<option value="">👷 Barcha ishchilar</option>` + state.workers.map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join("");
    sel.value = state.entries.workerId;
    syncChips();
  } catch (e) { fail(e); }
}

// --- Hamyon: xarajatlar ------------------------------------------------------

function loadWallet() {
  const w = state.wallet;
  $("walletMoney").hidden = w.tab !== "money";
  $("walletDebts").hidden = w.tab !== "debts";
  document.querySelectorAll("#walletTabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === w.tab));
  if (w.tab === "money") {
    loadPersonal();
    loadPersonalEntries(true);
  } else {
    loadDebts();
  }
}

async function loadPersonal() {
  try {
    state.wallet.data = await api(`/api/personal?period=${state.wallet.period}`);
    renderPersonal();
  } catch (e) { fail(e); }
}

function renderPersonal() {
  const d = state.wallet.data;
  const exp = d.totals.expense || {};
  const inc = d.totals.income || {};
  const cur = mainCurrency(Object.keys(exp).length ? exp : inc);

  $("wAmount").innerHTML = `${fmtNum(exp[cur] || 0)}<small>${esc(CUR[cur] || cur)}</small>`;
  $("wExtra").textContent = Object.entries(exp).filter(([c, v]) => c !== cur && v).map(([c, v]) => "+ " + money(v, c)).join("  ");
  $("wIncome").textContent = fmtShort(inc[cur] || 0);
  const net = (inc[cur] || 0) - (exp[cur] || 0);
  $("wNet").textContent = (net > 0 ? "+" : "") + fmtShort(net);

  // Xarajat o'tgan davrga nisbatan: ko'paygani — qizil
  const prev = (d.prev.expense || {})[cur] || 0;
  const now = exp[cur] || 0;
  const delta = $("wDelta");
  if (prev > 0) {
    const pct = Math.round(((now - prev) / prev) * 100);
    delta.className = "delta " + (pct > 0 ? "down" : "up");
    delta.innerHTML = `<b>${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct)}%</b> ${PREV_LABELS[d.period]} nisbatan`;
  } else {
    delta.innerHTML = "";
  }
  $("wRange").textContent = rangeLabel(d.start, d.end);

  const points = d.series.points;
  const values = points.map((p) => p.expense[cur] || 0);
  $("wChartTitle").textContent = CHART_TITLES[d.period];
  $("wTrendEmpty").hidden = values.some((v) => v > 0);
  drawBars("wTrend", "wTrendChart", points, values, {
    bucket: d.series.bucket,
    period: d.period,
    today: d.today,
    label: (p, v) => {
      const parts = [`${money(v, cur)} xarajat`];
      if (p.income[cur]) parts.push(`${money(p.income[cur], cur)} daromad`);
      return parts.join(" · ");
    },
  });

  const cats = d.by_category
    .map((c) => ({ name: c.name, value: c.amounts[cur] || 0 }))
    .filter((c) => c.value > 0)
    .sort((a, b) => b.value - a.value);
  $("wCatCount").textContent = cats.length ? `${cats.length} ta` : "";
  const total = drawDonut("wCat", "wCatChart", "wCatLegend", cats, (v) => money(v, cur), fmtShort);
  $("wDonutTotal").textContent = fmtShort(total);
  $("wDonutCur").textContent = CUR[cur] || cur;
}

async function loadPersonalEntries(reset) {
  const en = state.wallet.entries;
  if (reset) {
    en.offset = 0;
    en.items = [];
    $("wEntryList").innerHTML = SKELETONS;
  }
  try {
    const data = await api(`/api/personal/entries?limit=20&offset=${en.offset}`);
    en.items = en.items.concat(data.items);
    en.offset += data.items.length;
    $("wMoreBtn").hidden = !data.has_more;
    renderPersonalEntries();
  } catch (e) { fail(e); }
}

function renderPersonalEntries() {
  const items = state.wallet.entries.items;
  $("wEntryList").innerHTML = items.length
    ? items.map(personalRow).join("")
    : `<p class="muted">Hali yozuv yo'q. Botga ayting: «bozorga 50 ming won», «oylik tushdi 3 million».</p>`;
}

function personalRow(e) {
  const income = e.kind === "income";
  const sub = [dateLabel(e.date), e.note].filter(Boolean).join(" · ");
  return `
    <div class="mini-entry">
      <div class="mini-icon ${income ? "in" : ""}">${income ? "💰" : "💸"}</div>
      <div class="info"><b>${esc(e.category)}</b><small>${esc(sub)}</small></div>
      <div class="amt ${income ? "pos" : ""}">${income ? "+" : "−"}${esc(money(e.amount, e.currency))}</div>
      <button class="mini-del" data-del-personal="${e.id}" aria-label="O'chirish">🗑</button>
    </div>`;
}

async function deletePersonal(id) {
  if (!(await confirmBox("Yozuv o'chirilsinmi?"))) return;
  try {
    await api(`/api/personal/${id}`, { method: "DELETE" });
    state.wallet.entries.items = state.wallet.entries.items.filter((e) => e.id !== id);
    renderPersonalEntries();
    toast("O'chirildi");
    loadPersonal();
  } catch (e) { fail(e); }
}

// --- Hamyon: qarzlar ---------------------------------------------------------

async function loadDebts() {
  if (!state.wallet.debts) $("debtList").innerHTML = SKELETONS;
  try {
    state.wallet.debts = await api("/api/debts");
    renderDebts();
  } catch (e) { fail(e); }
}

function setMoneyStat(id, pay) {
  const text = moneyList(pay) || "0";
  $(id).textContent = text;
  $(id).classList.toggle("long", text.length > 12);
}

// Qoldiq: musbat — u sizga qarzdor, manfiy — siz unga qarzdorsiz
function balanceInfo(p) {
  const entries = Object.entries(p.balance || {});
  const pos = entries.filter(([, v]) => v > 0);
  const neg = entries.filter(([, v]) => v < 0);
  if (!entries.length) return { cls: "", text: "0", label: "yopilgan" };
  if (!neg.length) return { cls: "pos", text: pos.map(([c, v]) => money(v, c)).join(" + "), label: "sizga qarzdor" };
  if (!pos.length) return { cls: "neg", text: neg.map(([c, v]) => money(-v, c)).join(" + "), label: "siz qarzdorsiz" };
  return { cls: "", text: entries.map(([c, v]) => (v > 0 ? "+" : "−") + money(Math.abs(v), c)).join(" "), label: "aralash" };
}

function renderDebts() {
  const d = state.wallet.debts;
  renderHomeDebts();
  setMoneyStat("dOwed", d.owed_to_me);
  setMoneyStat("dOwe", d.i_owe);
  if (!d.people.length) {
    $("debtList").innerHTML = `<div class="empty"><span class="big">🤝</span>Hali qarz yozilmagan</div>`;
    return;
  }
  $("debtList").innerHTML = d.people.map((p) => {
    const b = balanceInfo(p);
    const sub = [p.last_date ? dateLabel(p.last_date) : "", `${p.count} ta yozuv`].filter(Boolean).join(" · ");
    return `
      <button class="row-card${p.settled ? " off" : ""}" data-person="${esc(p.key)}">
        <div class="avatar">${esc(initials(p.name))}</div>
        <div class="info"><b>${esc(p.name)}</b><small>${esc(sub)}</small></div>
        <div class="side ${b.cls}">${esc(b.text)}<small>${b.label}</small></div>
      </button>`;
  }).join("");
}

function debtRow(e) {
  const took = e.direction === "in";
  const sub = [dateLabel(e.date), e.note].filter(Boolean).join(" · ");
  return `
    <div class="mini-entry">
      <div class="mini-icon ${took ? "in" : "out"}">${took ? "📥" : "📤"}</div>
      <div class="info"><b>${e.stated ? (took ? "Siz qarzdorsiz" : "Sizga qarzdor") : took ? "Olindi" : "Berildi"}</b><small>${esc(sub)}</small></div>
      <div class="amt">${esc(money(e.amount, e.currency))}</div>
      <button class="mini-del" data-del-debt="${e.id}" aria-label="O'chirish">🗑</button>
    </div>`;
}

async function personSheet(key) {
  const p = state.wallet.debts.people.find((x) => x.key === key);
  if (!p) return closeSheet();
  const b = balanceInfo(p);
  openSheet(`
    <h3>${esc(p.name)}</h3>
    <div class="stat-pair">
      <div><b class="amt ${b.cls}">${esc(b.text)}</b><small>${b.label}</small></div>
      <div><b>${p.count} ta</b><small>yozuv</small></div>
    </div>
    <div id="personHistory">${SKELETONS}</div>
  `);
  try {
    const data = await api(`/api/debts/entries?key=${encodeURIComponent(key)}&limit=100`);
    $("personHistory").innerHTML = data.items.map(debtRow).join("") || `<p class="muted">Yozuv yo'q</p>`;
    $("personHistory").onclick = async (ev) => {
      const btn = ev.target.closest("[data-del-debt]");
      if (!btn || !(await confirmBox("Qarz yozuvi o'chirilsinmi?"))) return;
      try {
        await api(`/api/debts/${btn.dataset.delDebt}`, { method: "DELETE" });
        toast("O'chirildi");
        await loadDebts();
        personSheet(key);
      } catch (e) { fail(e); }
    };
  } catch (e) { fail(e); }
}

// --- Mijozlar: optom savdo ---------------------------------------------------

const CLIENT_KIND = { sale: "Mol berildi", payment: "To'lov olindi", return: "Mol qaytarildi" };
const CLIENT_ICON = { sale: "📦", payment: "💵", return: "↩️" };

function qtyText(e) {
  if (e.sets) return `${fmtNum(e.sets)} komplekt` + (e.pieces ? ` (${fmtNum(e.pieces)} dona)` : "");
  return e.pieces ? `${fmtNum(e.pieces)} dona` : "";
}

// Qoldiq: musbat — mijoz qarzdor, manfiy — oldindan to'lagan
function clientBalance(c) {
  const entries = Object.entries(c.balance || {});
  const pos = entries.filter(([, v]) => v > 0);
  const neg = entries.filter(([, v]) => v < 0);
  if (!entries.length) return { cls: "", text: "0", label: "hisob yopilgan" };
  if (!neg.length) return { cls: "pos", text: pos.map(([cur, v]) => money(v, cur)).join(" + "), label: "qarzi" };
  if (!pos.length) return { cls: "neg", text: neg.map(([cur, v]) => money(-v, cur)).join(" + "), label: "oldindan to'lagan" };
  return { cls: "", text: entries.map(([cur, v]) => (v > 0 ? "" : "−") + money(Math.abs(v), cur)).join(" "), label: "aralash" };
}

function rememberClients(data) {
  for (const c of data.items) state.clients.byId[c.id] = { ...c, period: data.period };
}

function setHeroAmount(amountId, extraId, pay) {
  const cur = mainCurrency(pay);
  $(amountId).innerHTML = `${fmtNum((pay || {})[cur] || 0)}<small>${esc(CUR[cur] || cur)}</small>`;
  $(extraId).textContent = Object.entries(pay || {}).filter(([c, v]) => c !== cur && v).map(([c, v]) => "+ " + money(v, c)).join("  ");
}

function renderHomeClients() {
  const d = state.clients.home;
  if (!d) return;
  setMoneyStat("mcOwed", d.totals.owed);
  setMoneyStat("mcSold", d.totals.sold);
  setMoneyStat("mcPaid", d.totals.paid);
  const owing = d.items.filter((c) => Object.values(c.balance).some((v) => v > 0));
  $("mClientList").innerHTML = owing.length
    ? owing.slice(0, 4).map((c) => {
        const b = clientBalance(c);
        return `
          <button class="mini-entry tap" data-client="${c.id}">
            <div class="avatar">${esc(initials(c.name))}</div>
            <div class="info"><b>${esc(c.name)}</b><small>${b.label}</small></div>
            <div class="amt ${b.cls}">${esc(b.text)}</div>
          </button>`;
      }).join("") + (owing.length > 4 ? `<p class="muted small" style="margin:10px 0 0">yana ${owing.length - 4} mijoz…</p>` : "")
    : `<p class="muted">✅ Mijozlarda qarz yo'q. Yozish uchun 🎤 ni bosib turib ayting: «Tohirga 10 komplekt maksima berdim, 140 mingdan».</p>`;
}

async function loadClients() {
  const c = state.clients;
  if (!c.data) $("clientList").innerHTML = SKELETONS;
  loadClientFeed(true);
  try {
    c.data = await api(`/api/clients?period=${c.period}`);
    rememberClients(c.data);
    renderClients();
  } catch (e) { fail(e); }
}

function renderClients() {
  const d = state.clients.data;
  setHeroAmount("cOwed", "cOwedExtra", d.totals.owed);
  setMoneyStat("cSold", d.totals.sold);
  setMoneyStat("cPaid", d.totals.paid);
  const qty = d.totals.sets ? ` · ${fmtNum(d.totals.sets)} komplekt (${fmtNum(d.totals.pieces)} dona)` : "";
  $("cRange").textContent = rangeLabel(d.start, d.end) + qty;
  if (!d.items.length) {
    $("clientList").innerHTML = `<div class="empty"><span class="big">🛍</span>Hali mijoz yo'q.<br>🎤 bilan ayting yoki «+ Qo'shish» tugmasini bosing.</div>`;
    return;
  }
  $("clientList").innerHTML = d.items.map((c) => {
    const b = clientBalance(c);
    const sub = [c.sets ? `${fmtNum(c.sets)} komplekt` : "", c.last_date ? dateLabel(c.last_date) : "yozuv yo'q"].filter(Boolean).join(" · ");
    return `
      <button class="row-card${c.is_active ? "" : " off"}" data-client="${c.id}">
        <div class="avatar">${esc(initials(c.name))}</div>
        <div class="info"><b>${esc(c.name)}</b><small>${esc(sub)}</small></div>
        <div class="side ${b.cls}">${esc(b.text)}<small>${b.label}</small></div>
      </button>`;
  }).join("");
}

async function loadClientFeed(reset) {
  const f = state.clients.feed;
  if (reset) {
    f.offset = 0;
    f.items = [];
  }
  try {
    const data = await api(`/api/clients/entries?limit=20&offset=${f.offset}`);
    f.items = f.items.concat(data.items.map((e) => ({ type: "client", ...e })));
    f.offset += data.items.length;
    $("cMoreBtn").hidden = !data.has_more;
    $("cFeed").innerHTML = f.items.map((e) => feedRow(e, { date: true, del: true })).join("")
      || `<p class="muted">Hali oldi-berdi yozilmagan.</p>`;
  } catch (e) { fail(e); }
}

async function deleteClientEntry(id, after) {
  if (!(await confirmBox("Yozuv o'chirilsinmi? Mijoz qoldig'i qayta hisoblanadi."))) return;
  try {
    await api(`/api/clients/entries/${id}`, { method: "DELETE" });
    toast("O'chirildi");
    refreshView();
    if (after) after();
  } catch (e) { fail(e); }
}

async function clientSheet(id) {
  const c = state.clients.byId[id];
  if (!c) return;
  const b = clientBalance(c);
  const periodLabel = { day: "bugun", week: "shu hafta", month: "shu oy", year: "shu yil" }[c.period] || "";
  openSheet(`
    <h3>${esc(c.name)}</h3>
    ${c.phone ? `<a class="link-box phone" href="tel:${esc(c.phone.replace(/[^+\d]/g, ""))}">📞 ${esc(c.phone)}</a>` : ""}
    <div class="stat-pair">
      <div><b class="amt ${b.cls}">${esc(b.text)}</b><small>${b.label}</small></div>
      <div><b>${fmtNum(c.sets)} komplekt</b><small>${periodLabel} · ${esc(moneyList(c.sold) || "0")}</small></div>
    </div>
    <div id="clientHistory">${SKELETONS}</div>
    <button class="secondary" id="clEdit">✏️ Ism va telefon</button>
  `);
  $("clEdit").onclick = () => clientEditSheet(c);
  try {
    const data = await api(`/api/clients/entries?client_id=${id}&limit=100`);
    if (!$("clientHistory")) return;
    $("clientHistory").innerHTML = data.items.map((e) => feedRow({ type: "client", ...e }, { date: true, del: true, noWho: true })).join("")
      || `<p class="muted">Hali oldi-berdi yo'q</p>`;
    $("clientHistory").onclick = (ev) => {
      const btn = ev.target.closest("[data-del-feed]");
      if (btn) deleteClientEntry(Number(btn.dataset.delFeed.split(":")[1]), () => closeSheet());
    };
  } catch (e) { fail(e); }
}

function clientEditSheet(c) {
  const isNew = !c;
  c = c || { name: "", phone: "", is_active: true };
  openSheet(`
    <h3>${isNew ? "Yangi mijoz" : esc(c.name)}</h3>
    <div class="field"><label>Ismi</label><input class="input" id="clName" maxlength="100" value="${esc(c.name)}" placeholder="Masalan: Tohir" autocomplete="off"></div>
    <div class="field"><label>Telefon (ixtiyoriy)</label><input class="input" id="clPhone" maxlength="30" inputmode="tel" value="${esc(c.phone || "")}" placeholder="+998 90 123 45 67"></div>
    ${isNew ? "" : `<label class="switch-row"><div>Faol<small>O'chirilgan mijoz ro'yxat oxirida turadi, yozuvlari saqlanadi</small></div><span class="switch"><input type="checkbox" id="clActive" ${c.is_active ? "checked" : ""}><i></i></span></label>`}
    <button class="primary" id="clSave">${isNew ? "Qo'shish" : "Saqlash"}</button>
  `);
  if (isNew) setTimeout(() => $("clName").focus(), 250);
  $("clSave").onclick = async () => {
    const body = { name: $("clName").value.trim(), phone: $("clPhone").value.trim() };
    if (!body.name) return $("clName").focus();
    if (!isNew) body.is_active = $("clActive").checked;
    $("clSave").disabled = true;
    try {
      if (isNew) await api("/api/clients", { method: "POST", body });
      else await api(`/api/clients/${c.id}`, { method: "PATCH", body });
      closeSheet();
      toast("Saqlandi");
      refreshView();
    } catch (e) { fail(e); $("clSave").disabled = false; }
  };
}

// --- Ishchilar va kasblar ---------------------------------------------------

async function loadWorkers() {
  if (state.workers.length) renderWorkers();
  else $("workerList").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const data = await api("/api/workers");
    state.workers = data.items;
    state.jobs = data.jobs;
    renderWorkers();
  } catch (e) { fail(e); }
}

function renderWorkers() {
  const items = state.workers;
  renderJobChips();
  if (!items.length) {
    $("workerList").innerHTML = `<div class="empty"><span class="big">👷</span>Hali ishchi yo'q.<br>«+ Qo'shish» tugmasini bosing.</div>`;
    return;
  }
  $("workerList").innerHTML = items.map((w) => {
    const tag = !w.is_active ? '<span class="tag off">O\'chirilgan</span>'
      : w.linked ? '<span class="tag ok">Ulangan</span>' : '<span class="tag wait">Havola kutilmoqda</span>';
    const job = w.job ? `<span class="tag job">${esc(w.job.name)}</span>` : '<span class="tag none">Kasbi tanlanmagan</span>';
    return `
      <button class="row-card${w.is_active ? "" : " off"}" data-worker="${w.id}">
        <div class="avatar">${esc(initials(w.name))}</div>
        <div class="info"><b>${esc(w.name)}</b>${job}${tag}</div>
        <div class="side">${fmtNum(w.month_qty)} ta<small>${esc(moneyList(w.month_pay) || "shu oy")}</small></div>
      </button>`;
  }).join("");
}

function renderJobChips() {
  $("jobChips").innerHTML = state.jobs.map((j) => `
    <button data-job="${j.id}">${esc(j.name)}${j.workers ? `<small>${j.workers}</small>` : ""}</button>`).join("")
    + `<button data-job="new" class="add">+ Kasb</button>`;
}

// Kasb tanlash tugmalari: tanlangani (selected) to'q rangda, «Kasbsiz» va «+ Yangi» bilan
function jobPicker(id, selected) {
  return `<div class="pick" id="${id}">
    ${state.jobs.map((j) => `<button data-job="${j.id}" class="${selected === j.id ? "on" : ""}">${esc(j.name)}</button>`).join("")}
    <button data-job="" class="${selected ? "" : "on"}">Kasbsiz</button>
    <button data-job="new" class="add">+ Yangi kasb</button>
  </div>`;
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
    <div class="field"><label>🏷 Kasbi</label>
      ${jobPicker("wJobs", w.job && w.job.id)}
      <span class="field-hint">Ish topshirganda kasb aytilmasa — shu kasbning narxi olinadi.</span>
    </div>
    ${invite}
    <button class="${w.is_active ? "danger" : "secondary"}" id="wToggle">${w.is_active ? "⛔ O'chirish (botdan foydalana olmaydi)" : "✅ Qayta faollashtirish"}</button>
    <button class="secondary" id="wEntries">🗂 Yozuvlarini ko'rish</button>
  `);
  const setJob = async (jobId) => {
    try {
      const upd = await api(`/api/workers/${w.id}`, { method: "PATCH", body: { job_id: jobId } });
      replaceWorker(upd);
      workerSheet(upd);
      toast(upd.job ? `Kasbi: ${upd.job.name}` : "Kasbsiz");
    } catch (e) { fail(e); }
  };
  $("wJobs").onclick = (ev) => {
    const b = ev.target.closest("[data-job]");
    if (!b || b.classList.contains("on")) return;
    if (b.dataset.job === "new") jobSheet(null, (job) => setJob(job.id));
    else setJob(b.dataset.job ? Number(b.dataset.job) : null);
  };
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
    state.entries.type = "work";
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

function addWorkerSheet(name = "", jobId = null) {
  openSheet(`
    <h3>Yangi ishchi</h3>
    <div class="field"><label>Ismi</label><input class="input" id="wName" maxlength="100" placeholder="Masalan: Alisher" autocomplete="off" value="${esc(name)}"></div>
    <div class="field"><label>🏷 Kasbi</label>${jobPicker("wNewJobs", jobId)}</div>
    <button class="primary" id="wSave">Qo'shish</button>
  `);
  const input = $("wName");
  let selected = jobId;
  if (!name) setTimeout(() => input.focus(), 250);
  $("wNewJobs").onclick = (ev) => {
    const b = ev.target.closest("[data-job]");
    if (!b) return;
    if (b.dataset.job === "new") {
      const typed = input.value;
      jobSheet(null, (job) => addWorkerSheet(typed, job.id));
      return;
    }
    selected = b.dataset.job ? Number(b.dataset.job) : null;
    $("wNewJobs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    haptic();
  };
  $("wSave").onclick = async () => {
    const value = input.value.trim();
    if (!value) return input.focus();
    $("wSave").disabled = true;
    try {
      const w = await api("/api/workers", { method: "POST", body: { name: value, job_id: selected } });
      state.workers.unshift(w);
      renderWorkers();
      toast("Qo'shildi — havolani ishchiga yuboring");
      workerSheet(w);
    } catch (e) { fail(e); $("wSave").disabled = false; }
  };
}

// Kasb qo'shish yoki tahrirlash. onCreated(job) — yangi kasb qo'shilgach (masalan, ishchiga berish uchun)
function jobSheet(j, onCreated) {
  const isNew = !j;
  openSheet(`
    <h3>${isNew ? "Yangi kasb" : esc(j.name)}</h3>
    <div class="field"><label>Kasb nomi</label><input class="input" id="jName" maxlength="50" value="${esc(j ? j.name : "")}" placeholder="Masalan: Upakovkachi" autocomplete="off"></div>
    <button class="primary" id="jSave">${isNew ? "Qo'shish" : "Saqlash"}</button>
    ${isNew
      ? '<p class="muted small" style="margin-top:12px">Kasb ishchiga beriladi, har bir modelga esa shu kasb uchun alohida narx qo\'yiladi («Modellar»).</p>'
      : `<button class="danger" id="jDel">🗑 O'chirish</button>
         <p class="muted small" style="margin-top:12px">${j.workers ? `${j.workers} ta ishchi kasbsiz qoladi. ` : ""}Eski yozuvlar o'zgarmaydi.</p>`}
  `);
  const input = $("jName");
  setTimeout(() => input.focus(), 250);
  $("jSave").onclick = async () => {
    const name = input.value.trim();
    if (!name) return input.focus();
    $("jSave").disabled = true;
    try {
      const res = isNew
        ? await api("/api/jobs", { method: "POST", body: { name } })
        : await api(`/api/jobs/${j.id}`, { method: "PATCH", body: { name } });
      state.jobs = res.items;
      if (isNew && onCreated) {
        const job = state.jobs.find((x) => x.name.toLowerCase() === name.toLowerCase()) || state.jobs[state.jobs.length - 1];
        onCreated(job);
      } else {
        closeSheet();
      }
      toast(isNew ? "Kasb qo'shildi" : "Saqlandi");
      if (state.view === "workers") loadWorkers();
      if (state.view === "models") renderModelList();
    } catch (e) { fail(e); $("jSave").disabled = false; }
  };
  if ($("jDel")) $("jDel").onclick = async () => {
    if (!(await confirmBox(`«${j.name}» kasbi o'chirilsinmi?`))) return;
    try {
      state.jobs = (await api(`/api/jobs/${j.id}`, { method: "DELETE" })).items;
      closeSheet();
      toast("O'chirildi");
      loadWorkers();
    } catch (e) { fail(e); }
  };
}

// --- Modellar ----------------------------------------------------------------

async function loadModels() {
  if (state.models.length) renderModelList();
  else $("modelList").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const data = await api("/api/models");
    state.models = data.items;
    if (data.jobs) state.jobs = data.jobs;
    renderModelList();
  } catch (e) { fail(e); }
}

// «Kroychi 1 500 · Brsovkachi 2 000» — kasblar ro'yxatidagi tartibda
function jobPrices(m) {
  return state.jobs
    .map((j) => [j, (m.prices || []).find((p) => p.job_id === j.id)])
    .filter(([, p]) => p)
    .map(([j, p]) => `${j.name} ${money(p.price, p.currency)}`);
}

function renderModelList() {
  const items = state.models;
  const owner = state.me.is_owner;
  if (!items.length) {
    $("modelList").innerHTML = `<div class="empty"><span class="big">🧵</span>Hali model yo'q${owner ? ".<br>«+ Qo'shish» tugmasini bosing." : ""}</div>`;
    return;
  }
  $("modelList").innerHTML = items.map((m) => {
    const byJob = owner ? jobPrices(m) : [];
    const side = m.price != null ? esc(money(m.price, m.currency)) : '<span class="muted">—</span>';
    return `
    <${owner ? "button" : "div"} class="row-card${m.is_active ? "" : " off"}" data-model="${m.id}">
      <div class="model-badge icon">🧵</div>
      <div class="info"><b>${esc(m.name)}</b>${byJob.length ? `<small class="line">${esc(byJob.join(" · "))}</small>` : ""}${m.is_active ? "" : '<span class="tag off">O\'chirilgan</span>'}</div>
      <div class="side">${side}<small>${owner ? "umumiy" : "bir dona"}</small></div>
    </${owner ? "button" : "div"}>`;
  }).join("");
}

function modelSheet(m) {
  const isNew = !m;
  m = m || { name: "", price: null, currency: state.me.currencies[0], is_active: true, prices: [] };
  const curOptions = state.me.currencies.concat(state.me.currencies.includes(m.currency) ? [] : [m.currency])
    .map((c) => `<option value="${c}" ${c === m.currency ? "selected" : ""}>${CUR[c] || c}</option>`).join("");
  const priceOf = (jobId) => {
    const p = (m.prices || []).find((x) => x.job_id === jobId);
    return p ? p.price : "";
  };
  const rows = state.jobs.map((j) => `
      <div class="price-row"><span>${esc(j.name)}</span><input class="input" data-job-price="${j.id}" inputmode="decimal" value="${priceOf(j.id)}" placeholder="—" aria-label="${esc(j.name)}"></div>`).join("");
  openSheet(`
    <h3>${isNew ? "Yangi model" : esc(m.name)}</h3>
    <div class="field"><label>Model nomi</label><input class="input" id="mName" maxlength="100" value="${esc(m.name)}" placeholder="Masalan: A-12" autocomplete="off"></div>
    <div class="field"><label>Bir dona uchun ish haqi</label>
      <div class="price-list">
        ${rows}
        <div class="price-row"><span>Umumiy<small>kasbining narxi yozilmagan ishlarga</small></span><input class="input" id="mPrice" inputmode="decimal" value="${m.price != null ? m.price : ""}" placeholder="—" aria-label="Umumiy narx"></div>
        <div class="price-row"><span>Valyuta</span><select class="select" id="mCur" style="margin:0" aria-label="Valyuta">${curOptions}</select></div>
      </div>
    </div>
    ${isNew ? "" : `<label class="switch-row"><div>Faol<small>O'chirilgan model yangi yozuvlarda taklif qilinmaydi</small></div><span class="switch"><input type="checkbox" id="mActive" ${m.is_active ? "checked" : ""}><i></i></span></label>`}
    <button class="primary" id="mSave">${isNew ? "Qo'shish" : "Saqlash"}</button>
    <p class="muted small" style="margin-top:12px">${isNew ? "" : "Yangi narx shu paytdan keyingi yozuvlarga qo'llanadi. "}💡 Ovoz bilan ham aytish mumkin: «A-12 modelga brsovka 2000dan».</p>
  `);
  if (isNew) setTimeout(() => $("mName").focus(), 250);
  $("mSave").onclick = async () => {
    const prices = {};
    document.querySelectorAll("[data-job-price]").forEach((el) => { prices[el.dataset.jobPrice] = el.value.trim() || null; });
    const body = { name: $("mName").value.trim(), price: $("mPrice").value.trim() || null, currency: $("mCur").value, prices };
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

// --- Ilova ichida yozish: ovoz, matn yoki rasm -> qoralama -> saqlash ----------

const compose = { busy: false, recorder: null, chunks: [], stream: null, timer: null, started: 0, starting: false, abort: false };
const MAX_RECORD_SEC = 120;
const FIX_ACTION = "d:fix";

// Server Telegram HTML (<b>, <i>) qaytaradi — faqat shu teglarni qoldiramiz
function tgHtml(html) {
  const t = document.createElement("template");
  t.innerHTML = String(html || "");
  t.content.querySelectorAll("*").forEach((el) => {
    if (["B", "I", "S"].includes(el.tagName)) [...el.attributes].forEach((a) => el.removeAttribute(a.name));
    else el.replaceWith(document.createTextNode(el.textContent));
  });
  return t.innerHTML.replace(/\n/g, "<br>");
}

// voice — yozilgan ovoz, images — tanlangan yoki tashlangan rasmlar
function composeSheet(voice, images) {
  const owner = state.me.is_owner;
  const example = owner ? "Masalan: bozorga 50 ming won" : "Masalan: A-12 dan 50 ta topshirdim";
  openSheet(`
    <h3>Yangi yozuv</h3>
    <div id="cOut"></div>
    <button class="rec-btn" id="cMic"><span id="cMicText">🎤 Bosib turing va gapiring</span></button>
    <div class="img-row">
      <button class="img-btn" id="cCam">📷 Suratga olish</button>
      <button class="img-btn" id="cGal">🖼 ${owner ? "Skrinshot" : "Galereya"}</button>
    </div>
    <div class="compose">
      <textarea class="input" id="cText" rows="1" maxlength="2000" placeholder="${esc(example)}"></textarea>
      <button class="send-btn" id="cSend" aria-label="Yuborish">➤</button>
    </div>
    <p class="muted small compose-hint">${owner
      ? "Xarajat, daromad, qarz yoki ish: «oylik tushdi 3 million», «Abbosdan 100 ming won oldim», «Valiga 50 dollar berdim».<br>📷 Chek yoki daftarni suratga oling, 🖼 bank ilovasidan skrinshot tanlang — koreyscha, lotin va kirill yozuvini o'qib, kategoriyalarga ajratadi; «✅ Tasdiqlash» ni bossangiz hisobga qo'shiladi."
      : "Model va sonini ayting: «kecha B-5 dan 30 ta». Boshqa ish qilgan bo'lsangiz — ish turini ham: «A-12 dan 40 ta brsovka».<br>📷 Daftaringizni suratga olsangiz ham bo'ladi."}</p>
  `);
  const input = $("cText");
  input.oninput = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  };
  input.onkeydown = (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendText(); }
  };
  // Kompyuterda: nusxalangan skrinshotni Ctrl+V bilan qo'yish
  input.onpaste = (ev) => {
    const files = imageFiles(ev.clipboardData && ev.clipboardData.files);
    if (!files.length) return;
    ev.preventDefault();
    sendImages(files);
  };
  $("cSend").onclick = sendText;
  $("cCam").onclick = () => !compose.busy && $("camInput").click();
  $("cGal").onclick = () => !compose.busy && $("galInput").click();
  bindHoldToTalk($("cMic"), () => toast("🎤 Tugmani bosib turing, gapiring va qo'yib yuboring"));
  $("cOut").onclick = (ev) => {
    const b = ev.target.closest("[data-cb]");
    if (b) draftAction(b.dataset.cb);
  };
  if (voice) sendVoice(voice);
  else if (images) sendImages(images);
  else composeRequest(api("/api/input"), null);
}

function sendVoice(blob) {
  composeRequest(api("/api/input/voice", { method: "POST", blob }), "🎧 Eshitilmoqda…");
}

// --- Rasm: bank skrinshoti, chek yoki daftar sahifasi ---------------------------

const MAX_IMAGES = 5;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
const IMAGE_MAX_SIDE = 2576; // Claude shundan kattasini baribir kichraytiradi
const IMAGE_MAX_BYTES = 3500000;

const imageFiles = (list) => [...(list || [])].filter((f) => !f.type || f.type.startsWith("image/"));

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Rasmni ochib bo'lmadi — skrinshot yoki JPG/PNG rasm tanlang")); };
    img.src = url;
  });
}

// Katta yoki boshqa formatdagi (HEIC va h.k.) rasm — JPEG qilib kichraytiriladi
async function prepareImage(file) {
  const img = await loadImage(file);
  const w = img.naturalWidth, h = img.naturalHeight;
  const scale = Math.min(1, IMAGE_MAX_SIDE / Math.max(w, h));
  if (scale === 1 && IMAGE_TYPES.includes(file.type) && file.size <= IMAGE_MAX_BYTES) return file;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; // shaffof PNG qora bo'lib qolmasin
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  for (const quality of [0.9, 0.8, 0.65]) {
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
    if (blob && blob.size <= IMAGE_MAX_BYTES) return blob;
  }
  throw new Error("Rasm juda katta");
}

// 📷 tugma: kamera yoki galereya tanlanadi (fayl oynasi faqat bosish ichida ochiladi — iPhone talabi)
function pickImageSheet() {
  const owner = state.me.is_owner;
  openSheet(`
    <h3>Rasm qo'shish</h3>
    <div class="pick-grid">
      <button class="pick-btn" id="pCam"><span>📷</span>Suratga olish<small>${owner ? "chek yoki daftar" : "daftar sahifasi"}</small></button>
      <button class="pick-btn" id="pGal"><span>🖼</span>Galereyadan<small>${owner ? "bank skrinshoti, bir nechta" : "bir nechta rasm"}</small></button>
    </div>
    <p class="muted small compose-hint">Koreyscha, lotin va kirill yozuvini o'qib, ${owner ? "kategoriyalarga ajratadi — tasdiqlaganingizdan keyin hisobga qo'shiladi" : "ish yozuviga aylantiradi"}. Bir martada ${MAX_IMAGES} tagacha rasm.</p>
  `);
  $("pCam").onclick = () => { haptic(); $("camInput").click(); };
  $("pGal").onclick = () => { haptic(); $("galInput").click(); };
}

// Kameradan, galereyadan yoki sudrab tashlangan rasm: yozish oynasi ochiq bo'lsa — shu yerda o'qiladi
function takeImages(files) {
  if (!state.me) return;
  if (!files.length) return toast("Rasm tanlang: skrinshot, chek yoki daftar", true);
  if ($("cOut") && !$("sheet").hidden) sendImages(files);
  else composeSheet(null, files);
}

async function sendImages(files) {
  if (compose.busy) return;
  if (files.length > MAX_IMAGES) toast(`Bir martada ${MAX_IMAGES} tagacha rasm o'qiladi — birinchi ${MAX_IMAGES} tasi olindi`);
  haptic();
  setBusy(true, files.length > 1 ? "🖼 Rasmlar o'qilmoqda…" : "🖼 Rasm o'qilmoqda…");
  const form = new FormData();
  try {
    for (const file of files.slice(0, MAX_IMAGES)) {
      const img = await prepareImage(file);
      form.append("image", img, img === file && file.name ? file.name : "rasm.jpg");
    }
  } catch (e) {
    setBusy(false);
    return fail(e);
  }
  // Matn maydoniga yozilgani — rasmga izoh: «kecha edi», «faqat xarajatlar»
  const caption = $("cText").value.trim();
  if (caption) form.append("caption", caption);
  $("cText").value = "";
  $("cText").style.height = "auto";
  composeRequest(api("/api/input/image", { method: "POST", form }), null);
}

function setBusy(on, label) {
  compose.busy = on;
  ["cSend", "cMic", "cCam", "cGal", "cText"].forEach((id) => $(id) && ($(id).disabled = on));
  document.querySelectorAll("#cOut [data-cb]").forEach((b) => (b.disabled = on));
  if (on && label && $("cOut")) $("cOut").insertAdjacentHTML("afterbegin", `<div class="c-note busy" id="cBusy">${esc(label)}</div>`);
  if (!on && $("cBusy")) $("cBusy").remove();
}

async function composeRequest(promise, label) {
  if (label) setBusy(true, label);
  try {
    renderCompose(await promise);
  } catch (e) {
    fail(e);
  } finally {
    setBusy(false);
  }
}

function renderCompose(res) {
  if (!$("cOut")) return; // oyna yopilgan
  let html = "";
  if (res.heard) html += `<div class="c-note heard">🗣 «${esc(res.heard)}»</div>`;
  if (res.notice) html += `<div class="c-note">${tgHtml(res.notice)}</div>`;
  if (res.saved) {
    html += `<div class="c-note saved">${tgHtml(res.saved)}</div>`;
    toast("Saqlandi");
    refreshView();
  }
  if (res.draft) {
    html += `<div class="draft-box">${tgHtml(res.draft.html)}</div>`;
    html += res.draft.buttons.map((row) => `<div class="kb-row">${row.map((b) =>
      `<button class="kb-btn${b.data === "d:save" ? " main" : ""}" data-cb="${esc(b.data)}">${esc(b.text)}</button>`).join("")}</div>`).join("");
    if (!res.draft.buttons.length || res.draft.html.includes("❓")) html += `<p class="muted small c-ask">Javobni pastga yozing yoki 🎤 bilan ayting.</p>`;
  }
  $("cOut").innerHTML = html;
  $("cMicText").textContent = res.draft ? "🎤 Tuzatish yoki javob — bosib turing" : "🎤 Bosib turing va gapiring";
}

function sendText() {
  const input = $("cText");
  const text = input.value.trim();
  if (!text || compose.busy) return input.focus();
  input.value = "";
  input.style.height = "auto";
  haptic();
  composeRequest(api("/api/input", { method: "POST", body: { text } }), "⏳ Tahlil qilinmoqda…");
}

function draftAction(data) {
  if (compose.busy) return;
  haptic();
  if (data === FIX_ACTION) {
    $("cText").placeholder = "Nimani o'zgartiramiz? Masalan: summa 45 ming, kecha edi";
    $("cText").focus();
    return;
  }
  composeRequest(api("/api/input/action", { method: "POST", body: { data } }), data === "d:save" ? "💾 Saqlanmoqda…" : null);
}

function pickMime() {
  if (!window.MediaRecorder) return null;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
}

// --- Ovozli xabar: Telegramdagidek bosib turib gapiriladi ----------------------
// Bosib turish — yozish, qo'yib yuborish — yuborish, chapga surish — bekor, tepaga surish — qulflash
// (qulflanganda qo'l bo'shaydi, «Yuborish» tugmasi chiqadi). Qisqa bosish — onTap.

const HOLD_MS = 200;
const CANCEL_DX = 90;
const LOCK_DY = 70;
const hold = { el: null, pointerId: null, x: 0, y: 0, timer: null, active: false };

function bindHoldToTalk(el, onTap) {
  el.addEventListener("contextmenu", (ev) => ev.preventDefault());
  el.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || hold.el) return;
    ev.preventDefault();
    if (compose.locked) return finishVoice(false); // qulflangan yozuvni shu tugma ham yuboradi
    if (compose.busy || compose.recorder || compose.starting) return;
    Object.assign(hold, { el, pointerId: ev.pointerId, x: ev.clientX, y: ev.clientY, active: false });
    try { el.setPointerCapture(ev.pointerId); } catch (_) { /* eski brauzer */ }
    hold.timer = setTimeout(() => {
      hold.active = true;
      startRecording();
    }, HOLD_MS);
  });
  el.addEventListener("pointermove", (ev) => {
    if (hold.el !== el || ev.pointerId !== hold.pointerId || !hold.active || compose.locked || !compose.recorder) return;
    const dx = Math.min(0, ev.clientX - hold.x);
    const dy = Math.min(0, ev.clientY - hold.y);
    $("recBar").style.setProperty("--dx", `${Math.max(dx, -CANCEL_DX)}px`);
    $("recBar").style.setProperty("--dy", `${Math.max(dy, -LOCK_DY)}px`);
    if (dx <= -CANCEL_DX) {
      releaseHold();
      finishVoice(true);
      toast("Bekor qilindi");
    } else if (dy <= -LOCK_DY) {
      releaseHold();
      lockRecording();
    }
  });
  const up = (ev) => {
    if (hold.el !== el || ev.pointerId !== hold.pointerId) return;
    const wasActive = hold.active;
    releaseHold();
    if (!wasActive) onTap();
    else if (!compose.locked) finishVoice(false);
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
}

function releaseHold() {
  clearTimeout(hold.timer);
  if (hold.el) try { hold.el.releasePointerCapture(hold.pointerId); } catch (_) { /* allaqachon qo'yilgan */ }
  hold.el = null;
  hold.active = false;
}

function pickMime() {
  if (!window.MediaRecorder) return null;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
}

function verticalSwipes(on) {
  // Yozayotganda barmoq tepaga surilsa yoki pastki oyna surilsa ilova yopilib qolmasin
  if (on && !$("sheet").hidden) return;
  try {
    if (!tg || !tg.isVersionAtLeast || !tg.isVersionAtLeast("7.7")) return;
    if (on) tg.enableVerticalSwipes();
    else tg.disableVerticalSwipes();
  } catch (_) { /* eski Telegram */ }
}

// Mikrofon bir marta ochiladi va ilova yopilguncha qayta ishlatiladi. Har yozuvda getUserMedia chaqirilib,
// keyin yopilsa, Telegram ichidagi brauzer (ayniqsa iPhone'da) har gal qaytadan ruxsat so'raydi.
let micStream = null;

async function getMic() {
  const tracks = micStream ? micStream.getAudioTracks() : [];
  if (tracks.length && tracks.every((t) => t.readyState === "live")) {
    tracks.forEach((t) => { t.enabled = true; });
    return micStream;
  }
  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  return micStream;
}

// Yozmayotganda ovoz olinmaydi, lekin ruxsat saqlanib qoladi
function pauseMic() {
  if (micStream) micStream.getAudioTracks().forEach((t) => { t.enabled = false; });
}

function releaseMic() {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  micStream = null;
}

async function startRecording() {
  const mime = pickMime();
  if (mime === null || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    releaseHold();
    toast("Bu qurilmada ovoz yozib bo'lmaydi — matn bilan yozing (klaviaturadagi 🎤 ham ishlaydi)", true);
    return;
  }
  compose.starting = true;
  compose.abort = false;
  verticalSwipes(false);
  setRecUI(true);
  let stream;
  try {
    stream = await getMic();
  } catch (_) {
    compose.starting = false;
    setRecUI(false);
    releaseHold();
    toast("Mikrofonga ruxsat berilmadi. Matn bilan yozing yoki klaviaturadagi 🎤 ni ishlating.", true);
    return;
  }
  compose.starting = false;
  if (compose.abort) {
    // Ruxsat so'ralayotganda barmoq qo'yib yuborildi — endi qayta bosib turish kifoya
    pauseMic();
    setRecUI(false);
    toast("🎤 Tayyor — endi tugmani bosib turib gapiring");
    return;
  }
  compose.stream = stream;
  compose.chunks = [];
  compose.cancel = false;
  compose.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  compose.recorder.ondataavailable = (ev) => ev.data.size && compose.chunks.push(ev.data);
  compose.recorder.onstop = onRecorded;
  compose.recorder.start();
  compose.started = Date.now();
  haptic("ok");
  const tick = () => {
    const sec = Math.floor((Date.now() - compose.started) / 1000);
    $("recClock").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
    if (sec >= MAX_RECORD_SEC) finishVoice(false);
  };
  tick();
  compose.timer = setInterval(tick, 250);
}

function setRecUI(on) {
  const bar = $("recBar");
  bar.hidden = !on;
  bar.classList.remove("locked");
  bar.style.setProperty("--dx", "0px");
  bar.style.setProperty("--dy", "0px");
  $("recHint").textContent = "‹ Bekor qilish uchun chapga suring";
  $("recSend").hidden = true;
  $("recClock").textContent = "0:00";
  $("fab").classList.toggle("rec", on);
  if ($("cMic")) $("cMic").classList.toggle("on", on);
  if (!on) {
    compose.locked = false;
    verticalSwipes(true);
  }
}

function lockRecording() {
  if (!compose.recorder) return;
  compose.locked = true;
  haptic();
  $("recBar").classList.add("locked");
  $("recHint").textContent = "✕ Bekor qilish";
  $("recSend").hidden = false;
}

// cancel=true — yozilgani yuborilmaydi
function finishVoice(cancel) {
  if (compose.starting) {
    compose.abort = true;
    return;
  }
  stopRecording(cancel);
}

function stopRecording(cancel) {
  const rec = compose.recorder;
  if (!rec) return;
  clearInterval(compose.timer);
  const tooShort = Date.now() - compose.started < 600;
  if (tooShort && !cancel) toast("Juda qisqa — tugmani bosib turib gapiring", true);
  compose.cancel = cancel || tooShort;
  compose.recorder = null;
  if (rec.state !== "inactive") rec.stop();
  else pauseMic();
  compose.stream = null;
  setRecUI(false);
}

function onRecorded(ev) {
  pauseMic(); // yozuv to'liq yig'ilgandan keyin
  const type = (ev.target.mimeType || compose.chunks[0]?.type || "audio/webm");
  const blob = new Blob(compose.chunks, { type });
  compose.chunks = [];
  if (compose.cancel) return;
  if (blob.size < 1500) {
    toast("Juda qisqa — tugmani bosib turib gapiring", true);
    return;
  }
  // Pastdagi 🎤 dan yozilgan bo'lsa — oyna ochilib, natija shu yerda chiqadi
  if ($("cOut") && !$("sheet").hidden) sendVoice(blob);
  else composeSheet(blob);
}

// Saqlangandan keyin ochiq bo'limdagi raqamlar yangilansin
function refreshView() {
  if (state.view === "home") loadDashboard();
  else if (state.view === "entries") loadEntries(true);
  else if (state.view === "wallet") loadWallet();
  else if (state.view === "clients") loadClients();
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
    <label class="switch-row"><div>🖼 Rasmdagi xarajatlarni darhol saqlash<small>O'chiq bo'lsa — rasmdan o'qilgani avval ko'rsatiladi va «✅ Tasdiqlash» ni bosganingizdan keyin hisobga qo'shiladi. Yoqilsa — tasdiqlamasdan darhol qo'shiladi. Avval kiritilganlari qayta qo'shilmaydi.</small></div>
      <span class="switch"><input type="checkbox" id="sAutoImg" ${me.auto_save_images ? "checked" : ""}><i></i></span></label>
    <div class="field"><label>📦 Bir komplektdagi dona (razmerlar) soni</label>
      <input class="input" id="sSetSize" type="number" inputmode="numeric" min="1" max="50" value="${me.set_size}">
    </div>
    <div class="field"><label>Valyutalar</label></div>
    ${curRows}
    <div class="field"><label>⭐ Asosiy valyuta — summa valyutasiz aytilsa shu olinadi</label>
      <select class="select" id="sMain">${["UZS", "USD", "KRW"].map((c) => `<option value="${c}" ${c === me.currencies[0] ? "selected" : ""}>${c} (${CUR[c]})</option>`).join("")}</select>
    </div>
    <div class="field"><label>🔔 Kundalik eslatma: «bugungi pulingizni kiriting»</label>
      <select class="select" id="sRemind"><option value="">🔕 O'chiq</option>${[19, 20, 21, 22].map((h) => `<option value="${h}" ${h === me.reminder_hour ? "selected" : ""}>har kuni ${h}:00</option>`).join("")}</select>
    </div>
  `);
  const save = async (body, input) => {
    try {
      const res = await api("/api/settings", { method: "PATCH", body });
      Object.assign(state.me, res);
      haptic();
      if (body.main_currency) settingsSheet();
    } catch (e) {
      fail(e);
      if (input && input.type === "checkbox") input.checked = !input.checked;
    }
  };
  $("sMain").onchange = (ev) => save({ main_currency: ev.target.value }, ev.target);
  $("sRemind").onchange = (ev) => save({ reminder_hour: ev.target.value === "" ? null : Number(ev.target.value) }, ev.target);
  $("sApproval").onchange = (ev) => save({ require_approval: ev.target.checked }, ev.target);
  $("sAutoImg").onchange = (ev) => save({ auto_save_images: ev.target.checked }, ev.target);
  $("sSetSize").onchange = (ev) => {
    const n = Number(ev.target.value);
    if (!Number.isInteger(n) || n < 1 || n > 50) {
      ev.target.value = me.set_size;
      return toast("1 dan 50 gacha son kiriting", true);
    }
    save({ set_size: n }).then(() => toast(`Komplekt = ${state.me.set_size} dona`));
  };
  document.querySelectorAll("[data-cur]").forEach((inp) => {
    inp.onchange = () => {
      const cur = [...document.querySelectorAll("[data-cur]")].filter((x) => x.checked).map((x) => x.dataset.cur);
      save({ currencies: cur }, inp).then(() => {
        $("sMain").value = state.me.currencies[0];
      });
    };
  });
}

// --- Maslahat: daromadni taqsimlash, kunlik limit, saboqlar -------------------

function planRow(icon, title, part, cur, savings) {
  const used = savings ? part.saved : part.spent;
  const pct = part.budget > 0 ? Math.min(100, Math.round((used / part.budget) * 100)) : 0;
  const over = !savings && used > part.budget;
  let note;
  if (savings) note = used >= part.budget ? "✅ shu oy ortib turibdi" : `shu oy ortib turgani: ${money(used, cur)}`;
  else note = `sarflandi ${money(used, cur)} · ` + (over ? `⚠️ ${money(used - part.budget, cur)} oshdi` : `qoldi ${money(part.budget - used, cur)}`);
  return `
    <div class="plan-row">
      <div class="plan-head"><b>${icon} ${title} <span class="muted">${part.pct}%</span></b><b>${money(part.budget, cur)}</b></div>
      <div class="bar ${over ? "over" : savings ? "save" : ""}"><i style="width:${pct}%"></i></div>
      <small>${note}</small>
    </div>`;
}

function budgetBlock(b, multi) {
  const cur = b.currency;
  const title = multi ? `<div class="advice-cur">${esc(CUR[cur] || cur)}</div>` : "";
  if (!b.needs) {
    return `${title}<div class="advice-card"><b>💸 Bu oy xarajat: ${money(b.spent, cur)}</b>
      <p class="muted">Daromad hali yozilmagan. Taqsimot uchun topgan pulingizni ham yozing: «oylik tushdi 5 million», «bugun 300 ming ishladim».</p></div>`;
  }
  const limit = b.spend_left > 0
    ? `<div><b>${money(b.daily_limit, cur)}</b><small>kunlik limit · ${b.days_left} kun qoldi</small></div>`
    : `<div><b class="amt neg">${money(-b.spend_left, cur)}</b><small>oylik rejadan oshdi</small></div>`;
  return `${title}
    <div class="advice-card">
      <div class="muted">${b.from_avg ? "O'rtacha oylik daromad (bu oy hali yozilmagan)" : "Bu oy daromad"}</div>
      <div class="advice-income">${money(b.base, cur)}</div>
      <div class="muted" style="margin-top:10px">Shu puldan ajrating:</div>
      ${planRow("🏠", "Zarur xarajatlar", b.needs, cur)}
      ${planRow("🎉", "Boshqa xarajatlar", b.wants, cur)}
      ${planRow("🏦", "Jamg'arma", b.savings, cur, true)}
    </div>
    <div class="stat-pair">${limit}<div><b>${money(b.today_spent, cur)}</b><small>bugun sarflandi</small></div></div>
    ${b.reserve ? `<div class="advice-note">🛟 <b>Zaxira fond maqsadi:</b> kamida ${money(b.reserve[0], cur)} (3 oylik xarajat), yaxshisi ${money(b.reserve[1], cur)} (6 oylik). Zaxira yig'ilgach — ortig'ini foizli omonatga emas, halol investitsiyaga yo'naltiring.</div>` : ""}
    ${b.tips.length ? `<div class="advice-tips">${b.tips.map((t) => `<p>${tgHtml(t)}</p>`).join("")}</div>` : ""}`;
}

async function adviceSheet() {
  haptic();
  openSheet(`<h3 id="advTitle">💡 Maslahat</h3><div id="advBody">${SKELETONS}</div>`);
  try {
    state.advice = await api("/api/advice");
    renderAdvice();
  } catch (e) { fail(e); }
}

function renderAdvice() {
  const d = state.advice;
  if (!d || !$("advBody")) return;
  $("advTitle").textContent = `💡 Maslahat — ${d.month}`;
  const wantsPct = 100 - 50 - d.savings_pct;
  const blocks = d.budgets.length
    ? d.budgets.map((b) => budgetBlock(b, d.budgets.length > 1)).join("")
    : `<div class="advice-card"><b>Hali daromad yoki xarajat yozilmagan.</b>
        <p class="muted">Avval topgan pulingizni yozing: «oylik tushdi 5 million» — bot uni qanday taqsimlashni ko'rsatadi. Keyin har kuni xarajatlaringizni yozib boring: «bozorga 80 ming, taksiga 20 ming».</p></div>`;
  const hourOpts = [`<option value="">🔕 O'chiq</option>`]
    .concat(d.reminder_hours.map((h) => `<option value="${h}" ${h === d.reminder_hour ? "selected" : ""}>🔔 har kuni ${h}:00</option>`)).join("");
  $("advBody").innerHTML = `
    <div class="field"><label>🏦 Daromadning qancha qismi jamg'armaga</label>
      <div class="seg three" id="advPct">${d.savings_choices.map((p) => `<button data-pct="${p}" class="${p === d.savings_pct ? "on" : ""}">${p}%</button>`).join("")}</div>
    </div>
    ${blocks}
    <button class="primary" id="advAi">🤖 Shaxsiy maslahat olish</button>
    <div id="advAiOut"></div>
    <div class="advice-lesson" id="advLesson"></div>
    <div class="field" style="margin-top:14px"><label>Kundalik eslatma: «bugungi pulingizni kiriting»</label>
      <select class="select" id="advRemind">${hourOpts}</select>
    </div>
    <p class="muted">Qoida: daromadning 50% — zarur xarajatlar (ovqat, uy, kommunal, yo'l), ${wantsPct}% — boshqa xarajatlar (kafe, kiyim, dam olish), ${d.savings_pct}% — jamg'arma.</p>`;
  renderLesson(d.lesson);

  $("advPct").onclick = async (ev) => {
    const b = ev.target.closest("[data-pct]");
    if (!b || Number(b.dataset.pct) === d.savings_pct) return;
    try {
      const res = await api("/api/settings", { method: "PATCH", body: { savings_pct: Number(b.dataset.pct) } });
      Object.assign(state.me, res);
      haptic();
      state.advice = await api("/api/advice");
      renderAdvice();
    } catch (e) { fail(e); }
  };
  $("advRemind").onchange = async (ev) => {
    const v = ev.target.value;
    try {
      const res = await api("/api/settings", { method: "PATCH", body: { reminder_hour: v === "" ? null : Number(v) } });
      Object.assign(state.me, res);
      d.reminder_hour = res.reminder_hour;
      haptic();
      toast(v === "" ? "Eslatma o'chirildi" : `Har kuni ${v}:00 da eslatiladi`);
    } catch (e) { fail(e); }
  };
  $("advAi").onclick = async () => {
    const btn = $("advAi");
    btn.disabled = true;
    btn.textContent = "🤖 Tahlil qilinmoqda…";
    try {
      const res = await api("/api/advice/ai", { method: "POST" });
      if (!$("advAiOut")) return;
      $("advAiOut").innerHTML = `<div class="advice-ai">${esc(res.text).replace(/\n/g, "<br>")}</div>`;
      btn.hidden = true;
      haptic("ok");
    } catch (e) {
      fail(e);
      btn.disabled = false;
      btn.textContent = "🤖 Shaxsiy maslahat olish";
    }
  };
}

function renderLesson(index) {
  const lessons = state.advice.lessons;
  const i = ((index % lessons.length) + lessons.length) % lessons.length;
  const box = $("advLesson");
  if (!box) return;
  box.innerHTML = `<span class="muted">📚 Saboq ${i + 1}/${lessons.length}</span>${tgHtml(lessons[i])}
    <button class="secondary" id="advNextLesson">Keyingi saboq ›</button>`;
  $("advNextLesson").onclick = () => { haptic(); renderLesson(i + 1); };
}

// --- Pastki oyna -------------------------------------------------------------

function openSheet(html) {
  resetSheetDrag();
  $("sheetBody").innerHTML = html;
  $("sheetBody").scrollTop = 0;
  $("sheet").hidden = false;
  $("sheetBackdrop").hidden = false;
  verticalSwipes(false); // oynani pastga surganda Telegram ilovani yopib qo'ymasin
  if (tg && tg.BackButton) tg.BackButton.show();
}
function closeSheet() {
  stopRecording(true);
  resetSheetDrag();
  $("sheet").hidden = true;
  $("sheetBackdrop").hidden = true;
  verticalSwipes(true);
  if (tg && tg.BackButton) tg.BackButton.hide();
}

// Oynani pastga surib yopish: tutqichdan yoki ichi eng tepada turganda istalgan joyidan
const SHEET_CLOSE_DY = 110;
const drag = { x: 0, y: 0, t: 0, dy: 0, v: 0, head: false, ok: false, on: false, timer: null };

function resetSheetDrag() {
  clearTimeout(drag.timer);
  drag.on = false;
  for (const el of [$("sheet"), $("sheetBackdrop")]) {
    el.style.transition = "";
    el.style.transform = "";
    el.style.opacity = "";
  }
}

function bindSheetDrag() {
  const sheet = $("sheet");
  const backdrop = $("sheetBackdrop");
  sheet.addEventListener("touchstart", (ev) => {
    if (drag.on) return;
    const t = ev.touches[0];
    const head = $("sheetHead").contains(ev.target);
    const busy = compose.recorder || ev.target.closest(".rec-btn, textarea, input, select");
    Object.assign(drag, {
      x: t.clientX, y: t.clientY, dy: 0, head,
      ok: ev.touches.length === 1 && !busy && (head || $("sheetBody").scrollTop <= 0),
    });
  }, { passive: true });
  sheet.addEventListener("touchmove", (ev) => {
    if (!drag.ok || ev.touches.length !== 1) return;
    const t = ev.touches[0];
    if (!drag.on) {
      const dy = t.clientY - drag.y;
      // Tepaga yoki yonga surilsa — ichidagi ro'yxat odatdagidek aylanadi
      if (!ev.cancelable || dy < 0 || Math.abs(t.clientX - drag.x) > Math.max(dy, 8)) {
        drag.ok = drag.head && ev.cancelable;
        return;
      }
      ev.preventDefault(); // ichi eng tepada: brauzer sahifani cho'zmasin
      if (dy < 8) return;
      clearTimeout(drag.timer);
      Object.assign(drag, { on: true, y: t.clientY, t: ev.timeStamp, v: 0 }); // oyna shu nuqtadan suriladi — sakramaydi
      sheet.style.transition = backdrop.style.transition = "none";
    }
    ev.preventDefault();
    const dy = Math.max(0, t.clientY - drag.y);
    // Tezlik (px/ms) — oxirgi harakatlar bo'yicha: tez silkitilsa qisqa surishda ham yopiladi
    const v = (dy - drag.dy) / Math.max(1, ev.timeStamp - drag.t);
    drag.v = drag.v ? 0.6 * v + 0.4 * drag.v : v;
    drag.dy = dy;
    drag.t = ev.timeStamp;
    sheet.style.transform = `translate(-50%, ${drag.dy}px)`;
    backdrop.style.opacity = String(Math.max(0, 1 - drag.dy / sheet.offsetHeight));
  }, { passive: false });
  const end = (ev) => {
    if (!drag.on) return;
    drag.on = false;
    const fast = drag.dy > 40 && drag.v > 0.5;
    sheet.style.transition = "transform .22s cubic-bezier(.2, .8, .2, 1)";
    backdrop.style.transition = "opacity .22s";
    if (drag.dy > SHEET_CLOSE_DY || fast) {
      haptic();
      sheet.style.transform = `translate(-50%, ${sheet.offsetHeight + 20}px)`;
      backdrop.style.opacity = "0";
      drag.timer = setTimeout(closeSheet, 220);
    } else {
      sheet.style.transform = "";
      backdrop.style.opacity = "";
      drag.timer = setTimeout(resetSheetDrag, 220);
    }
  };
  sheet.addEventListener("touchend", end);
  sheet.addEventListener("touchcancel", end);
}

function syncChips() {
  const en = state.entries;
  const isWork = en.type === "work";
  document.querySelectorAll("#typeChips button").forEach((b) => b.classList.toggle("on", b.dataset.type === en.type));
  document.querySelectorAll("#statusChips button").forEach((b) => b.classList.toggle("on", b.dataset.status === en.status));
  $("statusChips").hidden = !isWork;
  $("workerFilter").hidden = !isWork || !state.me.is_owner || state.workers.length === 0;
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
    state.entries.type = "work";
    state.entries.status = "pending";
    state.entries.workerId = "";
    syncChips();
    show("entries");
  };

  $("typeChips").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.type === state.entries.type) return;
    state.entries.type = b.dataset.type;
    state.entries.status = "";
    state.entries.workerId = "";
    $("workerFilter").value = "";
    syncChips();
    haptic();
    loadEntries(true);
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
    const del = ev.target.closest("[data-del-feed]");
    if (del) {
      const [type, id] = del.dataset.delFeed.split(":");
      deleteFeedItem(type, Number(id));
      return;
    }
    const b = ev.target.closest("[data-act]");
    if (b) entryAction(b.dataset.act, Number(b.dataset.id));
  };

  // Asosiy: qarzlar va harakatlar
  $("mDebtList").onclick = (ev) => {
    const b = ev.target.closest("[data-person]");
    if (b) personSheet(b.dataset.person);
  };
  $("mDebtsAll").onclick = () => {
    state.wallet.tab = "debts";
    show("wallet");
  };
  const openClient = (ev) => {
    const b = ev.target.closest("[data-client]");
    if (b) clientSheet(Number(b.dataset.client));
  };
  $("mClientList").onclick = openClient;
  $("clientList").onclick = openClient;
  $("mClientsAll").onclick = () => show("clients");
  $("addClientBtn").onclick = () => clientEditSheet(null);
  $("cMoreBtn").onclick = () => loadClientFeed(false);
  $("cFeed").onclick = (ev) => {
    const btn = ev.target.closest("[data-del-feed]");
    if (btn) deleteClientEntry(Number(btn.dataset.delFeed.split(":")[1]));
  };
  $("cPeriodSeg").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.period === state.clients.period) return;
    state.clients.period = b.dataset.period;
    document.querySelectorAll("#cPeriodSeg button").forEach((x) => x.classList.toggle("on", x === b));
    haptic();
    loadClients();
  };
  $("mFeedAll").onclick = () => {
    state.entries.type = "";
    state.entries.status = "";
    state.entries.workerId = "";
    $("workerFilter").value = "";
    syncChips();
    show("entries");
  };

  $("workerList").onclick = (ev) => {
    const b = ev.target.closest("[data-worker]");
    if (!b) return;
    const w = state.workers.find((x) => x.id === Number(b.dataset.worker));
    if (w) workerSheet(w);
  };
  $("addWorkerBtn").onclick = () => addWorkerSheet();
  $("jobChips").onclick = (ev) => {
    const b = ev.target.closest("[data-job]");
    if (!b) return;
    if (b.dataset.job === "new") jobSheet(null);
    else jobSheet(state.jobs.find((j) => j.id === Number(b.dataset.job)));
  };

  $("modelList").onclick = (ev) => {
    if (!state.me.is_owner) return;
    const b = ev.target.closest("[data-model]");
    if (!b) return;
    const m = state.models.find((x) => x.id === Number(b.dataset.model));
    if (m) modelSheet(m);
  };
  $("addModelBtn").onclick = () => modelSheet(null);
  document.querySelectorAll(".team-seg").forEach((seg) => {
    seg.onclick = (ev) => {
      const b = ev.target.closest("button");
      if (b && b.dataset.team !== state.view) show(b.dataset.team);
    };
  });

  // Hamyon
  $("walletTabs").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.tab === state.wallet.tab) return;
    state.wallet.tab = b.dataset.tab;
    haptic();
    loadWallet();
  };
  $("wPeriodSeg").onclick = (ev) => {
    const b = ev.target.closest("button");
    if (!b || b.dataset.period === state.wallet.period) return;
    state.wallet.period = b.dataset.period;
    document.querySelectorAll("#wPeriodSeg button").forEach((x) => x.classList.toggle("on", x === b));
    haptic();
    loadPersonal();
  };
  $("wMoreBtn").onclick = () => loadPersonalEntries(false);
  $("wEntryList").onclick = (ev) => {
    const b = ev.target.closest("[data-del-personal]");
    if (b) deletePersonal(Number(b.dataset.delPersonal));
  };
  $("debtList").onclick = (ev) => {
    const b = ev.target.closest("[data-person]");
    if (b) personSheet(b.dataset.person);
  };

  window.addEventListener("pagehide", releaseMic);
  $("settingsBtn").onclick = settingsSheet;
  // Summa kartasini bosganda — maslahat va reja
  for (const id of ["wHero", "mHero"]) {
    $(id).onclick = adviceSheet;
    $(id).onkeydown = (ev) => { if (ev.key === "Enter") adviceSheet(); };
  }
  $("sheetBackdrop").onclick = closeSheet;
  bindSheetDrag();

  // O'rtadagi tugma: bosib turish — ovozli xabar, bir marta bosish — matn bilan yozish oynasi
  bindHoldToTalk($("fab"), () => {
    haptic("ok");
    composeSheet();
  });
  // Kompyuterda: skrinshot yoki chekni ilova ustiga sudrab tashlash
  const hasFiles = (ev) => ev.dataTransfer && [...ev.dataTransfer.types].includes("Files");
  document.addEventListener("dragover", (ev) => { if (hasFiles(ev)) ev.preventDefault(); });
  document.addEventListener("drop", (ev) => {
    if (!hasFiles(ev)) return;
    ev.preventDefault();
    takeImages(imageFiles(ev.dataTransfer.files));
  });
  // 📷 tugma — kamera yoki galereya
  $("cameraBtn").onclick = () => {
    haptic();
    if (!compose.busy) pickImageSheet();
  };
  const onPicked = (ev) => {
    const files = imageFiles(ev.target.files);
    ev.target.value = "";
    if (files.length) takeImages(files);
  };
  $("camInput").onchange = onPicked;
  $("galInput").onchange = onPicked;
  $("recSend").onclick = () => finishVoice(false);
  $("recHint").onclick = () => {
    if (!compose.locked) return;
    finishVoice(true);
    toast("Bekor qilindi");
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
  loadAvatar();
  $("settingsBtn").hidden = !me.is_owner;
  $("cameraBtn").hidden = false;
  $("addModelBtn").hidden = !me.is_owner;
  // Egada: Hamyon va Jamoa (ishchilar + modellar). Ishchida: Narxlar va Yordam
  $("navWallet").hidden = !me.is_owner;
  // Egada: Asosiy · Mijozlar · 🎤 · Hamyon · Jamoa («Yozuvlar» — bosh sahifadagi «Hammasi →» orqali)
  $("navEntries").hidden = me.is_owner;
  $("navClients").hidden = !me.is_owner;
  $("navWorkers").hidden = !me.is_owner;
  $("navModels").hidden = me.is_owner;
  $("navHelp").hidden = me.is_owner;
  document.querySelectorAll(".team-seg").forEach((el) => (el.hidden = !me.is_owner));
  // Egada bosh sahifada — pullar holati, «Yozuvlar»da — barcha turdagi yozuvlar
  $("moneySection").hidden = !me.is_owner;
  $("workTitle").hidden = !me.is_owner;
  $("typeChips").hidden = !me.is_owner;
  if (!me.is_owner) state.entries.type = "work";
  syncChips();
  if (!me.is_owner) {
    $("modelsTitle").textContent = "Narxlar";
    $("modelsHint").textContent = me.job
      ? `Bir dona uchun ish haqi — kasbingiz (${me.job.name}) bo'yicha`
      : "Narx — bir dona uchun ish haqi";
  }
  await fillWorkerFilter();
  show(startView());
}

// Logotip o'rnida — Telegram profil rasmi (bo'lmasa logotip qoladi)
async function loadAvatar() {
  const setImg = (src, onError) => {
    const img = new Image();
    img.alt = "";
    img.onload = () => $("logo").replaceChildren(img);
    if (onError) img.onerror = onError;
    img.src = src;
  };
  const fromServer = async () => {
    try {
      const res = await fetch("/api/avatar", { headers: authHeaders() });
      if (!res.ok) return;
      setImg(URL.createObjectURL(await res.blob()));
    } catch (_) { /* rasm bo'lmasa logotip qoladi */ }
  };
  const photoUrl = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.photo_url;
  if (photoUrl) setImg(photoUrl, fromServer);
  else fromServer();
}

// Botdagi «Ilovani ochish» tugmasi kerakli bo'limni ochadi: ?view=debts | wallet | entries
function startView() {
  const view = new URLSearchParams(location.search).get("view");
  if (view === "entries") return "entries";
  if (!state.me.is_owner) return "home";
  if (view === "clients") return "clients";
  if (view === "debts" || view === "wallet") {
    state.wallet.tab = view === "debts" ? "debts" : "money";
    return "wallet";
  }
  return "home";
}

init();
