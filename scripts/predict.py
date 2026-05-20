"""
부산 북구갑 2026-06-03 보궐선거 예측 — Nate Silver 538-style 모델

핵심 구성요소
-----------
1. 사전분포(Prior) — 과거 선거 가중평균 + 보궐 보정
2. House effects — 매체 lean (right/center/centerleft/left) 별 편향 보정
3. Fieldwork midpoint 기준 recency weighting: √n × exp(-Δt / τ)
4. 샤이 보수(Shy Conservative) 보정 — 부산 지역 과거 폴 vs 실제 격차 ~4%p
5. Effective n + Dirichlet-Multinomial 베이지안 사후
6. Monte Carlo + 선거일 드리프트 (보수 후보 음의 상관)
7. 시나리오 분석 (단일화, 모멘텀, 민주결집)
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
DATA = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
PLOTS = OUT / "plots"
HISTORY = OUT / "history"
WEB_DATA = ROOT / "web" / "data"
WEB_HISTORY = WEB_DATA / "history"
PLOTS.mkdir(parents=True, exist_ok=True)
HISTORY.mkdir(parents=True, exist_ok=True)
WEB_DATA.mkdir(parents=True, exist_ok=True)
WEB_HISTORY.mkdir(parents=True, exist_ok=True)

AS_OF = os.environ.get("PREDICT_AS_OF", date.today().isoformat())
ELECTION_DATE = date(2026, 6, 3)
DAYS_UNTIL = (ELECTION_DATE - date.fromisoformat(AS_OF)).days

CANDIDATES = ["하정우", "한동훈", "박민식"]

# ============================================================
# 1. 사전분포 (Prior) — 과거 선거 가중평균
# ============================================================
# 북구갑 진보 vs 보수 균형 (총선): 2016 진보 55.9% / 2020 50.6% / 2024 52.3%
prior_progressive_NA = 0.50 * 0.523 + 0.30 * 0.506 + 0.20 * 0.559
prior_conservative_NA = 0.50 * 0.467 + 0.30 * 0.486 + 0.20 * 0.441
# 보궐선거 = 투표율 하락 → 진보 -2%p
boy_election_shift = -0.02
prior_progressive = prior_progressive_NA + boy_election_shift
prior_conservative = prior_conservative_NA - boy_election_shift

# 보수 표 분할 (한동훈 65% / 박민식 35%)
HAN_SHARE_OF_CONSERVATIVE = 0.65
PARK_SHARE_OF_CONSERVATIVE = 0.35

prior_means = {
    "하정우": prior_progressive,
    "한동훈": prior_conservative * HAN_SHARE_OF_CONSERVATIVE,
    "박민식": prior_conservative * PARK_SHARE_OF_CONSERVATIVE,
}
prior_total = sum(prior_means.values())
prior_means = {k: v / prior_total for k, v in prior_means.items()}

PRIOR_STRENGTH = 200
prior_alpha = np.array([prior_means[c] * PRIOR_STRENGTH for c in CANDIDATES])

print("=" * 60)
print("STEP 1. 사전분포 (Prior)")
print("=" * 60)
for c, p in prior_means.items():
    print(f"  {c}: {p:.3f}")
print(f"  prior_alpha: {dict(zip(CANDIDATES, prior_alpha.round(2)))}")

# ============================================================
# 2. 여론조사 로드 + House Effects + Fieldwork Midpoint Weighting
# ============================================================
polls_df = pd.read_csv(DATA / "current_polls_2026.csv")
threeway = polls_df[polls_df["election"] == "2026_buksu_gap_byelection"].copy()
threeway["sample_size_num"] = pd.to_numeric(threeway["sample_size"], errors="coerce")
threeway = threeway.dropna(subset=["sample_size_num"]).copy()
threeway["fieldwork_start"] = pd.to_datetime(threeway["fieldwork_start"])
threeway["fieldwork_end"] = pd.to_datetime(threeway["fieldwork_end"])
# Fieldwork midpoint (친구가 지적한 부분)
threeway["fw_mid"] = threeway["fieldwork_start"] + (threeway["fieldwork_end"] - threeway["fieldwork_start"]) / 2

# Pivot: 폴 1건 = 1행, 후보별 컬럼
pivoted = threeway.pivot_table(
    index=["poll_date", "pollster", "pollster_lean", "sample_size_num", "fieldwork_start", "fieldwork_end", "fw_mid"],
    columns="candidate",
    values="support_pct",
    aggfunc="first",
).reset_index()

# House Effects: 매체 lean 별 알려진 편향 (Korean polling literature, Nate Silver-style)
# 우파 매체: 보수 후보 약 +2~3%p 과대 추정 경향 (특히 부산 같은 보수 지역)
# 좌파 매체: 진보 후보 약 +2%p 과대 추정 경향
# 중도: 거의 없음
HOUSE_EFFECTS = {
    "right":     {"하정우": +1.5, "한동훈": -1.0, "박민식": -0.5},  # 우파 매체 → 보수 +1.5%p 과대 → 보정
    "centerleft":{"하정우": -0.5, "한동훈": +0.3, "박민식": +0.2},  # 좌파성향 약함
    "left":      {"하정우": -1.5, "한동훈": +1.0, "박민식": +0.5},  # 좌파 매체 → 진보 +1.5%p 과대 → 보정
    "center":    {"하정우":  0.0, "한동훈":  0.0, "박민식":  0.0},
}

# 보정된 raw support
for c in CANDIDATES:
    pivoted[f"adj_{c}"] = pivoted.apply(
        lambda r: r[c] + HOUSE_EFFECTS.get(r["pollster_lean"], HOUSE_EFFECTS["center"])[c],
        axis=1
    )

# Recency weighting: √n × exp(-Δt_midpoint / τ)
TIME_DECAY_HALFLIFE = 7  # days
RECENCY_WINDOW_DAYS = 21  # 21일 이내 폴만 (early poll 포함)
today_ts = pd.to_datetime(AS_OF)
pivoted["days_old"] = (today_ts - pivoted["fw_mid"]).dt.total_seconds() / 86400.0
pivoted = pivoted[(pivoted["days_old"] >= 0) & (pivoted["days_old"] <= RECENCY_WINDOW_DAYS)].copy()

pivoted["w_time"] = 0.5 ** (pivoted["days_old"] / TIME_DECAY_HALFLIFE)
pivoted["w_size"] = np.sqrt(pivoted["sample_size_num"] / 500.0)  # 500명 기준 정규화
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
# 3. 샤이 보수 (Shy Conservative) 보정
# ============================================================
# 부산 지역 과거 폴 vs 실제 결과 격차:
#  - 2022 대선 부산: poll 53% → actual 윤석열 58.3% → 보수 +5.3%p shy
#  - 2024 총선 부산 평균: 폴 보수 평균 약 44% → actual 47% → 보수 +3%p shy
#  - 2021 부산시장 보궐: 폴 박형준 53-58% → actual 62.7% → 보수 +5-10%p
# 평균 ~ +4%p, 보궐선거에서는 응답률 낮아 shy 효과 더 강할 가능성

SHY_CONSERVATIVE_PCT = 0.040  # 4.0%p
SHY_SOURCE = "부산 2022대선(+5.3%p)·2024총선(+3%p)·2021시장보궐(+5-10%p) 평균"
# 분배: 한동훈 50% / 박민식 50% (한동훈은 인지도 효과, 박민식은 전통 보수 위축 효과)
SHY_SPLIT_HAN = 0.50
SHY_SPLIT_PARK = 0.50

support_shy = dict(support_avg_adj)
support_shy["하정우"] -= SHY_CONSERVATIVE_PCT
support_shy["한동훈"] += SHY_CONSERVATIVE_PCT * SHY_SPLIT_HAN
support_shy["박민식"] += SHY_CONSERVATIVE_PCT * SHY_SPLIT_PARK

# clip & renormalize 부동층
undecided_after_shy = max(0.0, 1.0 - sum(support_shy.values()))

print()
print("=" * 60)
print(f"STEP 3. 샤이 보수 보정 (+{SHY_CONSERVATIVE_PCT*100:.1f}%p, 한·박 {SHY_SPLIT_HAN:.0%}/{SHY_SPLIT_PARK:.0%} 분배)")
print("=" * 60)
for c in CANDIDATES:
    print(f"  {c}: {support_avg_adj[c]*100:5.2f}% → {support_shy[c]*100:5.2f}%")
print(f"  부동층(샤이 후): {undecided_after_shy*100:5.2f}%")

# ============================================================
# 4. 부동층 배분
# ============================================================
UNDECIDED_ALLOCATION = {
    "하정우": 0.30,  # 진보 부동층
    "한동훈": 0.40,  # 보수+중도+인지도
    "박민식": 0.30,  # 보수 잔여
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
DESIGN_EFFECT = 7.0  # polling industry rule: 비표본오차·house effect uncertainty 반영
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

# 선거일 드리프트: σ = 4.5%p @ 14일, √days 스케일링
# 공표 금지기간 (5/28 이후) 추가 변동성도 자동 반영
DRIFT_SIGMA_AT_14D = 0.045
days_factor = max(1.0, DAYS_UNTIL) / 14.0
sigma_drift = DRIFT_SIGMA_AT_14D * np.sqrt(days_factor)

cov = np.array([
    [sigma_drift**2,  0.0,            0.0],
    [0.0,             sigma_drift**2, -0.5 * sigma_drift**2],
    [0.0,             -0.5*sigma_drift**2, sigma_drift**2],
])
drift = RNG.multivariate_normal(mean=np.zeros(3), cov=cov, size=N_SIMS)

samples = np.clip(posterior_samples + drift, 1e-4, None)
samples = samples / samples.sum(axis=1, keepdims=True)

winners = np.argmax(samples, axis=1)
win_prob = {CANDIDATES[i]: float((winners == i).mean()) for i in range(3)}

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

sorted_samples = np.sort(samples, axis=1)
margin = sorted_samples[:, -1] - sorted_samples[:, -2]
close_race = float((margin < 0.01).mean())

# ============================================================
# 7. 샤이 보수 미보정 (counterfactual) — 비교용
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
win_prob_no_shy = {CANDIDATES[i]: float((winners_no_shy == i).mean()) for i in range(3)}
posterior_mean_no_shy = posterior_alpha_no_shy / posterior_alpha_no_shy.sum()

# ============================================================
# 8. 시나리오 분석
# ============================================================
scenarios = {}
scenarios["보수단일화 (한동훈)"] = posterior_alpha.copy()
park_alpha = scenarios["보수단일화 (한동훈)"][2]
scenarios["보수단일화 (한동훈)"][1] += park_alpha * 0.70
scenarios["보수단일화 (한동훈)"][0] += park_alpha * 0.15
scenarios["보수단일화 (한동훈)"][2] = 0.001

scenarios["보수단일화 (박민식)"] = posterior_alpha.copy()
han_alpha = scenarios["보수단일화 (박민식)"][1]
scenarios["보수단일화 (박민식)"][2] += han_alpha * 0.60
scenarios["보수단일화 (박민식)"][0] += han_alpha * 0.20
scenarios["보수단일화 (박민식)"][1] = 0.001

scenarios["한동훈 모멘텀"] = posterior_alpha.copy()
scenarios["한동훈 모멘텀"][1] *= 1.15
scenarios["한동훈 모멘텀"][0] *= 0.92
scenarios["한동훈 모멘텀"][2] *= 0.92

scenarios["민주 결집"] = posterior_alpha.copy()
scenarios["민주 결집"][0] *= 1.15
scenarios["민주 결집"][1] *= 0.94
scenarios["민주 결집"][2] *= 0.94

scenario_results = {}
print()
print("=" * 60)
print("STEP 8. 시나리오 분석")
print("=" * 60)
for name, alpha in scenarios.items():
    sims = RNG.dirichlet(alpha, size=N_SIMS)
    if "단일화" in name:
        d_sc = drift.copy()
        if "한동훈" in name:
            d_sc[:, 2] = 0
        else:
            d_sc[:, 1] = 0
    else:
        d_sc = drift
    sims = np.clip(sims + d_sc, 1e-4, None)
    sims = sims / sims.sum(axis=1, keepdims=True)
    wins = np.argmax(sims, axis=1)
    win_p = {CANDIDATES[i]: float((wins == i).mean()) for i in range(3)}
    means = sims.mean(axis=0)
    scenario_results[name] = {
        "win_prob": win_p,
        "mean_share": {CANDIDATES[i]: float(means[i]) for i in range(3)},
    }
    print(f"\n【 {name} 】")
    for c in CANDIDATES:
        print(f"  {c:6s} {win_p[c]*100:5.1f}%  평균득표 {means[CANDIDATES.index(c)]*100:5.1f}%")

scenario_results["BASELINE (3자대결 현황)"] = {
    "win_prob": win_prob,
    "mean_share": {CANDIDATES[i]: float(posterior_mean[i]) for i in range(3)},
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
    "model": "Nate Silver 538-style: House effects + Fieldwork midpoint + 샤이보수 + Dirichlet + drift",
    "election": "2026-06-03 부산 북구갑 국회의원 보궐선거",
    "election_date": ELECTION_DATE.isoformat(),
    "as_of": AS_OF,
    "days_until_election": DAYS_UNTIL,
    "candidates": CANDIDATES,
    "prior": {
        "method": "북구갑 최근 3회 총선 가중평균(0.5, 0.3, 0.2) + 보궐선거 -2%p 보정",
        "prior_progressive": float(prior_progressive),
        "prior_conservative": float(prior_conservative),
        "conservative_split_HAN/PARK": [HAN_SHARE_OF_CONSERVATIVE, PARK_SHARE_OF_CONSERVATIVE],
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
        # 호환성 필드
        "source": f"{len(pivoted)}건 평균",
        "date": pivoted.iloc[pivoted["days_old"].argmin()]["fw_mid"].date().isoformat(),
        "n": int(N_EFFECTIVE),
        "support": support_avg_raw,
        "undecided": float(undecided_raw),
    },
    "shy_conservative": {
        "applied": True,
        "magnitude_pct": SHY_CONSERVATIVE_PCT * 100,
        "split": {"한동훈": SHY_SPLIT_HAN, "박민식": SHY_SPLIT_PARK},
        "source": SHY_SOURCE,
        "confidence": "medium",
        "notes": "부산 2022대선·2024총선·2021시장보궐 폴 vs 실제 결과 격차 평균",
    },
    "drift": {
        "sigma_drift": float(sigma_drift),
        "days_factor": float(days_factor),
        "sigma_at_14d": DRIFT_SIGMA_AT_14D,
        "han_park_correlation": -0.5,
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

# web/data 동기화
shutil.copy2(OUT / "predictions.json", WEB_DATA / "predictions.json")
shutil.copy2(timeline_path, WEB_DATA / "history_timeline.json")
shutil.copy2(snapshot_path, WEB_HISTORY / f"{AS_OF}.json")

# ============================================================
# 10. 시각화
# ============================================================
colors = {"하정우": "#0050A0", "한동훈": "#888888", "박민식": "#E61E2B"}

# 10-1. 사후 분포
fig, ax = plt.subplots(figsize=(10, 6))
for i, c in enumerate(CANDIDATES):
    ax.hist(samples[:, i] * 100, bins=80, alpha=0.6, label=f"{c} (평균 {posterior_mean[i]*100:.1f}%)", color=colors[c])
    ax.axvline(posterior_mean[i] * 100, color=colors[c], linestyle="--", alpha=0.8)
ax.set_xlabel("득표율 (%)"); ax.set_ylabel("시뮬레이션 빈도")
ax.set_title("부산 북구갑 — 후보별 득표율 사후분포 (Nate Silver-style)")
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
x = np.arange(len(scenario_names)); width = 0.25
for i, c in enumerate(CANDIDATES):
    vals = [scenario_results[s]["win_prob"][c] * 100 for s in scenario_names]
    ax.bar(x + i * width, vals, width, label=c, color=colors[c])
ax.set_xticks(x + width); ax.set_xticklabels(scenario_names, rotation=15, ha="right")
ax.set_ylabel("당선 확률 (%)"); ax.set_title("시나리오별 당선 확률")
ax.legend(); ax.grid(axis="y", alpha=0.3); plt.tight_layout()
plt.savefig(PLOTS / "03_scenarios.png", dpi=120); plt.close()

# 10-4. 역대 추세
fig, ax = plt.subplots(figsize=(12, 6))
na_gap = pd.read_csv(DATA / "national_assembly_buksu_gap.csv")
years = sorted(na_gap["year"].unique())
prog_share, cons_share = [], []
for y in years:
    sub = na_gap[na_gap["year"] == y]
    prog = sub[sub["party"].isin(["더불어민주당"])]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["새누리당", "미래통합당", "국민의힘"])]["vote_share_pct"].sum()
    prog_share.append(prog); cons_share.append(cons)
ax.plot(years, prog_share, "o-", color=colors["하정우"], label="민주당 (총선)", linewidth=2, markersize=10)
ax.plot(years, cons_share, "o-", color=colors["박민식"], label="보수 (총선)", linewidth=2, markersize=10)
pres = pd.read_csv(DATA / "presidential_buksu.csv")
for y in sorted(pres["year"].unique()):
    sub = pres[pres["year"] == y]
    prog = sub[sub["party"] == "더불어민주당"]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["자유한국당", "국민의힘"])]["vote_share_pct"].sum()
    ax.plot(y, prog, "s", color=colors["하정우"], alpha=0.4, markersize=10)
    ax.plot(y, cons, "s", color=colors["박민식"], alpha=0.4, markersize=10)
ax.axhline(50, color="black", linestyle=":", alpha=0.5)
ax.set_ylabel("득표율 (%)"); ax.set_xlabel("연도")
ax.set_title("역대 추세 (원=총선, 사각형=대선[북구 전체])"); ax.legend(); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(PLOTS / "04_historical_trend.png", dpi=120); plt.close()

WEB_PLOTS = ROOT / "web" / "plots"
WEB_PLOTS.mkdir(parents=True, exist_ok=True)
for p in PLOTS.glob("*.png"):
    shutil.copy2(p, WEB_PLOTS / p.name)

print()
print("=" * 60)
print("완료")
print("=" * 60)
