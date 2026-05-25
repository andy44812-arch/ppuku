#!/usr/bin/env python3
"""Build poll-based trend timeline for Seoul & Busan elections.

For each day in [2026-05-01, today] compute a fieldwork-midpoint
weighted moving average of all polls. Weight = sqrt(n_eff) * 2^(-days_old / halflife).
Window = 21 days, halflife = 7 days (matches the main prediction model).

Output: web/data/{seoul,busan}/poll_trend.json
- raw poll points (per pollster fieldwork window)
- daily smoothed trend (one entry per day)
- golden-cross detection: actual (in trend so far) + linear extrapolation
  of the recent gap to estimate when the challenger crosses the leader.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# 예측 스크립트와 동일한 규약: PREDICT_AS_OF(KST) 우선, 없으면 오늘.
# 하드코딩 금지 — 매일 추세선/골든크로스가 최신 날짜까지 갱신되어야 함.
TODAY = date.fromisoformat(os.environ.get("PREDICT_AS_OF", date.today().isoformat()))
START = date(2026, 5, 1)
ELECTION_DAY = date(2026, 6, 3)
HALFLIFE_DAYS = 7
WINDOW_DAYS = 21
EXTRAPOLATION_LOOKBACK_DAYS = 12


def load_polls(csv_path: Path, election_ids: set[str]):
    polls = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["election"] not in election_ids:
                continue
            polls.append({
                "pollster": row["pollster"],
                "lean": row["pollster_lean"],
                "fw_start": date.fromisoformat(row["fieldwork_start"]),
                "fw_end": date.fromisoformat(row["fieldwork_end"]),
                "n": int(row["sample_size"]),
                "candidate": row["candidate"],
                "support": float(row["support_pct"]) / 100,
                "election": row["election"],
            })
    return polls


def group_polls(polls):
    grouped: dict[tuple, dict] = {}
    for p in polls:
        key = (p["pollster"], p["fw_start"], p["fw_end"], p["election"])
        if key not in grouped:
            span = (p["fw_end"] - p["fw_start"]).days
            mid = p["fw_start"] + timedelta(days=span // 2)
            grouped[key] = {
                "pollster": p["pollster"],
                "lean": p["lean"],
                "fw_start": p["fw_start"],
                "fw_end": p["fw_end"],
                "mid": mid,
                "n": p["n"],
                "election": p["election"],
                "support": {},
            }
        grouped[key]["support"][p["candidate"]] = p["support"]
    out = list(grouped.values())
    out.sort(key=lambda g: g["mid"])
    return out


def compute_trend(grouped, candidates, start_d: date, end_d: date,
                  halflife=HALFLIFE_DAYS, window=WINDOW_DAYS):
    trend = []
    days = (end_d - start_d).days + 1
    for i in range(days):
        d = start_d + timedelta(days=i)
        wsum = {c: 0.0 for c in candidates}
        norm = {c: 0.0 for c in candidates}
        n_polls_used = 0
        for gp in grouped:
            mid = gp["mid"]
            if mid > d:
                continue
            days_old = (d - mid).days
            if days_old > window:
                continue
            w = math.sqrt(gp["n"]) * (0.5 ** (days_old / halflife))
            counted = False
            for c in candidates:
                if c in gp["support"]:
                    wsum[c] += gp["support"][c] * w
                    norm[c] += w
                    counted = True
            if counted:
                n_polls_used += 1
        entry = {"date": d.isoformat(), "support": {}, "n_polls": n_polls_used}
        any_val = False
        for c in candidates:
            if norm[c] > 0:
                entry["support"][c] = wsum[c] / norm[c]
                any_val = True
            else:
                entry["support"][c] = None
        if any_val:
            trend.append(entry)
    return trend


def find_actual_cross(trend, leader, challenger):
    """First date the challenger ≥ leader in the smoothed trend (from below)."""
    prev = None
    for entry in trend:
        s = entry["support"]
        a, b = s.get(leader), s.get(challenger)
        if a is None or b is None:
            prev = None
            continue
        if prev is not None:
            pl, pc = prev
            if pc < pl and b >= a:
                return entry["date"]
        prev = (a, b)
    return None


def linear_extrapolate(trend, leader, challenger,
                       lookback=EXTRAPOLATION_LOOKBACK_DAYS,
                       election_d: date = ELECTION_DAY):
    """Fit gap = leader-challenger on recent trend, extrapolate to gap=0."""
    valid = [e for e in trend
             if e["support"].get(leader) is not None
             and e["support"].get(challenger) is not None]
    if len(valid) < 3:
        return None
    recent = valid[-lookback:] if len(valid) > lookback else valid

    base = date.fromisoformat(recent[0]["date"])
    xs = [(date.fromisoformat(e["date"]) - base).days for e in recent]
    ys = [e["support"][leader] - e["support"][challenger] for e in recent]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    intercept = my - slope * mx
    gap_today = ys[-1]
    res = {
        "lookback_days_used": len(recent),
        "gap_today_pct": gap_today * 100,
        "gap_change_pct_per_day": slope * 100,
        "method": "OLS on (date, gap=leader-challenger) for the trailing window",
    }
    if slope >= 0:
        res["cross_date_extrapolated"] = None
        res["days_from_today"] = None
        res["reaches_before_election"] = False
        res["note"] = "추세상 격차가 좁혀지지 않음 (slope ≥ 0)"
        return res

    days_to_cross = -intercept / slope
    cross_d = base + timedelta(days=int(round(days_to_cross)))
    res["cross_date_extrapolated"] = cross_d.isoformat()
    res["days_from_today"] = (cross_d - TODAY).days
    res["reaches_before_election"] = cross_d <= election_d
    return res


def build_region(csv_path: Path, election_ids: set[str], candidates,
                 leader: str, challenger: str, label: str, out_path: Path):
    raw = load_polls(csv_path, election_ids)
    grouped = group_polls(raw)
    trend = compute_trend(grouped, candidates, START, TODAY)
    actual_cross = find_actual_cross(trend, leader, challenger)
    extrap = linear_extrapolate(trend, leader, challenger)

    last = trend[-1] if trend else None

    out = {
        "as_of": TODAY.isoformat(),
        "election": label,
        "election_date": ELECTION_DAY.isoformat(),
        "candidates": candidates,
        "golden_cross_pair": {"leader": leader, "challenger": challenger},
        "method": (
            f"폴 fieldwork midpoint 기준 √n × 2^(-days_old/{HALFLIFE_DAYS}) "
            f"가중 일별 이동평균. 윈도우 {WINDOW_DAYS}일, 반감기 {HALFLIFE_DAYS}일."
        ),
        "halflife_days": HALFLIFE_DAYS,
        "window_days": WINDOW_DAYS,
        "trend_start": START.isoformat(),
        "polls": [
            {
                "pollster": gp["pollster"],
                "lean": gp["lean"],
                "fieldwork_start": gp["fw_start"].isoformat(),
                "fieldwork_end": gp["fw_end"].isoformat(),
                "fw_midpoint": gp["mid"].isoformat(),
                "n": gp["n"],
                "support": gp["support"],
            }
            for gp in grouped
        ],
        "trend": trend,
        "current_support": (last["support"] if last else None),
        "current_gap_pct": (
            (last["support"][leader] - last["support"][challenger]) * 100
            if last and last["support"].get(leader) is not None and last["support"].get(challenger) is not None
            else None
        ),
        "golden_cross": {
            "actual_observed_date": actual_cross,
            "extrapolation": extrap,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    print("== Seoul ==")
    seoul = build_region(
        csv_path=REPO / "data/raw/seoul/current_polls_seoul.csv",
        election_ids={"2026_seoul_mayor"},
        candidates=["정원오", "오세훈"],
        leader="정원오",
        challenger="오세훈",
        label="2026-06-03 서울특별시장 선거",
        out_path=REPO / "web/data/seoul/poll_trend.json",
    )
    print(f"  polls grouped: {len(seoul['polls'])}, trend days: {len(seoul['trend'])}")
    print(f"  current gap (정원오 − 오세훈): {seoul['current_gap_pct']:.2f}%p")
    gc = seoul["golden_cross"]
    print(f"  actual cross observed: {gc['actual_observed_date']}")
    if gc["extrapolation"]:
        ex = gc["extrapolation"]
        print(f"  extrapolated cross: {ex.get('cross_date_extrapolated')} "
              f"(slope {ex['gap_change_pct_per_day']:.3f}%p/일, "
              f"D−{ex.get('days_from_today')}일)")

    print("== Busan (3-way) ==")
    busan = build_region(
        csv_path=REPO / "data/raw/busan/current_polls_busan.csv",
        election_ids={"2026_buksu_gap_byelection"},
        candidates=["하정우", "한동훈", "박민식"],
        leader="하정우",
        challenger="한동훈",
        label="2026-06-03 부산 북구갑 보궐선거",
        out_path=REPO / "web/data/busan/poll_trend.json",
    )
    print(f"  polls grouped: {len(busan['polls'])}, trend days: {len(busan['trend'])}")
    print(f"  current gap (하정우 − 한동훈): {busan['current_gap_pct']:.2f}%p")
    gc = busan["golden_cross"]
    print(f"  actual cross observed: {gc['actual_observed_date']}")
    if gc["extrapolation"]:
        ex = gc["extrapolation"]
        print(f"  extrapolated cross: {ex.get('cross_date_extrapolated')} "
              f"(slope {ex['gap_change_pct_per_day']:.3f}%p/일, "
              f"D−{ex.get('days_from_today')}일)")


if __name__ == "__main__":
    main()
