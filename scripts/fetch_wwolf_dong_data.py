"""
WWolf/korea-election 저장소에서 부산 북구갑 동별 선거 결과 추출.
2016 총선, 2017 대선 emd(읍면동) 단위 데이터를 가져옴.
"""
import io
import sys
from pathlib import Path

import pandas as pd
import requests

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BUSAN_BUKGU_DONGS = [
    "구포1동", "구포2동", "구포3동",
    "금곡동",
    "화명1동", "화명2동", "화명3동",
    "덕천1동", "덕천2동", "덕천3동",
    "만덕1동", "만덕2동", "만덕3동",
]

def fetch_tsv(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text), sep="\t")

def main():
    print("[1/2] 2016 총선 데이터 가져오는 중...")
    df_2016 = fetch_tsv(
        "https://raw.githubusercontent.com/WWolf/korea-election/master/dataset/2016general_cand_full.tsv"
    )
    print(f"  전체 row: {len(df_2016):,}")
    print(f"  columns: {list(df_2016.columns)}")

    busan = df_2016[df_2016["광역"] == "부산광역시"]
    bukgu = busan[busan["시군구"] == "북구"]
    print(f"  부산 북구 row: {len(bukgu):,}")
    print(f"  포함 동: {sorted(bukgu['읍면동'].unique())}")

    bukgu.to_csv(OUT_DIR / "2016_NA_busan_bukgu_dong.csv", index=False, encoding="utf-8-sig")
    print(f"  → 저장: 2016_NA_busan_bukgu_dong.csv")

    print("\n[2/2] 2017 대선 데이터 가져오는 중...")
    df_2017 = fetch_tsv(
        "https://raw.githubusercontent.com/WWolf/korea-election/master/dataset/2017presidential_emd.tsv"
    )
    print(f"  전체 row: {len(df_2017):,}")
    print(f"  columns: {list(df_2017.columns)}")

    busan17 = df_2017[df_2017["광역"] == "부산광역시"]
    bukgu17 = busan17[busan17["시군구"] == "북구"]
    print(f"  부산 북구 row: {len(bukgu17):,}")
    print(f"  포함 동: {sorted(bukgu17['읍면동'].unique())}")

    bukgu17.to_csv(OUT_DIR / "2017_Pres_busan_bukgu_dong.csv", index=False, encoding="utf-8-sig")
    print(f"  → 저장: 2017_Pres_busan_bukgu_dong.csv")

    print("\n완료. 저장 위치:", OUT_DIR)

if __name__ == "__main__":
    main()
