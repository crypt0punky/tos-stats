// tos-stats frontend.
// Загружает data/current.json, рендерит главную сетку + detail views.
// Routing через hash: #/ -> main, #/EURUSD -> detail.

"use strict";

let DATA = null;
let activeChart = null;

// =====================
// FETCH + BOOT
// =====================

async function loadData() {
  // Cache-bust по week ID чтобы юзер видел свежие данные после weekly push.
  const url = "data/current.json?v=" + Date.now();
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`fetch current.json: ${resp.status}`);
  return await resp.json();
}

async function boot() {
  try {
    DATA = await loadData();
  } catch (e) {
    console.error(e);
    document.getElementById("view-main").classList.add("hide");
    document.getElementById("view-detail").classList.add("hide");
    document.getElementById("view-error").classList.remove("hide");
    return;
  }
  setHeroMeta();
  renderMain();
  colorizeNarrativeNumbers();

  const initHash = location.hash.replace(/^#\//, "");
  if (initHash) route("detail", initHash);
}

// =====================
// HELPERS
// =====================

function fmt(n) {
  if (n === 0) return "0";
  return (n > 0 ? "+" : "-") + Math.abs(n).toLocaleString("en-US");
}
function fmtPlain(n) {
  return n.toLocaleString("en-US");
}
function tagLabel(t) {
  return { extreme: "Крайнее", stretched: "Растянуто", momentum: "Импульс", neutral: "Нейтрально" }[t] || t;
}
function directionOf(p) {
  if (!p || p.tag === "neutral") return null;
  return p.am_net >= 0 ? "long" : "short";
}
function setHeroMeta() {
  document.getElementById("meta-week").textContent = `${DATA.week} · ${DATA.year}`;
  const upd = (DATA.updated_at || "").slice(0, 10).replace(/-/g, ".");
  document.getElementById("meta-updated").textContent = upd
    ? `${upd.split(".").reverse().join(".")}`
    : "-";
}

function makeSparklineFromHistory(pair) {
  // Если у нас есть history -- строим спарклайн по реальным AM Net последних 12 недель.
  const h = DATA.history && DATA.history[pair];
  if (!h || h.length < 2) {
    // Падаем на синтетику если history нет (smoke test без backfill).
    return makeSyntheticSpark(pair.length);
  }
  const values = h.map(r => r.am).reverse();  // от старого к новому
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const range = hi - lo || 1;
  return values.map((v, i) => {
    const x = (200 * i) / (values.length - 1);
    const y = 32 - ((v - lo) / range) * 28; // отрисовка в окне 4..32 (Y инвертирован)
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function makeSyntheticSpark(seed) {
  const points = [];
  let v = 18;
  for (let i = 0; i < 13; i++) {
    v += Math.sin(seed * 1.3 + i * 0.7) * 2;
    v = Math.max(4, Math.min(32, v));
    points.push(`${(200 * i / 12).toFixed(1)},${v.toFixed(1)}`);
  }
  return points.join(" ");
}

function makeWilliamsHistorySeries(pair) {
  // Для 3y chart нам нужна серия Williams percentile по неделям.
  // У нас в JSON есть только текущий + table_weeks_show раз репортов.
  // Полная серия восстанавливается из истории AM Net по rolling 3y окну,
  // но для frontend проще: посчитаем Williams для каждой точки в имеющейся истории
  // относительно полного 3y окна (это approx -- backend в идеале передает готовую серию).
  const h = DATA.history && DATA.history[pair];
  if (!h || h.length === 0) return makeFallbackSeries(50);

  const values = h.map(r => r.am).reverse(); // старые -> новые
  if (values.length < 4) return makeFallbackSeries(values[values.length - 1] || 50);

  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (hi === lo) return values.map(() => 50);

  return values.map(v => Math.round(((v - lo) / (hi - lo)) * 100));
}

function makeFallbackSeries(williamsNow) {
  // Когда нет реальной истории - синтетика просто чтобы chart не падал.
  const pts = [];
  for (let i = 0; i < 26; i++) {
    const progress = i / 25;
    const base = 50 + (williamsNow - 50) * Math.pow(progress, 1.2);
    const noise = (Math.sin(i * 0.4) * 10) + (Math.sin(i * 0.13) * 6);
    pts.push(Math.max(0, Math.min(100, Math.round(base + noise))));
  }
  return pts;
}

// =====================
// RENDER MAIN
// =====================

function renderMain() {
  // TLDR показываем только если есть реальный контент, не placeholder.
  const tldrSection = document.getElementById("tldr-section");
  const tldr = (DATA.tldr || "").trim();
  const isPlaceholder = tldr.toLowerCase().includes("placeholder") || tldr.toLowerCase().includes("initial");
  if (tldr && !isPlaceholder) {
    document.getElementById("tldr-body").innerHTML = tldr;
    tldrSection.classList.remove("hide");
  } else {
    tldrSection.classList.add("hide");
  }

  const grid = document.getElementById("pairs-grid");
  grid.innerHTML = DATA.pairs.map(p => {
    const spark = makeSparklineFromHistory(p.id);
    const dir = directionOf(p);
    const dirClass = dir ? `dir-${dir}` : "";
    const dotHtml = dir ? `<span class="tag-dot ${dir}"></span>` : "";
    const numDirClass = dir ? `dir-${dir}` : "";
    const amNetClass = dir ? (p.am_net >= 0 ? "delta-pos" : "delta-neg") : "";
    return `
      <div class="card ${dirClass}" data-pair="${p.id}">
        <div class="card-head">
          <div class="card-ticker">${p.id}</div>
          <span class="tag tag-${p.tag}">${dotHtml}${tagLabel(p.tag)}</span>
        </div>
        <div class="card-metric">
          <span class="card-bignum ${numDirClass}">${p.williams.w3y}</span>
          <span class="card-unit">Williams 3y</span>
        </div>
        <svg class="spark" viewBox="0 0 200 36" preserveAspectRatio="none">
          <polyline points="${spark}" fill="none" stroke="#0a0a0a" stroke-width="1.5"/>
        </svg>
        <div class="card-meta">
          <div class="card-meta-item"><span class="label">AM Net</span><span class="value ${amNetClass}">${fmtPlain(p.am_net)}</span></div>
          <div class="card-meta-item"><span class="label">Неделя</span><span class="value ${p.am_wow >= 0 ? "delta-pos" : "delta-neg"}">${fmt(p.am_wow)}</span></div>
        </div>
      </div>
    `;
  }).join("");

  // Click handlers на cards.
  grid.querySelectorAll(".card").forEach(el => {
    el.addEventListener("click", () => route("detail", el.dataset.pair));
  });

  // DXY aggregate card.
  const d = DATA.dxy_aggregate;
  const dxyCard = document.getElementById("dxy-card");
  if (!d) { dxyCard.innerHTML = ""; return; }
  const dxyTag = d.tag;
  const dxyDir = dxyTag === "neutral" ? null : (d.weighted_net >= 0 ? "long" : "short");
  const dxyDotHtml = dxyDir ? `<span class="tag-dot ${dxyDir}"></span>` : "";
  const dxyDirClass = dxyDir ? `dir-${dxyDir}` : "";

  dxyCard.innerHTML = `
    <div class="card ${dxyDirClass}" data-pair="DXY">
      <div class="card-head">
        <div class="card-ticker">DXY POSITIONING <span style="color: var(--color-midtone-gray); font-family: var(--font-geist); font-size: 11px; letter-spacing: 0.06em; margin-left: 8px; text-transform: uppercase;">Агрегат · TOS Custom</span></div>
        <span class="tag tag-${dxyTag}">${dxyDotHtml}${tagLabel(dxyTag)}</span>
      </div>
      <div class="dxy-card-inner">
        <div>
          <div class="card-metric"><span class="card-bignum ${dxyDirClass}">${d.williams.w3y}</span><span class="card-unit">Williams 3y · ${d.williams.w3y} перцентиль</span></div>
          <div style="font-size: 13px; color: var(--color-rich-black); margin-top: 8px; max-width: 320px;">Совокупная позиция по доллару через 6 пар G10. Веса по индексу DXY: EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, AUD 3.9%, NZD 3.9%.</div>
        </div>
        <svg class="spark" style="height: 56px;" viewBox="0 0 400 56" preserveAspectRatio="none">
          <polyline points="0,38 33,36 67,34 100,33 133,30 167,28 200,25 233,22 267,18 300,14 333,10 367,8 400,6" fill="none" stroke="#0a0a0a" stroke-width="2"/>
          <line x1="0" y1="48" x2="400" y2="48" stroke="#e5e5e5" stroke-width="1" stroke-dasharray="2 3"/>
          <line x1="0" y1="8" x2="400" y2="8" stroke="#e5e5e5" stroke-width="1" stroke-dasharray="2 3"/>
          <text x="4" y="54" font-size="10" fill="#737373" font-family="Geist Mono">0%</text>
          <text x="4" y="14" font-size="10" fill="#737373" font-family="Geist Mono">100%</text>
        </svg>
      </div>
    </div>
  `;
  dxyCard.querySelector(".card").addEventListener("click", () => route("detail", "DXY"));
}

// =====================
// RENDER DETAIL
// =====================

function renderDetail(pairId) {
  if (activeChart) { activeChart.destroy(); activeChart = null; }

  let p;
  if (pairId === "DXY") {
    const d = DATA.dxy_aggregate;
    p = {
      id: "DXY POSITIONING",
      tag: d.tag,
      williams: d.williams,
      am_net: d.weighted_net,
      am_wow: d.wow,
      am_mom: d.mom,
      am_3m: d.m3,
      narrative: d.narrative,
      watch: [],
      isAggregate: true,
    };
  } else {
    p = DATA.pairs.find(x => x.id === pairId);
  }
  if (!p) return;

  const dir = directionOf(p) || (p.isAggregate && p.am_net >= 0 ? "long" : (p.isAggregate ? "short" : null));
  const dotHtml = dir ? `<span class="tag-dot ${dir}"></span>` : "";
  const headerDirClass = dir ? `dir-${dir}` : "";
  const dataLabel = `Неделя ${DATA.week} · ${DATA.year}`;

  const c = document.getElementById("detail-content");
  c.innerHTML = `
    <div class="detail-header ${headerDirClass}" style="padding-left: ${dir ? "24px" : "0"};">
      <div>
        <div class="detail-title-row">
          <div class="detail-title">${p.id}</div>
          <span class="tag tag-${p.tag}" style="font-size: 12px; padding: 4px 14px;">${dotHtml}${tagLabel(p.tag)}</span>
        </div>
        <div class="detail-sub">Williams 3y перцентиль · CFTC TFF · ${dataLabel}</div>
      </div>
      <div class="detail-stats">
        <div class="detail-stat"><span class="label">Williams 3y</span><span class="value ${dir ? "dir-" + dir : ""}">${p.williams.w3y}</span></div>
        <div class="detail-stat"><span class="label">${p.isAggregate ? "Агрегат нетто" : "AM Net"}</span><span class="value mono ${dir ? (p.am_net >= 0 ? "dir-long" : "dir-short") : ""}">${fmtPlain(p.am_net)}</span></div>
        <div class="detail-stat"><span class="label">Неделя</span><span class="value mono ${p.am_wow >= 0 ? "delta-pos" : "delta-neg"}">${fmt(p.am_wow)}</span></div>
      </div>
    </div>

    <div class="windows-strip">
      <div class="window-stat"><span class="label">Williams 3y</span><span class="value ${dir ? "dir-" + dir : ""}">${p.williams.w3y}</span></div>
      <div class="window-stat"><span class="label">Williams 1y</span><span class="value ${dir ? "dir-" + dir : ""}">${p.williams.w1y}</span></div>
      <div class="window-stat"><span class="label">Williams 6m</span><span class="value ${dir ? "dir-" + dir : ""}">${p.williams.w6m}</span></div>
      <div class="window-stat"><span class="label">Месяц Δ</span><span class="value ${p.am_mom >= 0 ? "delta-pos" : "delta-neg"}">${fmt(p.am_mom)}</span></div>
      <div class="window-stat"><span class="label">3 мес Δ</span><span class="value ${p.am_3m >= 0 ? "delta-pos" : "delta-neg"}">${fmt(p.am_3m)}</span></div>
    </div>

    <div class="chart-card">
      <span class="label">Williams index (0-100), история</span>
      <div class="chart-wrap"><canvas id="historyChart" role="img" aria-label="Williams percentile history"></canvas></div>
    </div>

    <div class="writeup">
      <div class="head">
        <div class="head-title">Разбор от TOS AI</div>
        <div class="head-source">${dataLabel}</div>
      </div>
      <div class="writeup-section">
        <span class="stitle">Срез</span>
        <div class="body">${p.narrative.snapshot || ""}</div>
      </div>
      <div class="writeup-section">
        <span class="stitle">Динамика</span>
        <div class="body">${p.narrative.dynamics || ""}</div>
      </div>
      <div class="writeup-section">
        <span class="stitle">История</span>
        <div class="body">${p.narrative.historical || ""}</div>
      </div>
      <div class="writeup-section">
        <span class="stitle">В связке</span>
        <div class="body">${p.narrative.cross_pair || ""}</div>
      </div>
    </div>

    ${p.watch && p.watch.length ? `
    <div class="watch-card">
      <span class="label">Что отслеживать на неделе</span>
      <ul class="watch-list">${p.watch.map(w => `<li>${w}</li>`).join("")}</ul>
    </div>
    ` : ""}

    ${!p.isAggregate ? `
    <div class="reports-card">
      <span class="label">Последние ${DATA.history && DATA.history[pairId] ? DATA.history[pairId].length : 0} еженедельных репортов</span>
      <table class="reports-table">
        <thead><tr><th>Дата</th><th class="num">AM Net</th><th class="num">Неделя Δ</th><th class="num">LF Net</th><th class="num">Неделя Δ</th><th class="num">OI</th></tr></thead>
        <tbody>${renderReportsRows((DATA.history && DATA.history[pairId]) || [])}</tbody>
      </table>
    </div>
    ` : ""}
  `;

  renderHistoryChart(pairId);
}

function renderHistoryChart(pairId) {
  const series = pairId === "DXY"
    ? makeFallbackSeries(DATA.dxy_aggregate.williams.w3y)  // Aggregate -- нет таблицы history, fallback
    : makeWilliamsHistorySeries(pairId);

  const ctx = document.getElementById("historyChart").getContext("2d");
  activeChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: series.map(() => ""),
      datasets: [{
        data: series,
        borderColor: "#0a0a0a",
        borderWidth: 1.5,
        pointRadius: 0,
        fill: false,
        tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, grid: { display: false } },
        y: {
          min: 0, max: 100,
          ticks: { color: "#737373", font: { family: "Geist Mono", size: 11 }, stepSize: 25 },
          grid: { color: "#e5e5e5", drawTicks: false },
        },
      },
      animation: { duration: 0 },
    },
  });
}

// =====================
// REPORTS TABLE
// =====================

function percentile(arr, p) {
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.floor(sorted.length * p);
  return sorted[Math.min(idx, sorted.length - 1)];
}

function renderReportsRows(rows) {
  if (!rows.length) return `<tr><td colspan="6" style="text-align: center; color: var(--color-midtone-gray); padding: 24px;">История пока пуста, появится после первого ингеста.</td></tr>`;
  const amDeltas = rows.map(r => Math.abs(r.am_d || 0));
  const lfDeltas = rows.map(r => Math.abs(r.lf_d || 0));
  const amThresh = percentile(amDeltas, 0.7) || 1;
  const lfThresh = percentile(lfDeltas, 0.7) || 1;

  return rows.map((r, i) => {
    const isCurrent = i === 0;
    const amHot = Math.abs(r.am_d || 0) >= amThresh ? (r.am_d >= 0 ? "hot-pos" : "hot-neg") : "";
    const lfHot = Math.abs(r.lf_d || 0) >= lfThresh ? (r.lf_d >= 0 ? "hot-pos" : "hot-neg") : "";
    const dateFmt = r.date.split("-").reverse().join(".");
    return `<tr class="${isCurrent ? "current-row" : ""}">
      <td>${dateFmt}</td>
      <td class="num">${fmtPlain(r.am)}</td>
      <td class="num ${amHot}">${fmt(r.am_d || 0)}</td>
      <td class="num">${fmtPlain(r.lf)}</td>
      <td class="num ${lfHot}">${fmt(r.lf_d || 0)}</td>
      <td class="num">${fmtPlain(r.oi)}</td>
    </tr>`;
  }).join("");
}

// =====================
// ROUTING + COLORIZE
// =====================

function route(view, pairId) {
  if (view === "main") {
    document.getElementById("view-main").classList.remove("hide");
    document.getElementById("view-detail").classList.add("hide");
    window.scrollTo(0, 0);
    history.pushState({}, "", "#/");
  } else if (view === "detail") {
    document.getElementById("view-main").classList.add("hide");
    document.getElementById("view-detail").classList.remove("hide");
    renderDetail(pairId);
    window.scrollTo(0, 0);
    history.pushState({}, "", `#/${pairId}`);
  }
  colorizeNarrativeNumbers();
}

function colorizeNarrativeNumbers() {
  document.querySelectorAll(".tldr-body em, .writeup-section .body em, .watch-list li em").forEach(el => {
    const t = el.textContent.trim();
    el.classList.remove("hot-short", "hot-long");
    if (t.startsWith("-")) el.classList.add("hot-short");
    else if (t.startsWith("+")) el.classList.add("hot-long");
  });
}

// Static handlers.
document.getElementById("brand-home").addEventListener("click", () => route("main"));
document.getElementById("nav-main").addEventListener("click", () => route("main"));
document.getElementById("back-link").addEventListener("click", () => route("main"));

window.addEventListener("popstate", () => {
  const hash = location.hash.replace(/^#\//, "");
  if (hash) route("detail", hash); else route("main");
});

boot();
