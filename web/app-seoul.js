// ============================================================
// 서울특별시장 선거 예측 — 클라이언트 렌더링 (2자대결)
// ============================================================
const CANDIDATE_META = {
  "정원오": { party: "더불어민주당", color: "#2f6fdb", short: "민주", photo: "images/seoul/정원오.webp" },
  "오세훈": { party: "국민의힘",     color: "#e74c5e", short: "국힘", photo: "images/seoul/오세훈.webp" },
};

const fmt = {
  pct: (v, d = 1) => `${(v * 100).toFixed(d)}%`,
  pctPP: (v, d = 1) => `${(v * 100).toFixed(d)}%p`,
  num: (v) => new Intl.NumberFormat("ko-KR").format(v),
  date: (iso) => {
    const d = new Date(iso);
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
  },
};

async function loadJSON(path) {
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`failed to load ${path}: ${res.status}`);
  return res.json();
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function renderUpdated(pred) {
  setText("updated-at", `${fmt.date(pred.as_of)} 기준 · 매일 자동 업데이트`);
  setText("footer-asof", `Last updated: ${pred.as_of}`);
  setText("days-until", pred.days_until_election ?? "—");
}

function renderHeadline(pred) {
  const probs = pred.win_probability;
  const ranked = Object.entries(probs).sort((a, b) => b[1] - a[1]);
  const [leadName, leadProb] = ranked[0];
  const [secondName, secondProb] = ranked[1];
  const meta = CANDIDATE_META[leadName];

  const lead = document.getElementById("lead-line");
  lead.innerHTML = `
    <span class="lead-name" style="color:${meta.color}">${leadName}</span>
    <span style="color:var(--text-dim); font-size:0.7em; margin: 0 4px;">의</span>
    <span>당선 확률</span>
    <span class="lead-pct">${fmt.pct(leadProb, 1)}</span>
  `;

  const marginMedian = pred.margin.median;
  const closeProb = pred.margin.P_close_race_within_1pp;
  setText(
    "margin-line",
    `2위 ${secondName} ${fmt.pct(secondProb, 1)} · 1·2위 격차 중앙값 ${fmt.pctPP(marginMedian, 1)} · 1%p 이내 박빙 확률 ${fmt.pct(closeProb, 1)}`
  );
}

function renderCandidates(pred) {
  const wrap = document.getElementById("candidate-grid");
  wrap.innerHTML = "";
  const candidates = pred.candidates;
  const sorted = [...candidates].sort((a, b) => pred.win_probability[b] - pred.win_probability[a]);
  const leader = sorted[0];

  for (const name of sorted) {
    const meta = CANDIDATE_META[name];
    const winP = pred.win_probability[name];
    const mean = pred.posterior_mean[name];
    const pct = pred.vote_share_percentiles[name];
    const card = document.createElement("div");
    card.className = "candidate-card" + (name === leader ? " leading" : "");
    card.style.setProperty("--card-color", meta.color);
    card.innerHTML = `
      <div class="cand-row">
        <div class="cand-identity">
          <img class="cand-photo" src="${meta.photo}" alt="${name}" loading="lazy" />
          <div>
            <div class="cand-name">${name}</div>
            <div class="cand-party">${meta.party}</div>
          </div>
        </div>
        <div class="cand-pct-wrap">
          <div class="cand-pct">${(winP * 100).toFixed(1)}%</div>
          <div class="cand-pct-label">당선 확률</div>
        </div>
      </div>
      <div class="cand-bar"><div class="cand-bar-fill" style="width:${Math.max(winP * 100, 0.5)}%"></div></div>
      <div class="cand-foot">
        <span>예상 득표 <strong>${fmt.pct(mean, 1)}</strong></span>
        <span>90% CI <strong>${fmt.pct(pct.p5, 1)} ~ ${fmt.pct(pct.p95, 1)}</strong></span>
      </div>
    `;
    wrap.appendChild(card);
  }
}

const CHART_DEFAULTS = {
  font: { family: "Pretendard, sans-serif" },
  textColor: "#e7ecf5",
  grid: "rgba(255,255,255,0.06)",
  tickColor: "#8d97b3",
};

function applyChartDefaults() {
  Chart.defaults.color = CHART_DEFAULTS.textColor;
  Chart.defaults.font.family = "Pretendard, -apple-system, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.plugins.legend.labels.color = CHART_DEFAULTS.textColor;
}

function renderVoteShareChart(pred) {
  const candidates = pred.candidates;
  const meanData = candidates.map(c => pred.posterior_mean[c] * 100);
  const p5Data = candidates.map(c => pred.vote_share_percentiles[c].p5 * 100);
  const p95Data = candidates.map(c => pred.vote_share_percentiles[c].p95 * 100);
  const colors = candidates.map(c => CANDIDATE_META[c].color);

  const barData = candidates.map((c, i) => [p5Data[i], p95Data[i]]);

  const ctx = document.getElementById("voteShareChart");
  new Chart(ctx, {
    type: "bar",
    data: {
      labels: candidates,
      datasets: [
        {
          label: "90% 신뢰구간 (5~95 퍼센타일)",
          data: barData,
          backgroundColor: colors.map(c => `${c}55`),
          borderColor: colors,
          borderWidth: 2,
          borderRadius: 8,
          borderSkipped: false,
          maxBarThickness: 110,
        },
        {
          label: "중앙값",
          type: "scatter",
          data: candidates.map((c, i) => ({ x: c, y: meanData[i] })),
          backgroundColor: colors,
          borderColor: "#fff",
          borderWidth: 2,
          pointRadius: 7,
          pointHoverRadius: 9,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "x",
      plugins: {
        legend: { position: "bottom", labels: { padding: 14, boxWidth: 14 } },
        tooltip: {
          backgroundColor: "#0b0f1a",
          borderColor: "#243054",
          borderWidth: 1,
          padding: 12,
          callbacks: {
            label: (ctx) => {
              if (ctx.dataset.type === "scatter") {
                return `중앙값: ${ctx.parsed.y.toFixed(1)}%`;
              }
              const [lo, hi] = ctx.raw;
              return `90% CI: ${lo.toFixed(1)}% ~ ${hi.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          max: 65,
          ticks: { color: CHART_DEFAULTS.tickColor, callback: (v) => `${v}%` },
          grid: { color: CHART_DEFAULTS.grid },
        },
        x: {
          ticks: { color: CHART_DEFAULTS.tickColor, font: { size: 14, weight: "600" } },
          grid: { display: false },
        },
      },
    },
  });
}

function renderScenarioChart(pred) {
  const scenarios = pred.scenarios;
  const candidates = pred.candidates;

  const order = [
    "BASELINE (2자대결 현황)",
    "보수 결집 (현직 효과)",
    "진보 결집 (정부 지원)",
    "오세훈 모멘텀",
    "정원오 모멘텀",
  ].filter(n => n in scenarios);

  const datasets = candidates.map(c => ({
    label: c,
    data: order.map(s => (scenarios[s].win_prob[c] || 0) * 100),
    backgroundColor: CANDIDATE_META[c].color,
    borderRadius: 6,
    maxBarThickness: 44,
  }));

  const labels = order.map(n => n.replace("BASELINE (", "").replace(")", ""));

  const ctx = document.getElementById("scenarioChart");
  new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { padding: 14, boxWidth: 14 } },
        tooltip: {
          backgroundColor: "#0b0f1a",
          borderColor: "#243054",
          borderWidth: 1,
          padding: 12,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}% 당선 확률`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: { color: CHART_DEFAULTS.tickColor, callback: v => `${v}%` },
          grid: { color: CHART_DEFAULTS.grid },
        },
        x: {
          ticks: { color: CHART_DEFAULTS.tickColor },
          grid: { display: false },
        },
      },
    },
  });
}

function renderHistoryChart(timeline) {
  const ctx = document.getElementById("historyChart");
  if (!timeline || timeline.length === 0) return;

  const candidates = Object.keys(timeline[0].win_probability);
  const labels = timeline.map(d => d.date);

  const datasets = candidates.map(c => ({
    label: c,
    data: timeline.map(d => (d.win_probability[c] || 0) * 100),
    borderColor: CANDIDATE_META[c]?.color || "#888",
    backgroundColor: `${CANDIDATE_META[c]?.color || "#888"}22`,
    borderWidth: 2.5,
    pointRadius: 4,
    pointHoverRadius: 6,
    tension: 0.3,
    fill: false,
  }));

  const onlyOne = timeline.length === 1;

  new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { padding: 14, boxWidth: 14 } },
        tooltip: {
          backgroundColor: "#0b0f1a",
          borderColor: "#243054",
          borderWidth: 1,
          padding: 12,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}%`,
          },
        },
        title: onlyOne ? {
          display: true,
          text: "데이터가 1일치만 있어 추세선이 보이지 않습니다 — 내일부터 채워집니다",
          color: "#8d97b3",
          font: { size: 13, weight: "normal" },
          padding: { top: 8, bottom: 16 },
        } : undefined,
      },
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { color: CHART_DEFAULTS.tickColor, callback: v => `${v}%` },
          grid: { color: CHART_DEFAULTS.grid },
        },
        x: {
          ticks: { color: CHART_DEFAULTS.tickColor },
          grid: { color: CHART_DEFAULTS.grid },
        },
      },
    },
  });
}

function renderGoldenCrossCard(trend) {
  const statusEl = document.getElementById("gc-status");
  const detailEl = document.getElementById("gc-detail");
  if (!statusEl || !detailEl || !trend) return;

  const leader = trend.golden_cross_pair.leader;
  const challenger = trend.golden_cross_pair.challenger;
  const lColor = CANDIDATE_META[leader]?.color || "#888";
  const cColor = CANDIDATE_META[challenger]?.color || "#888";
  const gc = trend.golden_cross || {};
  const ex = gc.extrapolation;
  const gap = trend.current_gap_pct;

  if (gc.actual_observed_date) {
    statusEl.innerHTML = `🚨 <span style="color:${cColor}">${challenger}</span> 추세선이 <span style="color:${lColor}">${leader}</span>을 추월 — 골든크로스 발생 (${gc.actual_observed_date})`;
  } else {
    statusEl.innerHTML = `현재 추세선상 <span style="color:${lColor}">${leader}</span>가 ${gap.toFixed(1)}%p 우위 · 골든크로스 <strong>미발생</strong>`;
  }

  if (!ex) {
    detailEl.innerHTML = `<span class="gc-dim">추세 외삽 불가 (데이터 부족)</span>`;
    return;
  }

  const slope = ex.gap_change_pct_per_day;
  const dir = slope < 0 ? "좁혀짐" : slope > 0 ? "벌어짐" : "변화 없음";
  const dirColor = slope < 0 ? cColor : lColor;

  let crossLine;
  if (ex.cross_date_extrapolated) {
    const reaches = ex.reaches_before_election;
    const badge = reaches
      ? `<span class="gc-badge gc-badge-warn">선거일 전 도달 가능</span>`
      : `<span class="gc-badge gc-badge-ok">선거일 후로 외삽</span>`;
    crossLine = `현 추세 단순 외삽 시 골든크로스 예상일: <strong>${ex.cross_date_extrapolated}</strong> (D−${ex.days_from_today}일) ${badge}`;
  } else {
    crossLine = `현 추세로는 골든크로스 미도달 (${ex.note || ""})`;
  }

  detailEl.innerHTML = `
    <div class="gc-line">최근 ${ex.lookback_days_used}일 격차 변화 <strong style="color:${dirColor}">${slope > 0 ? "+" : ""}${slope.toFixed(2)}%p/일</strong> (${dir})</div>
    <div class="gc-line">${crossLine}</div>
    <div class="gc-line gc-dim">선형 외삽은 단순 추정치이며 실제 선거는 막판 결집·이벤트로 더 빠르거나 더 늦게 일어날 수 있어요.</div>
  `;
}

function renderTrendChart(trend) {
  const ctx = document.getElementById("trendChart");
  if (!ctx || !trend || !trend.trend) return;

  const labels = trend.trend.map(d => d.date.slice(5));
  const dataByCand = {};
  for (const c of trend.candidates) {
    dataByCand[c] = trend.trend.map(d => {
      const v = d.support[c];
      return v == null ? null : v * 100;
    });
  }

  const polls = trend.polls || [];
  // category x축에 없는 라벨(예: 추세 시작 이전인 4월 폴)을 그리면 Chart.js가
  // 축 맨 끝에 엉뚱한 카테고리로 붙인다. 추세선 라벨 범위 내 폴만 산점도로 표시.
  const labelSet = new Set(labels);
  const pollScatterByCand = {};
  for (const c of trend.candidates) {
    pollScatterByCand[c] = polls
      .filter(p => p.support[c] != null && labelSet.has(p.fw_midpoint.slice(5)))
      .map(p => ({ x: p.fw_midpoint.slice(5), y: p.support[c] * 100, pollster: p.pollster }));
  }

  const datasets = [];
  for (const c of trend.candidates) {
    const color = CANDIDATE_META[c]?.color || "#888";
    datasets.push({
      label: `${c} 추세선`,
      data: dataByCand[c],
      borderColor: color,
      backgroundColor: `${color}22`,
      borderWidth: 3,
      pointRadius: 0,
      pointHoverRadius: 0,
      tension: 0.35,
      fill: false,
      order: 1,
    });
    datasets.push({
      label: `${c} 개별 폴`,
      data: pollScatterByCand[c],
      type: "scatter",
      backgroundColor: `${color}cc`,
      borderColor: "#0b0f1a",
      borderWidth: 1.5,
      pointRadius: 5,
      pointHoverRadius: 7,
      showLine: false,
      order: 2,
    });
  }

  new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            padding: 10,
            boxWidth: 12,
            filter: (item) => !item.text.includes("개별 폴"),
          },
        },
        tooltip: {
          backgroundColor: "#0b0f1a",
          borderColor: "#243054",
          borderWidth: 1,
          padding: 12,
          callbacks: {
            label: (ctx) => {
              if (ctx.dataset.type === "scatter") {
                const p = ctx.raw;
                return `${ctx.dataset.label.replace(" 개별 폴", "")} · ${p.pollster}: ${p.y.toFixed(1)}%`;
              }
              const v = ctx.parsed.y;
              if (v == null) return null;
              return `${ctx.dataset.label}: ${v.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        y: {
          min: 25,
          max: 60,
          ticks: { color: CHART_DEFAULTS.tickColor, callback: v => `${v}%` },
          grid: { color: CHART_DEFAULTS.grid },
          title: { display: true, text: "지지율(%)", color: CHART_DEFAULTS.tickColor },
        },
        x: {
          type: "category",
          ticks: { color: CHART_DEFAULTS.tickColor, maxRotation: 0, autoSkip: true, maxTicksLimit: 11 },
          grid: { color: CHART_DEFAULTS.grid },
        },
      },
    },
  });
}

function renderMethodNumbers(pred) {
  const prior = pred.prior;
  document.getElementById("prior-numbers").innerHTML = `
    <span class="num-pill">진보 ${(prior.prior_progressive * 100).toFixed(1)}%</span>
    <span class="num-pill">보수 ${(prior.prior_conservative * 100).toFixed(1)}%</span>
    <span class="num-pill">강도 ${prior.prior_strength}</span>
  `;
  const poll = pred.poll;
  const pollPills = [
    `<span class="num-pill">${poll.n_polls ?? 1}건 평균</span>`,
    `<span class="num-pill">Σn = ${poll.n_total_raw ?? poll.n}</span>`,
    `<span class="num-pill">n_eff = ${Math.round(poll.n_effective ?? poll.n)}</span>`,
    `<span class="num-pill">DE = ${poll.design_effect ?? "—"}</span>`,
    `<span class="num-pill">부동층 ${(poll.undecided * 100).toFixed(1)}%</span>`,
  ];
  document.getElementById("poll-numbers").innerHTML = pollPills.join("");
  const post = pred.posterior_alpha;
  const postMean = pred.posterior_mean;
  document.getElementById("posterior-numbers").innerHTML = pred.candidates.map(c =>
    `<span class="num-pill" style="border-color:${CANDIDATE_META[c].color}55">${c} ${(postMean[c] * 100).toFixed(1)}% (α=${Math.round(post[c])})</span>`
  ).join("");
}

function renderShyConservative(pred) {
  const sc = pred.shy_conservative;
  if (!sc || !sc.applied) return;
  setText("shy-number", `+${sc.magnitude_pct.toFixed(1)}%p`);
  setText("shy-confidence", sc.confidence === "high" ? "높음" : sc.confidence === "low" ? "낮음" : "중간");

  const cf = pred.counterfactual_no_shy;
  if (!cf) return;
  const tbody = document.querySelector("#shy-impact-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  for (const c of pred.candidates) {
    const without = (cf.win_probability[c] || 0) * 100;
    const withShy = (pred.win_probability[c] || 0) * 100;
    const delta = withShy - without;
    const meta = CANDIDATE_META[c];
    const deltaStr = (delta >= 0 ? "+" : "") + delta.toFixed(1) + "%p";
    const deltaClass = delta > 0.5 ? "pos" : delta < -0.5 ? "neg" : "neutral";
    tbody.innerHTML += `
      <tr>
        <td><span class="shy-cand-dot" style="background:${meta.color}"></span>${c}</td>
        <td class="r">${without.toFixed(1)}%</td>
        <td class="r"><strong>${withShy.toFixed(1)}%</strong></td>
        <td class="r delta ${deltaClass}">${deltaStr}</td>
      </tr>
    `;
  }
}

const LEAN_LABEL = {
  right: "우파",
  center: "중도",
  centerleft: "중도좌",
  left: "좌파",
};

function renderPolls(pred) {
  const wrap = document.getElementById("polls-grid");
  if (!wrap || !pred.poll || !pred.poll.polls_used) return;
  wrap.innerHTML = "";
  const polls = [...pred.poll.polls_used].sort((a, b) => b.weight - a.weight);
  for (const p of polls) {
    const jung = p.support["정원오"] * 100;
    const ose = p.support["오세훈"] * 100;
    const diff = Math.abs(jung - ose);
    const close = diff < 4;  // 오차범위(~3.5%p) 내
    const star = close ? "⚡ 오차범위 내 박빙" : "";
    const fwStart = p.fieldwork_start.slice(5).replace("-", "/");
    const fwEnd = p.fieldwork_end.slice(5).replace("-", "/");
    const fwDisplay = fwStart === fwEnd ? fwStart : `${fwStart}–${fwEnd}`;
    const leanLabel = LEAN_LABEL[p.lean] || p.lean;
    const cardClass = close ? "poll-card-c hilite" : "poll-card-c";
    const jungClass = jung > ose ? "pb ha lead" : "pb ha";
    const oseClass = ose > jung ? "pb park lead" : "pb park";
    wrap.innerHTML += `
      <div class="${cardClass}">
        <div class="pc-head">
          <div class="pc-source">${p.pollster}</div>
          <span class="lean-badge lean-${p.lean}">${leanLabel}</span>
        </div>
        <div class="pc-meta">
          조사 ${fwDisplay} · n=${p.n} · ${p.days_old.toFixed(0)}일 전
          <span class="pc-weight" title="모델 가중치">w=${p.weight.toFixed(2)}</span>
        </div>
        <div class="pc-bars">
          <span class="${jungClass}">정 ${jung.toFixed(1)}</span>
          <span class="${oseClass}">오 ${ose.toFixed(1)}</span>
        </div>
        ${star ? `<div class="pc-flag">${star}</div>` : ""}
      </div>
    `;
  }
}

const TAB_RENDERERS = {};
const TAB_RENDERED  = new Set();

function activateTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-panel").forEach(p => {
    p.classList.toggle("active", p.id === `tab-${tabId}`);
  });
  if (!TAB_RENDERED.has(tabId) && TAB_RENDERERS[tabId]) {
    requestAnimationFrame(() => {
      try { TAB_RENDERERS[tabId](); } catch (e) { console.error(e); }
      TAB_RENDERED.add(tabId);
    });
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

async function init() {
  applyChartDefaults();
  bindTabs();
  try {
    const [pred, timeline, trend] = await Promise.all([
      loadJSON("data/seoul/predictions.json"),
      loadJSON("data/seoul/history_timeline.json").catch(() => []),
      loadJSON("data/seoul/poll_trend.json").catch(() => null),
    ]);

    renderUpdated(pred);
    renderHeadline(pred);
    renderCandidates(pred);
    renderShyConservative(pred);
    renderMethodNumbers(pred);
    if (trend) renderGoldenCrossCard(trend);

    TAB_RENDERERS.details   = () => { if (trend) renderTrendChart(trend); renderVoteShareChart(pred); renderHistoryChart(timeline); };
    TAB_RENDERERS.scenarios = () => { renderScenarioChart(pred); };
    TAB_RENDERERS.polls     = () => { renderPolls(pred); };
    TAB_RENDERERS.method    = () => {};

    TAB_RENDERED.add("summary");
  } catch (e) {
    console.error(e);
    document.querySelector("main").insertAdjacentHTML(
      "afterbegin",
      `<div style="background:#3a1f24;border:1px solid #e74c5e;color:#ffb3bc;padding:18px;border-radius:12px;margin-bottom:24px;">
        데이터 로드 실패: ${e.message}<br><small>웹서버를 통해 열어주세요 (file:// 로 열면 fetch가 막힙니다). scripts/serve.sh 실행 후 http://localhost:8765 접속.</small>
       </div>`
    );
  }
}

init();
