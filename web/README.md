# 부산 북구갑 보궐선거 예측 대시보드

`2026년 6월 3일` 부산 북구갑 국회의원 보궐선거 예측 웹사이트.
베이지안(Dirichlet-Multinomial) + Monte Carlo (N=10,000) 모델, **매일 자동 업데이트**.

## 빠르게 보기

```bash
# 프로젝트 루트에서
./scripts/serve.sh
```

브라우저가 자동으로 열리며 `http://localhost:8765` 로 접속됩니다.
종료하려면 `Ctrl+C`.

## 매일 자동 업데이트 (이미 설정됨)

매일 **오전 7시 (현지 시간)** 에 launchd 가 자동으로 `scripts/update_daily.sh` 를 실행합니다.

스크립트가 하는 일:
1. `.venv/bin/python scripts/predict.py` 실행
2. `data/processed/predictions.json` 갱신
3. `data/processed/history/YYYY-MM-DD.json` 일별 스냅샷 저장
4. `data/processed/history_timeline.json` 시계열 갱신
5. `web/data/`, `web/plots/` 동기화
6. 실행 로그를 `logs/update_YYYY-MM-DD.log` 에 기록 (30일치 보관)

### launchd 상태 확인

```bash
launchctl list | grep election-prediction
```

`0` (마지막 exit code) 가 보이면 정상 등록.

### 수동 실행

```bash
./scripts/update_daily.sh
```

특정 날짜로 강제 실행하려면:

```bash
PREDICT_AS_OF=2026-05-20 .venv/bin/python scripts/predict.py
```

### 자동 업데이트 끄기/켜기

```bash
# 끄기
launchctl unload ~/Library/LaunchAgents/com.minkyulee.election-prediction.daily.plist
# 켜기
launchctl load ~/Library/LaunchAgents/com.minkyulee.election-prediction.daily.plist
```

## 폴 데이터 추가하기

`data/raw/current_polls_2026.csv` 에 새 여론조사 행을 추가하면 다음 실행부터 자동으로 가장 최신 폴이 모델에 반영됩니다.

필수 컬럼:
- `poll_date` (YYYY-MM-DD)
- `pollster`
- `sample_size` (숫자여야 자동 선택됨, 비어있거나 `(unknown)` 이면 무시됨)
- `election` = `2026_buksu_gap_byelection`
- `candidate` (하정우/한동훈/박민식)
- `support_pct` (예: 34.3)

3자 대결 폴이라면 3개 행을 함께 추가하세요.

## 가정 바꿔보기

`scripts/predict.py` 상단의 다음 상수를 조정해 다시 실행:

| 상수 | 현재 | 의미 |
|------|------|------|
| `HAN_SHARE_OF_CONSERVATIVE` | 0.65 | 보수 표 중 한동훈 흡수율 |
| `PRIOR_STRENGTH` | 200 | 사전 강도 (높을수록 과거 데이터 의존↑) |
| `UNDECIDED_ALLOCATION` | 0.35/0.40/0.25 | 부동층 배분 (하/한/박) |
| `boy_election_shift` | -0.02 | 보궐 투표율 하락 보정 |

## 디렉토리 구조

```
web/
├── index.html        # 메인 페이지
├── style.css         # 다크 대시보드 스타일
├── app.js            # Chart.js 렌더링 로직
├── data/             # predict.py 가 생성하는 JSON
│   ├── predictions.json
│   ├── history_timeline.json
│   └── history/YYYY-MM-DD.json
└── plots/            # 정적 PNG 차트
```

## 모델 한계

- 폴 1회(부산MBC) 에만 의존 — 다른 기관 폴이 추가되면 정확도 향상
- 동별 개표 데이터 미반영 — 13개 행정동 단위 예측 추후 추가 예정
- 부산시장 동시 선거 연동 효과 미반영
