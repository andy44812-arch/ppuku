"""
서울특별시장 2026-06-03 선거 예측 — Nate Silver 538-style 모델 (2자대결)

구조는 predict_busan.py와 동일. 차이점만 정리:
- 2자대결: 정원오(민주) vs 오세훈(국힘)
- Prior: 서울 과거 시장 선거(2018/2021/2022) + 대선 서울(2022) + 대선 전국(2025) 가중평균
- 샤이 보수: 부산보다 작음 (+1.5%p) — 서울은 swing 지역, 폴 적중률 양호
- 시나리오: 보수단일화 불가능(단일 후보) → 모멘텀/결집/현직효과로 대체
"""
import json
import os
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

# ============================================================
# 한글 폰트
# ============================================================
import platform
if platform.system() == "Darwin":
    rcParams["font.family"] = "AppleGothic"
elif platform.system() == "Windows":
    rcParams["font.family"] = "Malgun Gothic"
else:
    rcParams["font.family"] = "NanumGothic"
rcParams["axes.unicode_minus"] = False

# ============================================================
# 경로
# ============================================================
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "raw" / "seoul"
OUT = ROOT / "data" / "processed" / "seoul"
PLOTS = OUT / "plots"
HISTORY = OUT / "history"
WEB_DATA = ROOT / "web" / "data" / "seoul"
WEB_HISTORY = WEB_DATA / "history"
PLOTS.mkdir(parents=True, exist_ok=True)
HISTORY.mkdir(parents=True, exist_ok=True)
WEB_DATA.mkdir(parents=True, exist_ok=True)
WEB_HISTORY.mkdir(parents=True, exist_ok=True)

AS_OF = os.environ.get("PREDICT_AS_OF", date.today().isoformat())
ELECTION_DATE = date(2026, 6, 3)
DAYS_UNTIL = (ELECTION_DATE - date.fromisoformat(AS_OF)).days

CANDIDATES = ["정원오", "오세훈"]

# ============================================================
# 1. 사전분포 (Prior) — 서울 과거 선거 가중평균
# ============================================================
# 가중치: 2022 지선(0.30) · 2021 보궐(0.20) · 2018 지선(0.10) · 2022 대선(0.15) · 2025 대선(0.25)
# 진보(민주): 2018 박원순 52.79 / 2021 박영선 39.18 / 2022 송영길 39.23 / 2022 대선 이재명 45.73 / 2025 대선 이재명 49.42
# 보수(국힘+안철수 절반): 2018 김문수 23.34 + 안철수 9.78 = 33.12 / 2021 오세훈 57.50 / 2022 오세훈 59.05 /
#                       2022 대선 윤석열 50.56 / 2025 대선 김문수 41.15

prior_progressive = (
    0.30 * 0.3923 + 0.20 * 0.3918 + 0.10 * 0.5279 + 0.15 * 0.4573 + 0.25 * 0.4942
)  # ≈ 0.4410
prior_conservative = (
    0.30 * 0.5905 + 0.20 * 0.5750 + 0.10 * 0.3312 + 0.15 * 0.5056 + 0.25 * 0.4115
)  # ≈ 0.5040

# 2자대결로 정규화
prior_total = prior_progressive + prior_conservative
prior_means = {
    "정원오": prior_progressive / prior_total,
    "오세훈": prior_conservative / prior_total,
}

# 서울은 swing이라 prior 약하게 (부산은 200, 서울은 100)
PRIOR_STRENGTH = 100
prior_alpha = np.array([prior_means[c] * PRIOR_STRENGTH for c in CANDIDATES])

print("=" * 60)
print("STEP 1. 사전분포 (Prior) — 서울 과거 가중평균")
print("=" * 60)
for c, p in prior_means.items():
    print(f"  {c}: {p:.3f}")
print(f"  prior_alpha: {dict(zip(CANDIDATES, prior_alpha.round(2)))}")

# ============================================================
# 2. 여론조사 로드 + House Effects + Fieldwork Midpoint Weighting
# ============================================================
polls_df = pd.read_csv(DATA / "current_polls_seoul.csv")
twoway = polls_df[polls_df["election"] == "2026_seoul_mayor"].copy()
twoway["sample_size_num"] = pd.to_numeric(twoway["sample_size"], errors="coerce")
twoway = twoway.dropna(subset=["sample_size_num"]).copy()
twoway["fieldwork_start"] = pd.to_datetime(twoway["fieldwork_start"])
twoway["fieldwork_end"] = pd.to_datetime(twoway["fieldwork_end"])
twoway["fw_mid"] = twoway["fieldwork_start"] + (twoway["fieldwork_end"] - twoway["fieldwork_start"]) / 2

pivoted = twoway.pivot_table(
    index=["poll_date", "pollster", "pollster_lean", "sample_size_num", "fieldwork_start", "fieldwork_end", "fw_mid"],
    columns="candidate",
    values="support_pct",
    aggfunc="first",
).reset_index()

# House Effects: 2자대결 버전
#  - 우파 매체: 보수 후보(오세훈)를 +1.5%p 과대 → 보정
#  - 좌파/중도좌: 진보 후보(정원오)를 +1.0%p 과대 → 보정
HOUSE_EFFECTS = {
    "right":      {"정원오": +1.5, "오세훈": -1.5},
    "centerleft": {"정원오": -1.0, "오세훈": +1.0},
    "left":       {"정원오": -1.5, "오세훈": +1.5},
    "center":     {"정원오":  0.0, "오세훈":  0.0},
}

for c in CANDIDATES:
    pivoted[f"adj_{c}"] = pivoted.apply(
        lambda r: r[c] + HOUSE_EFFECTS.get(r["pollster_lean"], HOUSE_EFFECTS["center"])[c],
        axis=1
    )

TIME_DECAY_HALFLIFE = 7
RECENCY_WINDOW_DAYS = 21
today_ts = pd.to_datetime(AS_OF)
pivoted["days_old"] = (today_ts - pivoted["fw_mid"]).dt.total_seconds() / 86400.0
pivoted = pivoted[(pivoted["days_old"] >= 0) & (pivoted["days_old"] <= RECENCY_WINDOW_DAYS)].copy()

pivoted["w_time"] = 0.5 ** (pivoted["days_old"] / TIME_DECAY_HALFLIFE)
pivoted["w_size"] = np.sqrt(pivoted["sample_size_num"] / 800.0)  # 서울은 800 기준
pivoted["weight"] = pivoted["w_time"] * pivoted["w_size"]

total_w = pivoted["weight"].sum()
support_avg_raw = {c: float((pivoted[c] * pivoted["weight"]).sum() / total_w / 100.0) for c in CANDIDATES}
support_avg_adj = {c: float((pivoted[f"adj_{c}"] * pivoted["weight"]).sum() / total_w / 100.0) for c in CANDIDATES}

undecided_raw = max(0.0, 1.0 - sum(support_avg_raw.values()))

print()
print("=" * 60)
print("STEP 2. 여론조사 평균 (House Effect 보정 전 → 후)")
print("=" * 60)
print(f"폴 {len(pivoted)}건, 표본합 {int(pivoted['sample_size_num'].sum())}, 가중합 {total_w:.2f}")
for c in CANDIDATES:
    print(f"  {c}: {support_avg_raw[c]*100:5.2f}% → {support_avg_adj[c]*100:5.2f}% (HE 보정)")
print(f"  부동층(raw): {undecided_raw*100:5.2f}%")

# ============================================================
# 3. 샤이 보수 (Shy Conservative) 보정 — 서울 버전
# ============================================================
# 서울은 부산만큼 강하지 않음:
#  - 2022 대선 서울: 폴 윤석열 ~48% → 실제 50.56% → +2.5%p
#  - 2022 지선 서울: 폴 오세훈 ~58% → 실제 59.05% → +1%p (이미 큰 격차로 거의 잡힘)
#  - 2021 보궐: 폴 오세훈 53~58% → 실제 57.5% → 평균 안에서 약 +1.5%p
# 평균 +1.5%p (보수적 추정)

SHY_CONSERVATIVE_PCT = 0.015  # 1.5%p
SHY_SOURCE = "서울 2022대선(+2.5%p)·2022지선(+1%p)·2021보궐(+1.5%p) 평균"
# 2자대결이므로 전부 오세훈에게
SHY_SPLIT_OSE = 1.0

support_shy = dict(support_avg_adj)
support_shy["정원오"] -= SHY_CONSERVATIVE_PCT
support_shy["오세훈"] += SHY_CONSERVATIVE_PCT * SHY_SPLIT_OSE

undecided_after_shy = max(0.0, 1.0 - sum(support_shy.values()))

print()
print("=" * 60)
print(f"STEP 3. 샤이 보수 보정 (+{SHY_CONSERVATIVE_PCT*100:.1f}%p, 전액 오세훈)")
print("=" * 60)
for c in CANDIDATES:
    print(f"  {c}: {support_avg_adj[c]*100:5.2f}% → {support_shy[c]*100:5.2f}%")
print(f"  부동층(샤이 후): {undecided_after_shy*100:5.2f}%")

# ============================================================
# 4. 부동층 배분
# ============================================================
# 정원오:오세훈 = 45:55 (현직 효과 + 보수 잠재 결집 약간 반영)
UNDECIDED_ALLOCATION = {
    "정원오": 0.45,
    "오세훈": 0.55,
}

adjusted_final = {c: support_shy[c] + undecided_after_shy * UNDECIDED_ALLOCATION[c] for c in CANDIDATES}
total = sum(adjusted_final.values())
adjusted_final = {k: v / total for k, v in adjusted_final.items()}

print()
print("=" * 60)
print(f"STEP 4. 부동층 배분 + 정규화 → 최종 폴 추정")
print("=" * 60)
for c in CANDIDATES:
    print(f"  {c}: {adjusted_final[c]*100:5.2f}%")

# ============================================================
# 5. Effective Sample Size + Dirichlet Posterior
# ============================================================
total_n = int(pivoted["sample_size_num"].sum())
DESIGN_EFFECT = 7.0
N_EFFECTIVE = max(50.0, total_n / DESIGN_EFFECT)

poll_counts = np.array([adjusted_final[c] * N_EFFECTIVE for c in CANDIDATES])
posterior_alpha = prior_alpha + poll_counts
posterior_mean = posterior_alpha / posterior_alpha.sum()

print()
print("=" * 60)
print(f"STEP 5. 사후분포 — Effective n={N_EFFECTIVE:.0f} (Σn={total_n} / DE={DESIGN_EFFECT})")
print("=" * 60)
print(f"  posterior_alpha: {dict(zip(CANDIDATES, posterior_alpha.round(1)))}")
print(f"  posterior_mean: {dict(zip(CANDIDATES, [f'{x*100:.2f}%' for x in posterior_mean]))}")

# ============================================================
# 6. Monte Carlo + 선거일 드리프트
# ============================================================
RNG = np.random.default_rng(42)
N_SIMS = 10_000

posterior_samples = RNG.dirichlet(posterior_alpha, size=N_SIMS)

DRIFT_SIGMA_AT_14D = 0.045
days_factor = max(1.0, DAYS_UNTIL) / 14.0
sigma_drift = DRIFT_SIGMA_AT_14D * np.sqrt(days_factor)

# 2자대결: 한 쪽이 오르면 다른 쪽이 내림 → ρ = -1
# 단순화를 위해 single noise term을 양쪽에 반대 부호로 추가
single_noise = RNG.normal(0, sigma_drift, size=N_SIMS)
drift = np.column_stack([+single_noise, -single_noise])

samples = np.clip(posterior_samples + drift, 1e-4, None)
samples = samples / samples.sum(axis=1, keepdims=True)

winners = np.argmax(samples, axis=1)
win_prob = {CANDIDATES[i]: float((winners == i).mean()) for i in range(len(CANDIDATES))}

print()
print("=" * 60)
print(f"STEP 6. Monte Carlo (N={N_SIMS}, σ_drift={sigma_drift*100:.2f}%p)")
print("=" * 60)
for c in CANDIDATES:
    bar = "█" * int(win_prob[c] * 50)
    print(f"  {c:6s} {win_prob[c]*100:5.1f}%  {bar}")

quantiles = np.percentile(samples, [5, 50, 95], axis=0)
print()
for i, c in enumerate(CANDIDATES):
    print(f"  {c}: {quantiles[0][i]*100:.1f}% ~ [{quantiles[1][i]*100:.1f}%] ~ {quantiles[2][i]*100:.1f}%")

margin = np.abs(samples[:, 0] - samples[:, 1])
close_race = float((margin < 0.01).mean())

# ============================================================
# 7. 샤이 보수 미보정 (counterfactual)
# ============================================================
adjusted_no_shy = {c: support_avg_adj[c] + undecided_raw * UNDECIDED_ALLOCATION[c] for c in CANDIDATES}
total_no_shy = sum(adjusted_no_shy.values())
adjusted_no_shy = {k: v / total_no_shy for k, v in adjusted_no_shy.items()}
poll_counts_no_shy = np.array([adjusted_no_shy[c] * N_EFFECTIVE for c in CANDIDATES])
posterior_alpha_no_shy = prior_alpha + poll_counts_no_shy
samples_no_shy = RNG.dirichlet(posterior_alpha_no_shy, size=N_SIMS) + drift
samples_no_shy = np.clip(samples_no_shy, 1e-4, None)
samples_no_shy = samples_no_shy / samples_no_shy.sum(axis=1, keepdims=True)
winners_no_shy = np.argmax(samples_no_shy, axis=1)
win_prob_no_shy = {CANDIDATES[i]: float((winners_no_shy == i).mean()) for i in range(len(CANDIDATES))}
posterior_mean_no_shy = posterior_alpha_no_shy / posterior_alpha_no_shy.sum()

# ============================================================
# 8. 시나리오 분석 — 2자대결 버전
# ============================================================
scenarios = {}

# 보수 결집 (오세훈 +8%p 가상 swing)
scenarios["보수 결집 (현직 효과)"] = posterior_alpha.copy()
scenarios["보수 결집 (현직 효과)"][1] *= 1.18
scenarios["보수 결집 (현직 효과)"][0] *= 0.88

# 진보 결집 (정권 지원, 부동층 민주 쏠림)
scenarios["진보 결집 (정부 지원)"] = posterior_alpha.copy()
scenarios["진보 결집 (정부 지원)"][0] *= 1.12
scenarios["진보 결집 (정부 지원)"][1] *= 0.92

# 오세훈 모멘텀 (현재 추격세 지속)
scenarios["오세훈 모멘텀"] = posterior_alpha.copy()
scenarios["오세훈 모멘텀"][1] *= 1.10
scenarios["오세훈 모멘텀"][0] *= 0.94

# 정원오 모멘텀
scenarios["정원오 모멘텀"] = posterior_alpha.copy()
scenarios["정원오 모멘텀"][0] *= 1.10
scenarios["정원오 모멘텀"][1] *= 0.94

scenario_results = {}
print()
print("=" * 60)
print("STEP 8. 시나리오 분석")
print("=" * 60)
for name, alpha in scenarios.items():
    sims = RNG.dirichlet(alpha, size=N_SIMS)
    sims = np.clip(sims + drift, 1e-4, None)
    sims = sims / sims.sum(axis=1, keepdims=True)
    wins = np.argmax(sims, axis=1)
    win_p = {CANDIDATES[i]: float((wins == i).mean()) for i in range(len(CANDIDATES))}
    means = sims.mean(axis=0)
    scenario_results[name] = {
        "win_prob": win_p,
        "mean_share": {CANDIDATES[i]: float(means[i]) for i in range(len(CANDIDATES))},
    }
    print(f"\n【 {name} 】")
    for c in CANDIDATES:
        print(f"  {c:6s} {win_p[c]*100:5.1f}%  평균득표 {means[CANDIDATES.index(c)]*100:5.1f}%")

scenario_results["BASELINE (2자대결 현황)"] = {
    "win_prob": win_prob,
    "mean_share": {CANDIDATES[i]: float(posterior_mean[i]) for i in range(len(CANDIDATES))},
}

# ============================================================
# 9. 결과 저장
# ============================================================
polls_used_list = []
for _, row in pivoted.iterrows():
    polls_used_list.append({
        "pollster": str(row["pollster"]),
        "lean": str(row["pollster_lean"]),
        "fieldwork_start": row["fieldwork_start"].date().isoformat(),
        "fieldwork_end": row["fieldwork_end"].date().isoformat(),
        "fw_midpoint": row["fw_mid"].date().isoformat(),
        "publication": str(row["poll_date"]),
        "n": int(row["sample_size_num"]),
        "days_old": float(round(row["days_old"], 2)),
        "weight": float(round(row["weight"], 3)),
        "support": {c: float(row[c] / 100.0) for c in CANDIDATES},
        "support_adjusted": {c: float(row[f"adj_{c}"] / 100.0) for c in CANDIDATES},
    })

predictions = {
    "model": "Nate Silver 538-style: House effects + Fieldwork midpoint + 샤이보수 + Dirichlet + drift (2-way)",
    "election": "2026-06-03 서울특별시장 선거 (제9회 전국동시지방선거)",
    "election_date": ELECTION_DATE.isoformat(),
    "as_of": AS_OF,
    "days_until_election": DAYS_UNTIL,
    "candidates": CANDIDATES,
    "prior": {
        "method": "서울 2022지선(0.30) · 2021보궐(0.20) · 2018지선(0.10) · 2022대선(0.15) · 2025대선(0.25) 가중평균",
        "prior_progressive": float(prior_progressive),
        "prior_conservative": float(prior_conservative),
        "conservative_split_HAN/PARK": [1.0, 0.0],
        "prior_strength": PRIOR_STRENGTH,
        "prior_alpha": dict(zip(CANDIDATES, prior_alpha.tolist())),
    },
    "house_effects": {
        "applied": True,
        "values_pct": HOUSE_EFFECTS,
        "description": "매체 lean별 알려진 편향(%p)을 raw 지지율에서 차감",
    },
    "poll": {
        "method": f"최근 {RECENCY_WINDOW_DAYS}일 폴 {len(pivoted)}건, fieldwork midpoint 기준 recency × √n 가중평균",
        "n_polls": int(len(pivoted)),
        "n_total_raw": total_n,
        "n_effective": float(round(N_EFFECTIVE, 1)),
        "design_effect": DESIGN_EFFECT,
        "halflife_days": TIME_DECAY_HALFLIFE,
        "support_raw": support_avg_raw,
        "support_house_effect_adjusted": support_avg_adj,
        "support_shy_adjusted": support_shy,
        "adjusted_final": adjusted_final,
        "undecided_raw": float(undecided_raw),
        "undecided_after_shy": float(undecided_after_shy),
        "undecided_allocation": UNDECIDED_ALLOCATION,
        "polls_used": polls_used_list,
        "source": f"{len(pivoted)}건 평균",
        "date": pivoted.iloc[pivoted["days_old"].argmin()]["fw_mid"].date().isoformat() if len(pivoted) else AS_OF,
        "n": int(N_EFFECTIVE),
        "support": support_avg_raw,
        "undecided": float(undecided_raw),
    },
    "shy_conservative": {
        "applied": True,
        "magnitude_pct": SHY_CONSERVATIVE_PCT * 100,
        "split": {"오세훈": SHY_SPLIT_OSE},
        "source": SHY_SOURCE,
        "confidence": "medium",
        "notes": "서울 2022대선·2022지선·2021보궐 폴 vs 실제 결과 격차 평균",
    },
    "drift": {
        "sigma_drift": float(sigma_drift),
        "days_factor": float(days_factor),
        "sigma_at_14d": DRIFT_SIGMA_AT_14D,
        "han_park_correlation": -1.0,
    },
    "posterior_alpha": dict(zip(CANDIDATES, posterior_alpha.tolist())),
    "posterior_mean": dict(zip(CANDIDATES, posterior_mean.tolist())),
    "win_probability": win_prob,
    "vote_share_percentiles": {
        c: {
            "p5": float(quantiles[0][i]),
            "p50": float(quantiles[1][i]),
            "p95": float(quantiles[2][i]),
        }
        for i, c in enumerate(CANDIDATES)
    },
    "margin": {
        "median": float(np.median(margin)),
        "p5": float(np.percentile(margin, 5)),
        "p95": float(np.percentile(margin, 95)),
        "P_close_race_within_1pp": close_race,
    },
    "counterfactual_no_shy": {
        "description": "샤이 보수 보정을 적용하지 않은 경우 비교 결과",
        "posterior_mean": dict(zip(CANDIDATES, posterior_mean_no_shy.tolist())),
        "win_probability": win_prob_no_shy,
    },
    "scenarios": scenario_results,
}

with open(OUT / "predictions.json", "w", encoding="utf-8") as f:
    json.dump(predictions, f, ensure_ascii=False, indent=2)
print(f"\n예측 저장: {OUT / 'predictions.json'}")

snapshot_path = HISTORY / f"{AS_OF}.json"
with open(snapshot_path, "w", encoding="utf-8") as f:
    json.dump(predictions, f, ensure_ascii=False, indent=2)

# 시계열
timeline_path = OUT / "history_timeline.json"
timeline = []
for snap in sorted(HISTORY.glob("*.json")):
    try:
        with open(snap, encoding="utf-8") as f:
            d = json.load(f)
        timeline.append({
            "date": d["as_of"],
            "days_until_election": d.get("days_until_election"),
            "win_probability": d["win_probability"],
            "posterior_mean": d["posterior_mean"],
            "poll_source": d["poll"].get("source", "?"),
            "poll_date": d["poll"].get("date", "?"),
        })
    except Exception as e:
        print(f"  skip {snap.name}: {e}")
with open(timeline_path, "w", encoding="utf-8") as f:
    json.dump(timeline, f, ensure_ascii=False, indent=2)

shutil.copy2(OUT / "predictions.json", WEB_DATA / "predictions.json")
shutil.copy2(timeline_path, WEB_DATA / "history_timeline.json")
shutil.copy2(snapshot_path, WEB_HISTORY / f"{AS_OF}.json")

# ============================================================
# 10. 시각화
# ============================================================
colors = {"정원오": "#2f6fdb", "오세훈": "#e74c5e"}

# 10-1. 사후 분포
fig, ax = plt.subplots(figsize=(10, 6))
for i, c in enumerate(CANDIDATES):
    ax.hist(samples[:, i] * 100, bins=80, alpha=0.6, label=f"{c} (평균 {posterior_mean[i]*100:.1f}%)", color=colors[c])
    ax.axvline(posterior_mean[i] * 100, color=colors[c], linestyle="--", alpha=0.8)
ax.set_xlabel("득표율 (%)"); ax.set_ylabel("시뮬레이션 빈도")
ax.set_title("서울특별시장 — 후보별 득표율 사후분포 (Nate Silver-style)")
ax.legend(loc="upper right"); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(PLOTS / "01_vote_share_distribution.png", dpi=120); plt.close()

# 10-2. 당선 확률
fig, ax = plt.subplots(figsize=(8, 5))
probs = [win_prob[c] * 100 for c in CANDIDATES]
bars = ax.bar(CANDIDATES, probs, color=[colors[c] for c in CANDIDATES])
for b, p in zip(bars, probs):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, f"{p:.1f}%", ha="center", fontsize=14, fontweight="bold")
ax.set_ylabel("당선 확률 (%)"); ax.set_ylim(0, 100)
ax.set_title("당선 확률 (베이스라인)")
ax.grid(axis="y", alpha=0.3); plt.tight_layout()
plt.savefig(PLOTS / "02_win_probability.png", dpi=120); plt.close()

# 10-3. 시나리오
fig, ax = plt.subplots(figsize=(12, 6))
scenario_names = list(scenario_results.keys())
x = np.arange(len(scenario_names)); width = 0.35
for i, c in enumerate(CANDIDATES):
    vals = [scenario_results[s]["win_prob"][c] * 100 for s in scenario_names]
    ax.bar(x + i * width, vals, width, label=c, color=colors[c])
ax.set_xticks(x + width / 2); ax.set_xticklabels(scenario_names, rotation=15, ha="right")
ax.set_ylabel("당선 확률 (%)"); ax.set_title("시나리오별 당선 확률")
ax.legend(); ax.grid(axis="y", alpha=0.3); plt.tight_layout()
plt.savefig(PLOTS / "03_scenarios.png", dpi=120); plt.close()

# 10-4. 역대 추세
fig, ax = plt.subplots(figsize=(12, 6))
hist = pd.read_csv(DATA / "local_election_seoul.csv")
years = sorted(hist["year"].unique())
prog_share, cons_share = [], []
for y in years:
    sub = hist[hist["year"] == y]
    prog = sub[sub["party"].isin(["더불어민주당"])]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["자유한국당", "국민의힘", "바른미래당"])]["vote_share_pct"].sum()
    prog_share.append(prog); cons_share.append(cons)
ax.plot(years, prog_share, "o-", color=colors["정원오"], label="민주당 (서울시장)", linewidth=2, markersize=10)
ax.plot(years, cons_share, "o-", color=colors["오세훈"], label="보수+제3당 (서울시장)", linewidth=2, markersize=10)
pres = pd.read_csv(DATA / "presidential_seoul.csv")
for y in sorted(pres["year"].unique()):
    sub = pres[pres["year"] == y]
    prog = sub[sub["party"] == "더불어민주당"]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["자유한국당", "국민의힘"])]["vote_share_pct"].sum()
    ax.plot(y, prog, "s", color=colors["정원오"], alpha=0.4, markersize=10)
    ax.plot(y, cons, "s", color=colors["오세훈"], alpha=0.4, markersize=10)
ax.axhline(50, color="black", linestyle=":", alpha=0.5)
ax.set_ylabel("득표율 (%)"); ax.set_xlabel("연도")
ax.set_title("서울 역대 추세 (원=시장 직선, 사각형=대선[서울])"); ax.legend(); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(PLOTS / "04_historical_trend.png", dpi=120); plt.close()

WEB_PLOTS = ROOT / "web" / "plots" / "seoul"
WEB_PLOTS.mkdir(parents=True, exist_ok=True)
for p in PLOTS.glob("*.png"):
    shutil.copy2(p, WEB_PLOTS / p.name)

print()
print("=" * 60)
print("완료")
print("=" * 60)
