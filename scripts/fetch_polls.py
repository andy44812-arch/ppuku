#!/usr/bin/env python3
"""
fetch_polls.py — NESDC 신규 여론조사 발굴

중앙선거여론조사심의위원회(NESDC) 등록 목록을 매일 긁어와서,
서울시장(2026_seoul_mayor)·부산 북구갑 보궐(2026_buksu_gap_byelection)
관련 폴 중 우리 CSV에 아직 없는 것을 식별한다.

NESDC에는 후보별 지지율(%)이 메타데이터로 노출되지 않는다 — 첨부 PDF에만 있음.
따라서 이 스크립트는:
  1) NESDC 메타 데이터(조사기관, 의뢰자, 표본, 응답률, 조사일시, 공표일시)를 긁어
  2) 기존 CSV와 매칭해 신규/이미 보유 폴을 분리하고
  3) 신규 폴에 대해서는 후보별 지지율을 채워야 하는 "pending" 항목으로 출력한다.

사람이(또는 다음 Claude 세션에서) 의뢰자 매체의 뉴스 기사를 보고
정원오/오세훈 (또는 하정우/한동훈/박민식) 지지율을 채워서 CSV에 append.

사용:
    python scripts/fetch_polls.py             # 최근 7일 검사
    python scripts/fetch_polls.py --days 14   # 최근 14일 검사
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NESDC_LIST = "https://www.nesdc.go.kr/portal/bbs/B0000005/list.do"
NESDC_VIEW_FMT = "https://www.nesdc.go.kr/portal/bbs/B0000005/view.do?nttId={ntt_id}&menuNo=200467"

# 우리가 추적하는 선거별로 NESDC 검색 키워드 + CSV 정보
RACES = {
    "seoul": {
        "csv": ROOT / "data" / "raw" / "seoul" / "current_polls_seoul.csv",
        "pending": ROOT / "data" / "raw" / "seoul" / "pending_polls_seoul.csv",
        "election": "2026_seoul_mayor",
        "candidates": ["정원오", "오세훈"],
        "parties": {"정원오": "더불어민주당", "오세훈": "국민의힘"},
        # NESDC '지역' 필드 / '선거명'에 등장하는 패턴
        "region_match": [r"서울특별시\s*전체", r"서울특별시\s*광역단체장"],
        "election_match": [r"광역단체장선거"],
    },
    "busan": {
        "csv": ROOT / "data" / "raw" / "busan" / "current_polls_busan.csv",
        "pending": ROOT / "data" / "raw" / "busan" / "pending_polls_busan.csv",
        "election": "2026_buksu_gap_byelection",
        "candidates": ["하정우", "한동훈", "박민식"],
        "parties": {"하정우": "더불어민주당", "한동훈": "무소속", "박민식": "국민의힘"},
        "region_match": [r"부산광역시\s*북구\s*갑", r"북구\s*갑", r"북구갑"],
        "election_match": [r"국회의원\s*재.?보궐", r"국회의원\s*보궐"],
    },
}

# 매체 lean (House Effect 보정용) — 의뢰자명으로 매핑
LEAN_MAP = {
    "조선": "right", "채널A": "right", "MBN": "right", "TV조선": "right",
    "동아": "right", "펜앤마이크": "right", "뉴데일리": "right",
    "MBC": "centerleft", "KBS": "centerleft", "JTBC": "centerleft",
    "CBS": "centerleft", "한겨레": "centerleft", "오마이뉴스": "centerleft",
    "경향": "centerleft", "스트레이트뉴스": "centerleft",
    "여론조사꽃": "left", "민들레": "left", "뉴스토마토": "left",
    "SBS": "center", "중앙": "center", "한국": "center",
    "뉴스1": "center", "뉴시스": "center", "연합": "center",
    "국제신문": "center", "부산일보": "center", "폴리뉴스": "center",
    "머니투데이": "center", "헤럴드": "center", "파이낸셜": "center",
}


def detect_lean(commissioner: str, pollster: str) -> str:
    """매체+조사기관 이름으로 lean 추정. 기본 center."""
    text = f"{commissioner} {pollster}"
    for kw, lean in LEAN_MAP.items():
        if kw in text:
            return lean
    return "center"


@dataclass
class Poll:
    ntt_id: str = ""
    reg_no: str = ""             # 등록 글번호
    pollster: str = ""           # 조사기관명
    commissioner: str = ""       # 조사의뢰자
    survey_region: str = ""      # 조사지역
    election_name: str = ""      # 선거명
    fieldwork_start: str = ""    # YYYY-MM-DD
    fieldwork_end: str = ""      # YYYY-MM-DD
    publication: str = ""        # 최초 공표일 YYYY-MM-DD
    sample_size: int = 0
    response_rate_pct: float = 0.0
    method: str = ""             # 무선 ARS / 전화면접 / 혼합 등
    notes: str = ""
    detail_url: str = ""


def http_get(url: str, *, data: dict | None = None, timeout: int = 30) -> str:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_list_rows(html: str) -> list[tuple[str, list[str]]]:
    """NESDC 목록 페이지에서 row 추출. (ntt_id, [컬럼들]) 리스트."""
    rows = re.findall(
        r'<a href="(/portal/bbs/B0000005/view\.do\?nttId=(\d+)[^"]*)"[^>]*class="row tr"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    )
    out = []
    for _href, ntt, body in rows:
        cells = re.findall(r'<i class="tit"[^>]*></i>([^<]+)', body)
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        out.append((ntt, cells))
    return out


def fetch_list(days_back: int, *, max_pages: int = 30) -> list[Poll]:
    """NESDC 목록 페이지를 긁어 메타만 가진 Poll 리스트 반환.

    NESDC의 searchKrwd 필터가 신뢰성 없게 동작해서, 키워드 없이
    날짜 범위만으로 페이지네이션한다. 지역/선거명 매칭은 Python 측에서.
    NESDC pageUnit=30이지만 실제로는 페이지당 10개씩 반환되는 듯.
    """
    import time
    today = date.today()
    start = today - timedelta(days=days_back)
    polls: list[Poll] = []
    seen_ntt: set[str] = set()
    for page in range(1, max_pages + 1):
        data = {
            "pageIndex": str(page),
            "pageUnit": "10",
            "menuNo": "200467",
            "searchTime": "1",  # 등록일 기준
            "searchBgnDe": start.strftime("%Y-%m-%d"),
            "searchEndDe": today.strftime("%Y-%m-%d"),
            "searchCnd": "ttl",
            "searchKrwd": "",
        }
        try:
            html = http_get(NESDC_LIST, data=data)
        except Exception as e:
            print(f"  ! 목록 page {page} fetch 실패: {e}")
            break
        rows = parse_list_rows(html)
        if not rows:
            break
        new_on_page = 0
        for ntt, cells in rows:
            if ntt in seen_ntt:
                continue
            seen_ntt.add(ntt)
            new_on_page += 1
            # cells: 등록번호|조사기관|의뢰자|조사방법|추출틀|선거명|등록일|지역
            if len(cells) < 8:
                continue
            polls.append(Poll(
                ntt_id=ntt,
                reg_no=cells[0],
                pollster=cells[1],
                commissioner=cells[2],
                method=cells[3],
                election_name=cells[5],
                publication=cells[6],
                survey_region=cells[7],
                detail_url=NESDC_VIEW_FMT.format(ntt_id=ntt),
            ))
        if new_on_page == 0:
            # 이미 본 항목만 — 페이지 끝
            break
        time.sleep(0.3)  # 부하 분산
    return polls


def parse_detail(html: str, p: Poll) -> Poll:
    """디테일 페이지에서 표본수·응답률·조사일시·공표일시 등 채움."""
    # th/td 페어 추출
    pairs = dict(re.findall(r'<th[^>]*>([^<]+)</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL))
    # th 텍스트 정리
    clean = {}
    for k, v in pairs.items():
        kk = re.sub(r"\s+", " ", k).strip()
        vv = re.sub(r"<[^>]+>", " ", v)
        vv = re.sub(r"\s+", " ", vv).strip()
        clean.setdefault(kk, vv)

    if "조사의뢰자" in clean and not p.commissioner:
        p.commissioner = clean["조사의뢰자"].replace("방송사 : ", "").replace("뉴스통신사 : ", "").strip()
    if "조사기관명" in clean:
        p.pollster = clean["조사기관명"]
    if "조사지역" in clean:
        p.survey_region = clean["조사지역"]
    if "전체" in clean:
        m = re.search(r"\d[\d,]*", clean["전체"])
        if m:
            p.sample_size = int(m.group(0).replace(",", ""))

    # 응답률
    m = re.search(r'전체\s*응답률[^<]*</th>\s*<td[^>]*>\s*([\d.]+)\s*%', html)
    if not m:
        m = re.search(r'응답률[^<]*\(I/\(I\+R\)\)[^<]*</th>\s*<td[^>]*>\s*([\d.]+)\s*%', html)
    if m:
        p.response_rate_pct = float(m.group(1))

    # 조사일시 — JS로 채워지는 경우 많음. HTML 안에 raw 데이터 들어가 있을 수도.
    # "조사일시" td 안에 날짜 패턴 찾기
    m = re.search(r'조사일시[^<]*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
    if m:
        dates = re.findall(r'(20\d{2}-\d{2}-\d{2})', m.group(1))
        if dates:
            p.fieldwork_start = min(dates)
            p.fieldwork_end = max(dates)

    # 최초 공표일시
    m = re.search(r'최초[^<]*공표[^<]*지정일시[^<]*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
    if m:
        d = re.search(r'(20\d{2}-\d{2}-\d{2})', m.group(1))
        if d:
            p.publication = d.group(1)

    return p


def matches_race(p: Poll, race_cfg: dict) -> bool:
    blob = f"{p.election_name} {p.survey_region}"
    for pat in race_cfg["region_match"]:
        if re.search(pat, blob):
            return True
    return False


def load_existing(csv_path: Path) -> set[tuple[str, str, str]]:
    """기존 CSV의 (조사기관, fw_start, fw_end) 키 셋."""
    if not csv_path.exists():
        return set()
    keys = set()
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # 조사기관명 패턴: "MBC-코리아리서치(1차)" 처럼 매체+기관 결합. 매칭 느슨하게.
            keys.add((row["pollster"], row["fieldwork_start"], row["fieldwork_end"]))
    return keys


def existing_pollster_blobs(csv_path: Path) -> list[tuple[str, str, str]]:
    """기존 CSV의 (full_label, fw_start, fw_end) 풀 list."""
    if not csv_path.exists():
        return []
    out = []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append((row["pollster"], row["fieldwork_start"], row["fieldwork_end"]))
    return out


def _norm(s: str) -> str:
    """Compare keys after stripping suffixes/parens/whitespace."""
    s = re.sub(r"\(.*?\)|주식회사|\(주\)|㈜|\s+", "", s)
    return s


def has_existing(p: Poll, existing: list[tuple[str, str, str]]) -> bool:
    """우리 CSV에 같은 조사가 이미 있는지 — fw_start/end + pollster 키워드로 매칭.

    날짜는 ±1일까지 허용 (NESDC와 기사가 조사기간을 약간 다르게 기재하는 경우 있음).
    """
    if not (p.fieldwork_start and p.fieldwork_end):
        return False
    nesdc_pollster = _norm(p.pollster)
    nesdc_commish = _norm(p.commissioner)

    def dates_close(a: str, b: str) -> bool:
        try:
            da = datetime.strptime(a, "%Y-%m-%d").date()
            db = datetime.strptime(b, "%Y-%m-%d").date()
            return abs((da - db).days) <= 1
        except ValueError:
            return False

    for label, fs, fe in existing:
        if not (dates_close(fs, p.fieldwork_start) and dates_close(fe, p.fieldwork_end)):
            continue
        norm_label = _norm(label)
        # 자체 조사 (의뢰자 == 조사기관) → pollster만 매칭
        if nesdc_pollster and nesdc_pollster in norm_label:
            return True
        if nesdc_commish and nesdc_commish in norm_label:
            return True
    return False


PENDING_HEADER = [
    "poll_date", "fieldwork_start", "fieldwork_end", "pollster", "pollster_lean",
    "sample_size", "response_rate_pct", "election", "candidate", "party",
    "support_pct", "notes",
]


def emit_pending_rows(p: Poll, race_cfg: dict, race_key: str) -> list[list[str]]:
    """후보별 지지율은 빈 칸으로, 나머지는 NESDC 메타로 채워서 row 생성."""
    pollster_clean = re.sub(r"^\(주\)|주식회사", "", p.pollster).strip()
    label = f"{p.commissioner}-{pollster_clean}"
    label = re.sub(r"\s+", "", label)
    lean = detect_lean(p.commissioner, p.pollster)
    method_note = f"NESDC {p.reg_no} | {p.method} | {p.detail_url}"
    rows = []
    for c in race_cfg["candidates"]:
        rows.append([
            p.publication or "",
            p.fieldwork_start or "",
            p.fieldwork_end or "",
            label,
            lean,
            str(p.sample_size or ""),
            f"{p.response_rate_pct:.1f}" if p.response_rate_pct else "",
            race_cfg["election"],
            c,
            race_cfg["parties"][c],
            "",  # support_pct — 수동 채움
            method_note,
        ])
    return rows


def write_pending(path: Path, rows: Iterable[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(PENDING_HEADER)
        for r in rows:
            w.writerow(r)


def process_race(race_key: str, days_back: int, all_polls: list[Poll], *, verbose: bool = True) -> int:
    cfg = RACES[race_key]
    if verbose:
        print(f"\n=== {race_key.upper()} (최근 {days_back}일) ===")

    # 우리 선거에 해당하는 것만 (목록 단계에서 region/선거명으로 1차 필터)
    candidates = [p for p in all_polls if matches_race(p, cfg)]
    if verbose:
        print(f"  1차 필터(목록): {len(candidates)}건")

    # 디테일 fetch로 fw_start/end, 표본, 응답률 채우기
    relevant = []
    for p in candidates:
        try:
            detail_html = http_get(p.detail_url)
        except Exception as e:
            print(f"  ! detail fetch 실패 {p.ntt_id}: {e}")
            continue
        p = parse_detail(detail_html, p)
        # 디테일에서 조사지역/선거명 재확인
        if matches_race(p, cfg):
            relevant.append(p)

    if verbose:
        print(f"  디테일 확인 후 관련 폴: {len(relevant)}건")

    existing = existing_pollster_blobs(cfg["csv"])
    new_polls = [p for p in relevant if not has_existing(p, existing)]

    if not new_polls:
        if verbose:
            print(f"  ✓ 새로운 폴 없음. CSV 최신 상태.")
        return 0

    # 새로운 폴을 pending에 쓴다
    all_rows = []
    for p in new_polls:
        all_rows.extend(emit_pending_rows(p, cfg, race_key))

    write_pending(cfg["pending"], all_rows)

    if verbose:
        print(f"  ★ 신규 {len(new_polls)}건 → {cfg['pending'].relative_to(ROOT)}")
        for p in new_polls:
            print(f"    [reg {p.reg_no}] {p.commissioner}-{p.pollster} "
                  f"fw={p.fieldwork_start}~{p.fieldwork_end} pub={p.publication} "
                  f"n={p.sample_size} resp={p.response_rate_pct}% — {p.detail_url}")
        print(f"  → 의뢰자 기사에서 후보별 지지율을 확인해 "
              f"{cfg['pending'].relative_to(ROOT)} 의 support_pct 칸을 채운 뒤,")
        print(f"     해당 row 들을 {cfg['csv'].relative_to(ROOT)} 에 append 하세요.")

    return len(new_polls)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--days", type=int, default=7, help="최근 N일 (default: 7)")
    ap.add_argument("--race", choices=list(RACES) + ["all"], default="all")
    ns = ap.parse_args()

    print(f"NESDC 목록 fetching (최근 {ns.days}일)...")
    # Pagination: NESDC pageUnit max 30 → max_pages 더 크게
    all_polls = fetch_list(ns.days, max_pages=20)
    print(f"  전체 {len(all_polls)}건 등록 확인")

    races = list(RACES) if ns.race == "all" else [ns.race]
    total = 0
    for r in races:
        total += process_race(r, ns.days, all_polls)

    print(f"\n총 신규 폴: {total}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
