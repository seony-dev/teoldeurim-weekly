# -*- coding: utf-8 -*-
"""(2026-10-16) Apify malformed item (DATE_FILTER_TOO_STRICT 등) 사전 제거 회귀.

배경:
  `oldestPostDate` 서버측 필터 사용 시 Actor 가 "현재 8일 수집 범위 내 신규 Shorts 0"
  인 채널에 대해 아래 형태의 '에러 메타 레코드' 를 dataset 에 삽입하는 것이 실측으로
  확인됨.
    {
      "aboutChannelInfo": {...},
      "url": "https://www.youtube.com/@{channel}/shorts",
      "error": "DATE_FILTER_TOO_STRICT",
      "input": "https://www.youtube.com/@{channel}",
      "note": "No videos were collected due to date filtering."
    }
  이 레코드가 normalize_video() 를 거치면 전 필드 default 로 채워져 "(제목 없음)"
  카드로 collected 탭에 노출되는 리포트 품질 저하 발생.

지키는 원칙:
  · id/videoId/shortId 중 하나도 **non-empty 값**이 아니면 malformed 로 판정.
    (키 존재 여부가 아니라 실제 값 존재 여부로 판정 — None / "" 는 invalid).
  · title 유무로는 판정하지 않음.
  · normalize 이전에 drop. collected / hard_excluded count 에 포함되지 않음.
  · 걸러진 레코드는 log 에 error reason + 대상 채널 표기.
  · 정상 영상 (id 있음) · 엣지 (videoId 만 있음 · shortId 만 있음) 은 전부 보존.
  · Claude 분석 / direct / benchmark 결과에는 영향 없음 (어차피 Hard 컷 되던 레코드).

fixture 기반 · 실 Apify API 호출 X.
"""
import sys
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["benchmark", "benchmark_config", "targets", "targets.teoldeurim"]:
    if m in sys.modules:
        del sys.modules[m]

import benchmark

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# Fixtures (실측 Apify dataset 샘플 그대로)
# ═══════════════════════════════════════════════════════════════════
_META_DATE_FILTER = {
    "aboutChannelInfo": {
        "channelName": "K팝과몰입러",
        "inputChannelUrl": "https://www.youtube.com/@K팝과몰입러",
        "channelId": "UCLILKYP0GU5S5JrGFryBK_A",
    },
    "url": "https://www.youtube.com/@K%ED%8C%9D%EA%B3%BC%EB%AA%B0%EC%9E%85%EB%9F%AC/shorts",
    "error": "DATE_FILTER_TOO_STRICT",
    "input": "https://www.youtube.com/@K팝과몰입러",
    "note": "No videos were collected due to date filtering.",
}

_META_MYOHANDUK_CASE = {
    "aboutChannelInfo": {
        "channelName": "아이돌모음zip",
        "inputChannelUrl": "https://www.youtube.com/@idolclip_zip",
    },
    "url": "https://www.youtube.com/@idolclip_zip/shorts",
    "error": "DATE_FILTER_TOO_STRICT",
    "input": "https://www.youtube.com/@idolclip_zip",
}

_VALID_SHORTS = {
    "id": "cZwgBQHn740",
    "title": "콘서트 중에 무릎 꿇었던 설윤",
    "translatedTitle": "Sullyoon kneeling during the concert",
    "channelName": "짤덕방",
    "channelId": "UCcerVbAluh-1ifuEH6ZMqsw",
    "viewCount": 31401,
    "likes": 684,
    "duration": "00:00:16",
    "date": "2026-08-13T10:00:16.000Z",
    "url": "https://www.youtube.com/shorts/cZwgBQHn740",
}

_VALID_VIDEOID_ONLY = {
    "videoId": "abc123XYZab",
    "title": "fallback key test",
    "viewCount": 100_000,
    "duration": "00:00:30",
    "date": "2026-10-10T00:00:00Z",
    "url": "https://www.youtube.com/shorts/abc123XYZab",
}

_VALID_SHORTID_ONLY = {
    "shortId": "xyz789ABCD",
    "title": "shortId fallback",
    "channelName": "test",
}

_VALID_TITLE_MISSING = {
    # title 이 비어있지만 id 가 있으므로 유효 — title 유무로 판정하지 않음을 확인
    "id": "validNoTitle",
    "title": "",
    "channelName": "test",
    "viewCount": 50_000,
    "duration": "00:00:25",
    "date": "2026-10-12T00:00:00Z",
    "url": "https://www.youtube.com/shorts/validNoTitle",
}

_EMPTY = {}
_ID_EMPTY_STRING = {"id": "", "videoId": None, "shortId": ""}
_UNRELATED_META = {"aboutChannelInfo": {}, "note": "random meta"}  # id 류 전부 없음

# non-empty 판정 추가 edge case
_ID_NONE_VIDEOID_VALID = {"id": None, "videoId": "fallback01X"}  # id None 이지만 videoId valid → True
_ID_EMPTY_SHORTID_VALID = {"id": "", "shortId": "sidABC12345"}   # id "" 지만 shortId valid → True
_ALL_FALSY_MIXED = {"id": None, "videoId": "", "shortId": None}  # 전부 falsy → False


# ═══════════════════════════════════════════════════════════════════
# 1) _is_valid_video_item 판정 로직
# ═══════════════════════════════════════════════════════════════════
chk("VALID-01: 정상 Shorts (id 있음) → True",
    benchmark._is_valid_video_item(_VALID_SHORTS))
chk("VALID-02: videoId fallback → True",
    benchmark._is_valid_video_item(_VALID_VIDEOID_ONLY))
chk("VALID-03: shortId fallback → True",
    benchmark._is_valid_video_item(_VALID_SHORTID_ONLY))
chk("VALID-04: title 비어있어도 id 있으면 True (title 유무로 판정하지 않음)",
    benchmark._is_valid_video_item(_VALID_TITLE_MISSING))

chk("INVALID-01: DATE_FILTER_TOO_STRICT 메타 레코드 → False",
    not benchmark._is_valid_video_item(_META_DATE_FILTER))
chk("INVALID-02: myohanduk case 도 동일 → False",
    not benchmark._is_valid_video_item(_META_MYOHANDUK_CASE))
chk("INVALID-03: 완전 빈 dict → False",
    not benchmark._is_valid_video_item(_EMPTY))
chk("INVALID-04: id/videoId/shortId 모두 빈 문자열 또는 None → False (non-empty 값 아님)",
    not benchmark._is_valid_video_item(_ID_EMPTY_STRING))
chk("INVALID-05: 알 수 없는 메타 (error 필드도 없음) 라도 id 없으면 → False",
    not benchmark._is_valid_video_item(_UNRELATED_META))
chk("INVALID-06: id/videoId/shortId 전부 falsy mixed (None·'') → False",
    not benchmark._is_valid_video_item(_ALL_FALSY_MIXED))
chk("VALID-05: id=None 이어도 videoId 가 non-empty 값이면 → True (fallback 유효)",
    benchmark._is_valid_video_item(_ID_NONE_VIDEOID_VALID))
chk("VALID-06: id='' 이어도 shortId 가 non-empty 값이면 → True (fallback 유효)",
    benchmark._is_valid_video_item(_ID_EMPTY_SHORTID_VALID))


# ═══════════════════════════════════════════════════════════════════
# 2) drop_malformed_items — 분리 결과 검증
# ═══════════════════════════════════════════════════════════════════
mixed = [_VALID_SHORTS, _META_DATE_FILTER, _VALID_VIDEOID_ONLY,
         _META_MYOHANDUK_CASE, _VALID_SHORTID_ONLY, _VALID_TITLE_MISSING,
         _EMPTY, _UNRELATED_META]
clean, dropped = benchmark.drop_malformed_items(mixed)
chk("DROP-01: clean 길이 = 4 (정상 4개)",
    len(clean) == 4, f"got {len(clean)}")
chk("DROP-02: dropped 길이 = 4 (malformed 4개)",
    len(dropped) == 4, f"got {len(dropped)}")
chk("DROP-03: clean 안에 정상 fixture 전부 포함",
    _VALID_SHORTS in clean and _VALID_VIDEOID_ONLY in clean
    and _VALID_SHORTID_ONLY in clean and _VALID_TITLE_MISSING in clean)
chk("DROP-04: dropped 안에 정상 fixture 없음 (유효 영상 잘못 제거 X)",
    _VALID_SHORTS not in dropped and _VALID_VIDEOID_ONLY not in dropped
    and _VALID_SHORTID_ONLY not in dropped and _VALID_TITLE_MISSING not in dropped)
chk("DROP-05: 입력 순서 보존 (clean)",
    [id(x) for x in clean] == [id(_VALID_SHORTS), id(_VALID_VIDEOID_ONLY),
                                id(_VALID_SHORTID_ONLY), id(_VALID_TITLE_MISSING)])

# empty 리스트 안전성
clean_empty, dropped_empty = benchmark.drop_malformed_items([])
chk("DROP-06: 빈 리스트 → clean/dropped 모두 빈 리스트",
    clean_empty == [] and dropped_empty == [])

# 전부 valid 인 경우
only_valid = [_VALID_SHORTS, _VALID_VIDEOID_ONLY]
cv, dv = benchmark.drop_malformed_items(only_valid)
chk("DROP-07: 전부 valid → clean 2 / dropped 0",
    len(cv) == 2 and len(dv) == 0)

# 전부 malformed 인 경우
only_bad = [_META_DATE_FILTER, _META_MYOHANDUK_CASE]
cb, db = benchmark.drop_malformed_items(only_bad)
chk("DROP-08: 전부 malformed → clean 0 / dropped 2",
    len(cb) == 0 and len(db) == 2)


# ═══════════════════════════════════════════════════════════════════
# 3) _describe_dropped_item 로그 포맷 (error reason + 채널)
# ═══════════════════════════════════════════════════════════════════
desc = benchmark._describe_dropped_item(_META_DATE_FILTER)
chk("LOG-01: error reason 포함",
    "DATE_FILTER_TOO_STRICT" in desc, f"got {desc!r}")
chk("LOG-02: 채널명 (aboutChannelInfo.channelName) 포함",
    "K팝과몰입러" in desc, f"got {desc!r}")

# aboutChannelInfo 없이 input 만 있는 경우 fallback
_only_input = {"error": "SOME_ERROR", "input": "https://www.youtube.com/@fallback"}
desc2 = benchmark._describe_dropped_item(_only_input)
chk("LOG-03: aboutChannelInfo 없을 때 input URL fallback",
    "SOME_ERROR" in desc2 and "fallback" in desc2, f"got {desc2!r}")

# error 필드 자체가 없는 경우 '(error 필드 없음)' 표시
_no_error = {"url": "https://www.youtube.com/@noerr"}
desc3 = benchmark._describe_dropped_item(_no_error)
chk("LOG-04: error 필드 없으면 '(error 필드 없음)' 표기",
    "(error 필드 없음)" in desc3, f"got {desc3!r}")


# ═══════════════════════════════════════════════════════════════════
# 4) normalize_video / apify_collect 자체 시그니처 · 로직 무변경
# ═══════════════════════════════════════════════════════════════════
import inspect
sig_norm = inspect.signature(benchmark.normalize_video)
chk("UNCHANGED-01: normalize_video(item) 시그니처 그대로",
    list(sig_norm.parameters.keys()) == ["item"])

# normalize_video 자체는 malformed 를 받아도 'default 로 채운 dict' 를 반환하는
# 기존 동작 그대로. 이번 변경은 호출 전 drop 뿐.
norm = benchmark.normalize_video(_META_DATE_FILTER)
chk("UNCHANGED-02: normalize_video 자체는 malformed 입력도 종전처럼 처리 (기존 behavior 보존)",
    norm["title"] == "(제목 없음)" and norm["video_id"] == "")

sig_collect = inspect.signature(benchmark.apify_collect)
chk("UNCHANGED-03: apify_collect 시그니처 (token, channels, max_per_channel, sort_by, lookback_days=None) 유지",
    list(sig_collect.parameters.keys()) == ["token","channels","max_per_channel","sort_by","lookback_days"])


# ═══════════════════════════════════════════════════════════════════
# 5) hard_filter / profile / 8일 lookback / 비용 관련 로직 무변경 (grep)
# ═══════════════════════════════════════════════════════════════════
_src = Path("scripts/benchmark.py").read_text(encoding="utf-8")
chk("UNCHANGED-04: hard_filter() 함수 존재 (7일 조건 그대로)",
    "def hard_filter(videos, now):" in _src)
chk("UNCHANGED-05: UPLOADED_WITHIN_DAYS 참조 유지",
    "UPLOADED_WITHIN_DAYS" in _src)
chk("UNCHANGED-06: APIFY_COLLECTION_LOOKBACK_DAYS 참조 유지 (lookback 로직 그대로)",
    "APIFY_COLLECTION_LOOKBACK_DAYS" in _src)
chk("UNCHANGED-07: Apify Actor ID 참조 유지",
    "APIFY_DISCOVERY_ACTOR" in _src)

# 비용 관련 로직/문구는 건드리지 않음
import benchmark_config as bcfg
chk("UNCHANGED-08: APIFY_DISCOVERY_ACTOR config 그대로",
    bcfg.BENCHMARK_CONFIG["APIFY_DISCOVERY_ACTOR"] == "streamers/youtube-shorts-scraper")


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" Apify malformed item (DATE_FILTER_TOO_STRICT 등) 사전 제거 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
