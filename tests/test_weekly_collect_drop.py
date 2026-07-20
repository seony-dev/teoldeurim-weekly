# -*- coding: utf-8 -*-
"""회귀 — weekly.collect() drop 집계 KeyError 재발 방지 (2026-07-17 production 장애).

배경:
  기존 hard_filter_reason() 단독 테스트만 있어서 collect() 내부의
    drop[dropkey] += 1
  경로가 새 reason 코드 추가 시 KeyError 를 던지는 걸 잡지 못했다.
  이번 스크립트는 collect() 를 mock 으로 실제 실행해:
    fake YouTube details → parse_item → hard_filter_reason → drop 집계 → 반환
  전체 흐름을 재현하고 모든 reason 코드가 안전하게 집계되는지 확인한다.

실행:
  프로젝트 루트에서:  python tests/test_weekly_collect_drop.py
  종료 코드: 성공 0 / 실패 1
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

if "weekly" in sys.modules: del sys.modules["weekly"]
import weekly

checks = []
def chk(name, actual, expected=True):
    ok = (actual == expected)
    checks.append((name, ok, actual, expected))


def mk_video(vid, dur_iso, views, title="테스트 영상", channel="일반채널",
             channel_id="UC_normal", pub_iso=None, lang="ko"):
    """YouTube API videos.list 반환 형식의 fake item."""
    if pub_iso is None:
        # 기본: standard 범위 (100일 전 = 30일 초과 & 365일 이내)
        pub_iso = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": vid,
        "snippet": {
            "title": title, "channelTitle": channel, "channelId": channel_id,
            "publishedAt": pub_iso, "defaultAudioLanguage": lang,
        },
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": dur_iso},
    }


def _run_collect(profile, fake_details):
    """mock 을 통해 collect() 를 실제 실행하고 결과 반환."""
    # 필요한 함수만 monkeypatch — collect() 로직 자체는 그대로.
    orig_search = weekly.search_videos
    orig_fetch = weekly.fetch_video_details
    orig_sleep = weekly.time.sleep

    # search_videos: 각 검색어당 fake_details 의 video_id 를 모두 반환 (query 어트리뷰션)
    fake_ids = [d["id"] for d in fake_details]
    def mock_search(api_key, query, pa, pb):
        return [(vid, query) for vid in fake_ids]

    def mock_fetch(api_key, ids):
        # 요청된 id 들만 필터해서 반환
        wanted = set(ids)
        return [d for d in fake_details if d["id"] in wanted]

    weekly.search_videos = mock_search
    weekly.fetch_video_details = mock_fetch
    weekly.time.sleep = lambda *a, **kw: None   # no-op

    try:
        weekly._apply_weekly_profile(profile)
        # collect(api_key, days_oldest, days_newest, seen_meta)
        result = weekly.collect(
            api_key="MOCK",
            days_oldest=weekly.CONFIG["LOOKBACK_DAYS_OLDEST"],
            days_newest=weekly.CONFIG["LOOKBACK_DAYS_NEWEST_PRIMARY"],
            seen_meta={},
        )
        return result
    finally:
        weekly.search_videos = orig_search
        weekly.fetch_video_details = orig_fetch
        weekly.time.sleep = orig_sleep


# ═══════════════════════════════════════════════════════════════════
# 1. duration_min (14초) — 이번 production 실패 원인
# ═══════════════════════════════════════════════════════════════════
print("=" * 78)
print(" 1. duration_min (14초) — production 실패 재현 시나리오")
print("=" * 78)

# ★ 이 케이스가 이번 금요일 KeyError 정확히 재현. Counter 로 fix됐는지 검증.
raised = None
try:
    r = _run_collect("standard", [
        mk_video("V14", "PT14S", 1_000_000, title="14초 테스트"),
    ])
    chk("1-1: 14초 영상 collect() 완료 (KeyError 없음)", True)
    chk("1-2: candidates 0개", len(r["candidates"]) == 0)
    chk("1-3: hard_excluded 1개", len(r["hard_excluded"]) == 1)
    chk("1-4: exclusion_reason 에 '15초 미만' 포함",
        "15초 미만" in r["hard_excluded"][0].get("exclusion_reason", ""))
except KeyError as e:
    raised = e
    chk(f"1-1: 14초 영상 collect() KeyError 재발 ({e})", False)
except Exception as e:
    raised = e
    chk(f"1-1: 예상 못 한 예외 ({type(e).__name__}: {e})", False)


# ═══════════════════════════════════════════════════════════════════
# 2. 모든 reason code 를 한 번에 (혼합 케이스)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 2. 모든 reason code 혼합 (다중 컷 사유 동시 발생)")
print("=" * 78)

# standard profile 로 실행 (age_min=30, age_max=365)
now = datetime.now(timezone.utc)
def iso(days_ago):
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

fake_mix = [
    # 통과 케이스 (조회수 1M, 15~180초, 30~365일 이내)
    mk_video("PASS1", "PT30S",   1_000_000, title="통과 후보 1"),
    mk_video("PASS2", "PT60S",   2_000_000, title="통과 후보 2"),
    # 각 reason code 하나씩
    mk_video("V_MINVIEWS",    "PT30S", 100_000, title="조회수 미달"),
    mk_video("V_MAXVIEWS",    "PT30S", 100_000_000, title="조회수 초과"),
    mk_video("V_DUR_PARSE",   "",              1_000_000, title="파싱 실패"),
    mk_video("V_DUR_MIN",     "PT10S",         1_000_000, title="10초 초단타"),
    mk_video("V_DUR_MAX",     "PT4M",          1_000_000, title="240초 초과"),
    mk_video("V_KOREAN",      "PT30S",         1_000_000, title="English only title"),
    mk_video("V_CHANNEL",     "PT30S",         1_000_000, title="채널 차단 테스트",
             channel="털어드림", channel_id="UCfrO3ZMC-rOThB-NSxfGjTQ"),
    mk_video("V_KEYWORD",     "PT30S",         1_000_000, title="직캠 테스트"),
    mk_video("V_AGE_MIN",     "PT30S",         1_000_000, title="너무 최근",
             pub_iso=iso(5)),
    mk_video("V_AGE_MAX",     "PT30S",         1_000_000, title="너무 오래됨",
             pub_iso=iso(400)),
    mk_video("V_AGE_PARSE",   "PT30S",         1_000_000, title="published_at 이상",
             pub_iso="INVALID_TIMESTAMP"),
]

try:
    r = _run_collect("standard", fake_mix)
    chk("2-1: 다중 컷 사유 collect() 완료 (KeyError 없음)", True)
    # 통과 2개
    passed_ids = {c["video_id"] for c in r["candidates"]}
    chk("2-2: PASS1, PASS2 통과", passed_ids == {"PASS1", "PASS2"})
    chk("2-3: 나머지 11개 컷", len(r["hard_excluded"]) == 11)

    # 각 사유별 실제 exclusion_reason 매핑 확인
    reason_map = {c["video_id"]: c.get("exclusion_reason", "") for c in r["hard_excluded"]}
    def in_reason(vid, kw):
        return kw in reason_map.get(vid, "")
    chk("2-4: V_MINVIEWS → '조회수' & '미만'",
        in_reason("V_MINVIEWS", "미만"))
    chk("2-5: V_MAXVIEWS → '조회수' & '이상'",
        in_reason("V_MAXVIEWS", "이상"))
    chk("2-6: V_DUR_PARSE → '길이 파싱 실패'",
        in_reason("V_DUR_PARSE", "파싱 실패"))
    chk("2-7: V_DUR_MIN → '15초 미만'",
        in_reason("V_DUR_MIN", "15초 미만"))
    chk("2-8: V_DUR_MAX → 'Shorts 길이 초과'",
        in_reason("V_DUR_MAX", "Shorts 길이 초과"))
    chk("2-9: V_KOREAN → '한국어 제목 아님'",
        in_reason("V_KOREAN", "한국어 제목"))
    chk("2-10: V_CHANNEL → '채널 차단'",
        in_reason("V_CHANNEL", "채널 차단"))
    chk("2-11: V_KEYWORD → '키워드 차단'",
        in_reason("V_KEYWORD", "키워드 차단"))
    chk("2-12: V_AGE_MIN → '30일 이내'",
        in_reason("V_AGE_MIN", "30일 이내"))
    chk("2-13: V_AGE_MAX → '365일 초과'",
        in_reason("V_AGE_MAX", "365일 초과"))
    chk("2-14: V_AGE_PARSE → 'age_parse'",
        in_reason("V_AGE_PARSE", "age_parse"))
except KeyError as e:
    chk(f"2-1: 다중 컷 → KeyError 재발 ({e})", False)
except Exception as e:
    chk(f"2-1: 예상 못 한 예외 ({type(e).__name__}: {e})", False)


# ═══════════════════════════════════════════════════════════════════
# 3. duration 경계 (15/180초 통과, 14/181초 컷)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 3. duration 경계 정확성 (collect 경로)")
print("=" * 78)

try:
    r = _run_collect("standard", [
        mk_video("D14",  "PT14S",  1_000_000, title="14초"),
        mk_video("D15",  "PT15S",  1_000_000, title="15초"),
        mk_video("D180", "PT3M",   1_000_000, title="180초"),
        mk_video("D181", "PT3M1S", 1_000_000, title="181초"),
    ])
    passed = {c["video_id"] for c in r["candidates"]}
    cut = {c["video_id"] for c in r["hard_excluded"]}
    chk("3-1: 15/180초 통과", passed == {"D15", "D180"})
    chk("3-2: 14/181초 컷", cut == {"D14", "D181"})
except Exception as e:
    chk(f"3-1: 예상 못 한 예외 ({type(e).__name__}: {e})", False)


# ═══════════════════════════════════════════════════════════════════
# 4. recent profile 로도 동일 시나리오 (age_min=None)
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 4. recent profile — duration_min/parse 안전 집계")
print("=" * 78)
try:
    r = _run_collect("recent", [
        mk_video("RD14",  "PT14S",  500_000, title="14초 recent",   pub_iso=iso(10)),
        mk_video("RD15",  "PT15S",  500_000, title="15초 recent",   pub_iso=iso(10)),
        mk_video("RD180", "PT3M",   500_000, title="180초 recent",  pub_iso=iso(10)),
        mk_video("RDP",   "",       500_000, title="parse 실패",    pub_iso=iso(10)),
    ])
    passed = {c["video_id"] for c in r["candidates"]}
    cut = {c["video_id"] for c in r["hard_excluded"]}
    chk("4-1: recent 15/180초 통과", passed == {"RD15", "RD180"})
    chk("4-2: recent 14초 + parse 실패 컷", cut == {"RD14", "RDP"})
except KeyError as e:
    chk(f"4-1: recent 에서 KeyError ({e})", False)
except Exception as e:
    chk(f"4-1: 예상 못 한 예외 ({type(e).__name__}: {e})", False)


# ═══════════════════════════════════════════════════════════════════
# 5. Counter 동작 확인 — 없는 키 조회 시 0 반환
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 5. Counter 안전성")
print("=" * 78)
from collections import Counter as _C
c = _C()
c["existing_key"] += 1
chk("5-1: Counter 존재 안 하는 키 조회 → 0", c["nonexistent"] == 0)
chk("5-2: Counter += 1 안전", c["another_new"] + 1 == 1)


# ═══════════════════════════════════════════════════════════════════
# 6. weekly.py 실제 코드에서 drop 초기화 방식 확인
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 6. 소스 검증")
print("=" * 78)
src = (ROOT / "scripts" / "weekly.py").read_text(encoding="utf-8")
chk("6-1: 'drop = Counter()' 로 초기화됨", "drop = Counter()" in src)
chk("6-2: 'from collections import Counter' 임포트",
    "from collections import Counter" in src)
chk("6-3: 기존 하드코드 dict 초기화 사라짐",
    'drop = {"min_views":' not in src)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" 결과")
print("=" * 78)
p = f = 0
for name, ok, actual, expected in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}")
    if ok: p += 1
    else:
        f += 1
        print(f"     expected={expected}, actual={actual}")
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
