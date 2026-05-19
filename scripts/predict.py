"""
부산 북구갑 2026-06-03 보궐선거 예측 모델

방법론: Dirichlet-Multinomial 베이지안 모델 + Monte Carlo 시뮬레이션

- 사전분포(prior): 과거 선거 결과로부터 추정한 후보별 기대 득표율
- 우도(likelihood): 최신 여론조사 (CSV에서 유효한 가장 최근 폴 자동 선택)
- 사후분포(posterior): Dirichlet 결합 → 1만번 샘플링
- 산출물: 후보별 당선확률, 득표율 분포(5/50/95퍼센타일), 시나리오 분석
- 매일 실행: data/processed/predictions.json + history/YYYY-MM-DD.json + web/data/ 동기화
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
    # Linux (GitHub Actions) — NanumGothic 사용
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

# ============================================================
# 1. 사전분포 구성
# ============================================================
# 후보 3인: 하정우(민주), 한동훈(무소속), 박민식(국힘)
CANDIDATES = ["하정우", "한동훈", "박민식"]

# 과거 선거에서 추정한 "이 선거구의 진보 vs 보수 균형"
# 북구갑(과거 북구·강서구갑) 총선 결과:
#   2016: 진보 55.9%, 보수 44.1%
#   2020: 진보 50.6%, 보수 48.6%
#   2024: 진보 52.3%, 보수 46.7%
#
# 대선/지방선거는 보수 우세이지만, 총선이 가장 가까운 선거구 단위 비교
# 가중평균 (최신 가중 0.5, 직전 0.3, 그 이전 0.2):
prior_progressive_NA = 0.50 * 0.523 + 0.30 * 0.506 + 0.20 * 0.559
prior_conservative_NA = 0.50 * 0.467 + 0.30 * 0.486 + 0.20 * 0.441
# 보궐선거 = 투표율 하락 → 일반적으로 보수 약간 유리 (-2%p 진보)
boy_election_shift = -0.02
prior_progressive = prior_progressive_NA + boy_election_shift
prior_conservative = prior_conservative_NA - boy_election_shift

# 보수 표 분할: 한동훈 vs 박민식
# 여론조사상 보수 지지층에서 한동훈 60~70% 흡수.
# 박민식은 국힘 공식 후보이지만 인지도·지지율 한동훈에 밀림.
# 한동훈은 추가로 중도·무당층까지 흡수 → 보수 베이스 외 부동층 일부도 가져감
# 단, 사전 분포는 "구조적" 추정이므로 여기선 보수 베이스만 분할
HAN_SHARE_OF_CONSERVATIVE = 0.65
PARK_SHARE_OF_CONSERVATIVE = 0.35

prior_means = {
    "하정우": prior_progressive,
    "한동훈": prior_conservative * HAN_SHARE_OF_CONSERVATIVE,
    "박민식": prior_conservative * PARK_SHARE_OF_CONSERVATIVE,
}
# 정규화 (3자 외 군소 표심 고려 시 합이 1보다 작을 수 있음)
prior_total = sum(prior_means.values())
prior_means = {k: v / prior_total for k, v in prior_means.items()}

print("=" * 60)
print("STEP 1. 사전분포 (Prior)")
print("=" * 60)
print(f"북구갑 진보 기준선 (총선 가중평균 + 보궐 조정): {prior_progressive:.3f}")
print(f"북구갑 보수 기준선:                              {prior_conservative:.3f}")
print(f"한동훈/박민식 보수 표 분할: {HAN_SHARE_OF_CONSERVATIVE:.0%} / {PARK_SHARE_OF_CONSERVATIVE:.0%}")
print()
for c, p in prior_means.items():
    print(f"  사전 평균 {c}: {p:.3f}")
print()

# Prior strength (alpha 합) = "사전 표본 크기 환산"
# 과거 데이터를 너무 신뢰하면 폴 무시. 너무 약하면 폴만 따라감.
# 5회 정도의 선거 결과 ≈ 200명의 가상 표본으로 환산
PRIOR_STRENGTH = 200
prior_alpha = np.array([prior_means[c] * PRIOR_STRENGTH for c in CANDIDATES])
print(f"사전 강도 (alpha 합): {PRIOR_STRENGTH}")
print(f"사전 alpha: {dict(zip(CANDIDATES, prior_alpha.round(2)))}")

# ============================================================
# 2. 우도 (여론조사) — CSV에서 유효한 가장 최근 3자대결 폴 자동 선택
# ============================================================
polls_df = pd.read_csv(DATA / "current_polls_2026.csv")
# 3자 대결만 사용
threeway = polls_df[polls_df["election"] == "2026_buksu_gap_byelection"].copy()
# sample_size 가 숫자가 아닌 행 제외
threeway["sample_size_num"] = pd.to_numeric(threeway["sample_size"], errors="coerce")
valid = threeway.dropna(subset=["sample_size_num"]).copy()
valid["poll_date"] = pd.to_datetime(valid["poll_date"])
# 최근 폴 (날짜 동률이면 표본 큰 것)
valid = valid.sort_values(["poll_date", "sample_size_num"], ascending=[False, False])
latest_date = valid["poll_date"].max()
latest_poll = valid[valid["poll_date"] == latest_date]

support_map = {row["candidate"]: row["support_pct"] / 100.0 for _, row in latest_poll.iterrows()}
n_sample = int(latest_poll["sample_size_num"].iloc[0])
pollster = str(latest_poll["pollster"].iloc[0])
poll_date_str = latest_date.date().isoformat()
undecided = 1.0 - sum(support_map.values())

POLL = {
    "n": n_sample,
    "pollster": pollster,
    "date": poll_date_str,
    "support": support_map,
    "undecided": float(round(undecided, 4)),
}
print(f"\n[POLL] 자동 선택: {pollster} {poll_date_str} (n={n_sample})")
print(f"       {support_map}, 부동층={POLL['undecided']:.3f}")

# 부동층 배분: 보수 분열 상황에서 한동훈은 부동층 흡수력 ↑
# (인지도, 미디어 노출, "신선함" 효과)
# 시나리오를 여러 개 두고 추후 민감도 분석
UNDECIDED_ALLOCATION = {
    "하정우": 0.35,  # 민주 지지 부동층
    "한동훈": 0.40,  # 보수 + 중도 부동층
    "박민식": 0.25,  # 보수 잔여
}

print("=" * 60)
print("STEP 2. 우도 (Likelihood) - 부산MBC-한길리서치 2026-05-05")
print("=" * 60)
adjusted_poll = {}
for c in CANDIDATES:
    adjusted = POLL["support"][c] + POLL["undecided"] * UNDECIDED_ALLOCATION[c]
    adjusted_poll[c] = adjusted
    print(f"  {c}: 원지지율 {POLL['support'][c]:.3f} → 부동층배분 후 {adjusted:.3f}")
adjusted_total = sum(adjusted_poll.values())
adjusted_poll = {k: v / adjusted_total for k, v in adjusted_poll.items()}
print()

# 여론조사를 "가상 다항분포 관측치"로 변환
poll_counts = np.array([adjusted_poll[c] * POLL["n"] for c in CANDIDATES])
print(f"여론조사 환산 표본 수: {dict(zip(CANDIDATES, poll_counts.round(1)))}")

# ============================================================
# 3. 사후분포 (Dirichlet)
# ============================================================
posterior_alpha = prior_alpha + poll_counts

print()
print("=" * 60)
print("STEP 3. 사후분포 (Posterior Dirichlet)")
print("=" * 60)
print(f"사후 alpha: {dict(zip(CANDIDATES, posterior_alpha.round(2)))}")
posterior_mean = posterior_alpha / posterior_alpha.sum()
print(f"사후 평균 득표율: {dict(zip(CANDIDATES, [f'{x:.3f}' for x in posterior_mean]))}")

# ============================================================
# 4. Monte Carlo 시뮬레이션
# ============================================================
RNG = np.random.default_rng(42)
N_SIMS = 10_000

samples = RNG.dirichlet(posterior_alpha, size=N_SIMS)
# samples.shape = (N_SIMS, 3)

# 당선자 = argmax
winners = np.argmax(samples, axis=1)
win_prob = {CANDIDATES[i]: float((winners == i).mean()) for i in range(3)}

print()
print("=" * 60)
print("STEP 4. Monte Carlo 시뮬레이션 결과 (N=10,000)")
print("=" * 60)
print()
print("【 당선 확률 】")
for c in CANDIDATES:
    bar = "█" * int(win_prob[c] * 50)
    print(f"  {c:6s} {win_prob[c]*100:5.1f}%  {bar}")

print()
print("【 득표율 분포 (5/50/95 퍼센타일) 】")
quantiles = np.percentile(samples, [5, 50, 95], axis=0)
for i, c in enumerate(CANDIDATES):
    print(f"  {c:6s}  {quantiles[0][i]*100:5.1f}%  ~  [{quantiles[1][i]*100:5.1f}%]  ~  {quantiles[2][i]*100:5.1f}%")

# 마진 분포 (1위 - 2위)
sorted_samples = np.sort(samples, axis=1)
margin = sorted_samples[:, -1] - sorted_samples[:, -2]
print()
print("【 1·2위 격차 분포 】")
print(f"  중앙값: {np.median(margin)*100:.2f}%p")
print(f"  5%/95%: [{np.percentile(margin, 5)*100:.2f}%p ~ {np.percentile(margin, 95)*100:.2f}%p]")

# 1%p 이내 박빙 확률
close_race = float((margin < 0.01).mean())
print(f"  1%p 이내 박빙 확률: {close_race*100:.1f}%")

# ============================================================
# 5. 시나리오 분석
# ============================================================
print()
print("=" * 60)
print("STEP 5. 시나리오 분석")
print("=" * 60)

scenarios = {}

# Scenario A: 보수 단일화 (박민식 사퇴 → 한동훈으로 70% 흡수, 하정우로 15%, 기권 15%)
scenarios["보수단일화 (한동훈)"] = posterior_alpha.copy()
park_alpha = scenarios["보수단일화 (한동훈)"][2]
scenarios["보수단일화 (한동훈)"][1] += park_alpha * 0.70
scenarios["보수단일화 (한동훈)"][0] += park_alpha * 0.15
scenarios["보수단일화 (한동훈)"][2] = 0.001  # 거의 없음

# Scenario B: 보수 단일화 (한동훈 사퇴 → 박민식으로 60% 흡수)
scenarios["보수단일화 (박민식)"] = posterior_alpha.copy()
han_alpha = scenarios["보수단일화 (박민식)"][1]
scenarios["보수단일화 (박민식)"][2] += han_alpha * 0.60
scenarios["보수단일화 (박민식)"][0] += han_alpha * 0.20
scenarios["보수단일화 (박민식)"][1] = 0.001

# Scenario C: 한동훈 모멘텀 (지지율 +5%p 추가 상승)
scenarios["한동훈 모멘텀"] = posterior_alpha.copy()
scenarios["한동훈 모멘텀"][1] *= 1.15
scenarios["한동훈 모멘텀"][0] *= 0.92
scenarios["한동훈 모멘텀"][2] *= 0.92

# Scenario D: 민주 결집 (전재수 시장선거 동반효과)
scenarios["민주 결집"] = posterior_alpha.copy()
scenarios["민주 결집"][0] *= 1.15
scenarios["민주 결집"][1] *= 0.94
scenarios["민주 결집"][2] *= 0.94

scenario_results = {}
for name, alpha in scenarios.items():
    sims = RNG.dirichlet(alpha, size=N_SIMS)
    wins = np.argmax(sims, axis=1)
    win_p = {CANDIDATES[i]: float((wins == i).mean()) for i in range(3)}
    means = sims.mean(axis=0)
    scenario_results[name] = {
        "win_prob": win_p,
        "mean_share": {CANDIDATES[i]: float(means[i]) for i in range(3)},
    }
    print(f"\n【 {name} 】")
    for c in CANDIDATES:
        p = win_p[c]
        s = means[CANDIDATES.index(c)]
        bar = "█" * int(p * 30)
        print(f"  {c:6s} 당선확률 {p*100:5.1f}%  평균득표 {s*100:5.1f}%  {bar}")

# 베이스라인도 시나리오에 추가
scenario_results["BASELINE (3자대결 현황)"] = {
    "win_prob": win_prob,
    "mean_share": {CANDIDATES[i]: float(posterior_mean[i]) for i in range(3)},
}

# ============================================================
# 6. 결과 저장
# ============================================================
predictions = {
    "model": "Dirichlet-Multinomial Bayesian + Monte Carlo (N=10,000)",
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
    "poll": {
        "source": POLL["pollster"],
        "date": POLL["date"],
        "n": POLL["n"],
        "support": POLL["support"],
        "undecided": POLL["undecided"],
        "undecided_allocation": UNDECIDED_ALLOCATION,
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
    "scenarios": scenario_results,
}

with open(OUT / "predictions.json", "w", encoding="utf-8") as f:
    json.dump(predictions, f, ensure_ascii=False, indent=2)
print(f"\n예측 결과 저장: {OUT / 'predictions.json'}")

# 일별 스냅샷
snapshot_path = HISTORY / f"{AS_OF}.json"
with open(snapshot_path, "w", encoding="utf-8") as f:
    json.dump(predictions, f, ensure_ascii=False, indent=2)
print(f"일별 스냅샷 저장: {snapshot_path}")

# 히스토리 시계열 (각 날짜별 win_probability + posterior_mean 1줄)
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
            "poll_source": d["poll"]["source"],
            "poll_date": d["poll"]["date"],
        })
    except Exception as e:
        print(f"  skip {snap.name}: {e}")
with open(timeline_path, "w", encoding="utf-8") as f:
    json.dump(timeline, f, ensure_ascii=False, indent=2)
print(f"시계열 저장: {timeline_path} ({len(timeline)} 일)")

# web/data 로 복사
shutil.copy2(OUT / "predictions.json", WEB_DATA / "predictions.json")
shutil.copy2(timeline_path, WEB_DATA / "history_timeline.json")
shutil.copy2(snapshot_path, WEB_HISTORY / f"{AS_OF}.json")
# 플롯도 같이 노출
WEB_PLOTS = ROOT / "web" / "plots"
WEB_PLOTS.mkdir(parents=True, exist_ok=True)
for p in PLOTS.glob("*.png"):
    shutil.copy2(p, WEB_PLOTS / p.name)
print(f"web/data 동기화 완료: {WEB_DATA}")

# ============================================================
# 7. 시각화
# ============================================================
print("\nSTEP 6. 시각화 생성 중...")

# 7-1. 사후 득표율 분포 (히스토그램)
fig, ax = plt.subplots(figsize=(10, 6))
colors = {"하정우": "#0050A0", "한동훈": "#888888", "박민식": "#E61E2B"}
for i, c in enumerate(CANDIDATES):
    ax.hist(samples[:, i] * 100, bins=80, alpha=0.6, label=f"{c} (평균 {posterior_mean[i]*100:.1f}%)", color=colors[c])
    ax.axvline(posterior_mean[i] * 100, color=colors[c], linestyle="--", alpha=0.8)
ax.set_xlabel("득표율 (%)")
ax.set_ylabel("시뮬레이션 빈도")
ax.set_title("부산 북구갑 보궐선거 — 후보별 득표율 사후분포\n(1만회 Monte Carlo 시뮬레이션)")
ax.legend(loc="upper right")
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(PLOTS / "01_vote_share_distribution.png", dpi=120)
plt.close()

# 7-2. 당선 확률 막대
fig, ax = plt.subplots(figsize=(8, 5))
probs = [win_prob[c] * 100 for c in CANDIDATES]
bars = ax.bar(CANDIDATES, probs, color=[colors[c] for c in CANDIDATES])
for b, p in zip(bars, probs):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, f"{p:.1f}%", ha="center", fontsize=14, fontweight="bold")
ax.set_ylabel("당선 확률 (%)")
ax.set_ylim(0, 100)
ax.set_title("부산 북구갑 보궐선거 — 당선 확률 (베이스라인)")
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(PLOTS / "02_win_probability.png", dpi=120)
plt.close()

# 7-3. 시나리오 비교
fig, ax = plt.subplots(figsize=(12, 6))
scenario_names = list(scenario_results.keys())
x = np.arange(len(scenario_names))
width = 0.25
for i, c in enumerate(CANDIDATES):
    vals = [scenario_results[s]["win_prob"][c] * 100 for s in scenario_names]
    ax.bar(x + i * width, vals, width, label=c, color=colors[c])
ax.set_xticks(x + width)
ax.set_xticklabels(scenario_names, rotation=15, ha="right")
ax.set_ylabel("당선 확률 (%)")
ax.set_title("시나리오별 후보 당선 확률")
ax.legend()
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(PLOTS / "03_scenarios.png", dpi=120)
plt.close()

# 7-4. 역대 선거구 추세
fig, ax = plt.subplots(figsize=(12, 6))
na_gap = pd.read_csv(DATA / "national_assembly_buksu_gap.csv")
years = sorted(na_gap["year"].unique())
prog_share = []
cons_share = []
for y in years:
    sub = na_gap[na_gap["year"] == y]
    prog = sub[sub["party"].isin(["더불어민주당"])]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["새누리당", "미래통합당", "국민의힘"])]["vote_share_pct"].sum()
    prog_share.append(prog)
    cons_share.append(cons)
ax.plot(years, prog_share, "o-", color=colors["하정우"], label="민주당 (총선)", linewidth=2, markersize=10)
ax.plot(years, cons_share, "o-", color=colors["박민식"], label="보수 (총선)", linewidth=2, markersize=10)

# 대선·지방선거 (북구 전체) 추세도 추가
pres = pd.read_csv(DATA / "presidential_buksu.csv")
for y in sorted(pres["year"].unique()):
    sub = pres[pres["year"] == y]
    prog = sub[sub["party"] == "더불어민주당"]["vote_share_pct"].sum()
    cons = sub[sub["party"].isin(["자유한국당", "국민의힘"])]["vote_share_pct"].sum()
    ax.plot(y, prog, "s", color=colors["하정우"], alpha=0.4, markersize=10)
    ax.plot(y, cons, "s", color=colors["박민식"], alpha=0.4, markersize=10)

ax.axhline(50, color="black", linestyle=":", alpha=0.5)
ax.set_ylabel("득표율 (%)")
ax.set_xlabel("연도")
ax.set_title("부산 북구갑 역대 선거 추세\n(원=총선, 사각형=대선[북구 전체])")
ax.legend()
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(PLOTS / "04_historical_trend.png", dpi=120)
plt.close()

print(f"플롯 저장: {PLOTS}")
print()
print("=" * 60)
print("완료")
print("=" * 60)
