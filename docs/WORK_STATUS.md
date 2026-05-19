# 작업 현황 (WORK_STATUS)

> 최종 갱신: 2026-05-19 · 이 문서는 작업이 진행될 때마다 갱신합니다.

---

## 한 줄 요약

주간 메일러(`weekly.py`)는 운영 중이고, 신규 보조 모듈인 타 채널 벤치마크(`benchmark.py`)는
구현 완료·테스트 단계로 아직 git 커밋 및 전용 워크플로 작성 전 상태입니다.

---

## 완료된 작업

### 주간 메일러 (weekly.py) — 운영 중
- YouTube Data API v3 기반 K-pop Shorts 후보 수집 (22개 검색어)
- Hard 필터(조회수·길이·언어·업로드 기간·채널/키워드 블록·history dedup)
- Claude(Opus 4.7) 기반 Soft 필터 — 후보별 AI 분석 + 채택/탈락 판정
- 6~12개월 풀 + 3개월 fallback 수집 전략
- 인터랙티브 HTML 리포트 (단계별 탭·검색·접이식 필터 기준)
- 이메일 발송 (본문 표 + 첨부 HTML) / `history/` 자동 아카이브
- 실행 모드: 일반 / `DRY_RUN` / `FORCE_RESCAN` / `RESEND_LATEST` / `RESUME`(자동)
- GitHub Actions: `weekly.yml`(주간 cron), `resend.yml`(수동 재발송, 본인만)

### 타 채널 벤치마크 (benchmark.py) — 구현 완료, 테스트 단계
- `config/benchmark_config.py` — 벤치마크 전용 설정 (weekly.py와 독립)
- Apify(`streamers/youtube-shorts-scraper`)로 외부 채널 인기 Shorts 수집
- 참고 채널 = 수동 지정(`REFERENCE_CHANNELS`) + `history/`에서 자동 추출
  (`auto_discovered_reference_candidates`)
- 제외 채널 검증 3중: 수동 채널 검증(중단) / 자동 추출 재검증(드롭) / 병합 최종 검증
- `channel_id` 주 기준 + `channel_name` 보조 기준 검증, 중복 channel_id 제거
- 이미 발송한 영상(history video_id)은 후보 리스트에서 제외
- 지표 7종 계산(조회수·좋아요율·댓글률·반응률·구독자대비·길이·업로드일)
- Claude 2단계 분석: 영상별 적합도(fit_score) + 종합 기획 포인트
- HTML 리포트 (Section 1 참고 후보 / Section 2 기획 포인트)
- 1차 테스트 실행 성공 — `benchmark/2026-05-19_*` 산출물 생성 확인

### 문서화 (2026-05-19)
- `CLAUDE.md`, `README.md`(보강), `.env.example`, `docs/` 4종 작성

---

## 진행 중인 작업

- 벤치마크 모듈 **테스트 검증** — 1차 테스트 실행은 완료. 결과 품질 확인 중.

---

## 남은 작업

1. **benchmark.py + config/ git 커밋** — 현재 untracked 상태.
2. **`config/benchmark_config.py` 운영값 원복** — 현재 `[테스트값]`이 적용돼 있음
   (`MAX_SHORTS_PER_CHANNEL`, `MAX_TOTAL_RAW`, `MIN_VIEWS`, `MAX_ANALYSIS_CANDIDATES`,
   `FINAL_CANDIDATES`). 운영 전 주석에 적힌 권장값으로 되돌릴 것.
3. **`REFERENCE_CHANNELS` 수동 채널 등록 여부 결정** — 현재 비어 있음(자동 추출만 사용 중).
4. **`benchmark.yml` GitHub Actions 워크플로 작성** — 벤치마크 자동/수동 실행용. 미작성.
5. **`APIFY_TOKEN` GitHub 시크릿 등록** — `[확인 필요]`.
6. 벤치마크 실행 주기 확정 (월 1~2회 권장 — weekly와 다른 주기).

---

## 최근 변경사항

- 2026-05-19 — 벤치마크 모듈에 history 자동 채널 추출 + 기발송 영상 제외 기능 추가
- 2026-05-19 — 벤치마크 검증 로직을 `channel_id` 주 기준으로 재구성, 수동/자동 분리 처리
- 2026-05-19 — 첫 테스트용으로 `benchmark_config.py` 값 임시 하향
- 2026-05-19 — 인수인계 문서 세트 작성

---

## 다음 작업 추천 순서

1. 벤치마크 1차 테스트 결과(`benchmark/2026-05-19_report.html`) 품질 검토
2. 만족하면 `benchmark_config.py`를 운영값으로 원복
3. `REFERENCE_CHANNELS` 수동 채널 등록 여부 결정
4. `benchmark.yml` 워크플로 작성 + `APIFY_TOKEN` 시크릿 등록
5. `benchmark.py` + `config/` git 커밋
6. 운영 주기 확정 후 정식 운영 시작

---

## 확인 필요한 이슈

- `[확인 필요]` `APIFY_TOKEN`이 GitHub 레포 시크릿에 등록돼 있는지 (벤치마크 자동화 전제 조건)
- `[확인 필요]` `benchmark/` 산출물(`filtered_raw.json`)을 git에 커밋할지 여부
  — 현재 `.gitignore`에서 `*.html`은 제외되나 `*.json`은 제외 안 됨 → 결정 필요
- `[확인 필요]` 벤치마크 정식 운영 주기 (월 1회 / 격주 / 수동만)
- `[확인 필요]` Claude API 실제 월 청구액 — Anthropic 콘솔에서 확인

---

## 비개발자용 쉬운 요약

- **주간 봇**: 매주 금요일, 유튜브에서 K-pop 쇼츠 후보를 자동으로 모아 AI가 골라
  메일로 보내주는 시스템. 정상 작동 중.
- **벤치마크 봇**: 잘나가는 다른 채널들의 인기 쇼츠를 모아서 "우리가 써먹을 만한 후보"와
  "기획 아이디어"를 정리해주는 새 도구. 만들어서 한 번 테스트해본 단계.
- 지금 남은 일: 벤치마크 봇을 정식으로 돌릴 수 있게 마무리 설정하는 것.
