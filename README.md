# disk_cleanup.py — 로컬 디스크 공간 확보  
<sub>2026-07-29  Jonghyun Park w/ Claude</sub>  
랩탑 C: 여유 공간이 부족할 때 ① 재생성 가능한 캐시를 지우고 ② OneDrive 파일을 "온라인 전용"으로 되돌려 로컬 점유를 비우는 Windows 전용 스크립트.

---

## 배경 — 왜 용량이 차는가

OneDrive Files On-Demand 는 파일을 열면 로컬로 내려받고(hydrate) 그대로 **로컬에 남긴다**. 캡처 PNG·MHTML 처럼 한 번 보고 마는 대용량 파일이 쌓이면 클라우드에는 279 GB, 그중 로컬 점유가 40 GB 씩 되는 상황이 생긴다.

`attrib -P +U <파일>` 은
- `-P` : "이 장치에 항상 유지" 핀 해제
- `+U` : "공간 확보(온라인 전용)" 표시 → OneDrive 가 로컬 내용을 회수

**파일은 클라우드에 그대로 남고 목록에도 계속 보인다.** 더블클릭하면 다시 내려받는다(삭제 아님).

---

## 사용법

```bash
python disk_cleanup.py              # 현황 + dry-run (아무것도 안 바꿈)
python disk_cleanup.py --report     # 현황 리포트만 — 어느 폴더/확장자가 로컬을 먹는지
python disk_cleanup.py --cache --apply       # 캐시 삭제만 실제 수행
python disk_cleanup.py --dehydrate --apply   # OneDrive 온라인화만 실제 수행
python disk_cleanup.py --apply               # 둘 다 (RUN_* 상수 기준)
```

- **기본은 항상 dry-run** — `--apply` 없이는 어떤 파일도 건드리지 않는다.
- 실행 로그는 스크립트 폴더에 `disk_cleanup_log_YYMMDD_HHMM.txt` 로 남는다 (`LOG_TO_FILE`).

---

## 상단 상수

### 실행 모드
| 상수 | 설명 |
|---|---|
| `RUN_CACHE_CLEAN` / `RUN_DEHYDRATE` | 플래그(`--cache`/`--dehydrate`) 없이 `--apply` 만 줬을 때 무엇을 돌릴지 |

### OneDrive 온라인화
| 상수 | 설명 |
|---|---|
| `ONEDRIVE_ROOT` | 스캔 시작 경로. **다른 PC 는 이 줄만 교체** |
| `DEHYDRATE_EXTS` | 대상 확장자. `[]` 로 비우면 확장자 무관 전체 (최대 확보, 대신 열 때마다 다운로드) |
| `DEHYDRATE_EXCLUDE_DIRS` | `.git`·`node_modules`·`.venv` 등 제외 — **git 저장소를 온라인화하면 git 이 매우 느려지고 깨질 수 있음** |
| `DEHYDRATE_EXCLUDE_KEYWORDS` | 작업 중 폴더 제외용 (경로 substring, 대소문자 무시). 예: `["260721_"]` |
| `DEHYDRATE_MIN_MB` | 이 크기 미만은 건너뜀 (기본 0.5MB) — 작은 파일은 효과 대비 재다운로드 번거로움만 큼 |

### 캐시 삭제
| 상수 | 설명 |
|---|---|
| `CACHE_TARGETS` | `(경로, 설명)` 목록. **폴더 자체는 유지하고 하위 항목만** 삭제. 주석으로 pip/playwright 예시 포함 |
| `CACHE_PROTECT_KEYWORDS` | 경로에 이 문자열이 있으면 삭제 제외. 기본 `\claude` — **`Temp\claude` 는 실행 중인 Claude Code 세션 작업 파일이라 지우면 진행 중 작업이 끊긴다** (실제로 겪은 사고) |
| `CACHE_MAX_AGE_DAYS` | 숫자를 넣으면 그보다 오래된 항목만 삭제 (`None` = 전부) |

### 공통
`DRIVE`(여유 공간 표시 대상), `PROGRESS_EVERY`(진행률 출력 간격), `REPORT_TOP_N`, `LOG_TO_FILE` / `LOG_BASENAME`

---

## 동작 요약

| 모드 | 하는 일 |
|---|---|
| `--report` | 로컬에 내려와 있는(hydrate 된) 파일만 집계 → 상위 폴더·확장자 랭킹 출력. 변경 없음 |
| 캐시 삭제 | `CACHE_TARGETS` 하위 항목 삭제. 사용 중이라 못 지우는 파일은 자동 skip |
| 온라인화 | 조건에 맞는 파일마다 `attrib -P +U` 실행, `PROGRESS_EVERY` 마다 진행률·여유 공간 출력 |

파일 상태 판별은 Windows 파일 속성 비트로 한다 — `FILE_ATTRIBUTE_OFFLINE(0x1000)` / `RECALL_ON_DATA_ACCESS(0x400000)` 가 있으면 이미 온라인 전용이라 대상에서 제외, `PINNED(0x80000)` 는 `-P` 로 해제해야 온라인화가 먹는다.

---

## 되돌리기 (다시 로컬로)

```bash
# 특정 폴더를 항상 로컬 유지로
attrib -U +P "C:\...\폴더\*" /s /d
```
또는 탐색기에서 폴더 우클릭 → **"이 장치에 항상 유지"**. 개별 파일은 그냥 열면 자동으로 받아진다.

---

## 주의

- **OneDrive 동기화가 완료된 파일만** 온라인화된다. 아직 업로드 안 된 파일은 `attrib` 이 실패하고 그대로 로컬에 남는다(안전).
- 오프라인(비행기/네트워크 없음) 상태에서 필요한 자료는 미리 열어두거나 `DEHYDRATE_EXCLUDE_KEYWORDS` 로 제외할 것.
- 캐시 삭제는 되돌릴 수 없지만 전부 재생성되는 항목이다 (npm 캐시는 다음 설치 시 재다운로드, 크래시 덤프는 진단용 잔재).
- 실행 중 다른 프로그램이 쓰고 있는 파일은 건너뛴다 — 필요하면 브라우저·에디터를 닫고 재실행.

## 실제 적용 기록 (2026-07-21)

| 단계 | 결과 |
|---|---|
| 캐시 삭제 (CrashDumps 3.7 + npm-cache 6.5 + Temp 4.8 GB) | 여유 1.8 GB → 16.9 GB |
| 이미지·캡처류 온라인화 (png/jpg/mhtml/mp4, 8,208 파일 / 24.7 GB) | 순차 처리 |

로컬 점유 상위는 캠페인 페이지 캡처 아카이브(`03. CAMPAIGN NAME/02. MONITORING/ARCHIVE/backup(user_id)/*`, `10. Page Archive/4. CAMPAIGN NAME/backup(user_id)/*`, `251200-CAMPAIGN NAME/.../campaign_name_png/*`)와 `260610_shop_pf_pd_search_data` 의 추출 output CSV 였다.
