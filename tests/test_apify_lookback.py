# -*- coding: utf-8 -*-
"""(2026-10-13) Weekly 전용 Apify oldestPostDate 서버측 1차 필터 회귀.

지키는 원칙:
  · Weekly profile 3개 (teoldeurim weekly, myohanduk weekly, jjalduk weekly) 는
    APIFY_COLLECTION_LOOKBACK_DAYS = 8 을 가진다.
  · Standard (teoldeurim standard) 는 APIFY_COLLECTION_LOOKBACK_DAYS = None.
  · apify_collect() 호출 시 lookback_days=None 이면 run_input 에 oldestPostDate
    키 자체가 들어가지 않는다 (기존 수집 동작 완전 보존).
  · lookback_days=8 이면 run_input["oldestPostDate"] = (now_utc - 8d).date().
  · Weekly Hard filter 는 여전히 정확한 7일 조건 (UPLOADED_WITHIN_DAYS=7) 유지.
  · 조회수·길이 조건 변화 없음.
  · 다른 CFG 키 (ANALYSIS_MODEL, EXCLUDE_CHANNELS 등) 동작 변화 없음.

fixture 기반 (실 Apify API 호출 X).
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["benchmark", "benchmark_config", "targets",
          "targets.teoldeurim", "targets.myohanduk", "targets.jjalduk"]:
    if m in sys.modules:
        del sys.modules[m]

import benchmark
from targets import resolve_mode, get_target

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# 1) 각 target/mode profile 의 APIFY_COLLECTION_LOOKBACK_DAYS 값 검증
# ═══════════════════════════════════════════════════════════════════
cases = [
    ("teoldeurim", "weekly",   8),
    ("teoldeurim", "standard", None),
    ("myohanduk",  "weekly",   8),
    ("jjalduk",    "weekly",   8),
]
for slug, mode, expected in cases:
    cfg = resolve_mode(slug, mode)
    v = cfg.get("APIFY_COLLECTION_LOOKBACK_DAYS", "<MISSING>")
    chk(f"PROF[{slug}/{mode}]: APIFY_COLLECTION_LOOKBACK_DAYS={expected!r}",
        v == expected, f"got {v!r}")


# ═══════════════════════════════════════════════════════════════════
# 2) apify_collect() — lookback_days 전달 시 oldestPostDate 포함 여부
# ═══════════════════════════════════════════════════════════════════
# http_json 을 monkeypatch 해서 실 네트워크 호출 없이 run_input 만 캡쳐.
_captured_inputs = []
def _fake_http(url, data=None, method="GET", timeout=60):
    if method == "POST" and data is not None:
        _captured_inputs.append(data)
        # Fake run start 응답
        return {"data": {"id": "FAKE_RUN", "status": "SUCCEEDED",
                         "defaultDatasetId": "FAKE_DS"}}
    # 상태 폴링 응답
    if "actor-runs/" in url:
        return {"data": {"id": "FAKE_RUN", "status": "SUCCEEDED",
                         "defaultDatasetId": "FAKE_DS"}}
    # dataset items 응답
    return []

_orig_http = benchmark.http_json
benchmark.http_json = _fake_http
# CFG 에 ACTOR 세팅 (apify_collect 가 CFG.get 참조)
benchmark.CFG["APIFY_DISCOVERY_ACTOR"] = "streamers/youtube-shorts-scraper"


# (2a) lookback_days=None → oldestPostDate 미포함
_captured_inputs.clear()
benchmark.apify_collect("TOKEN", ["c1","c2"], 50, "POPULAR", lookback_days=None)
chk("CALL-01: lookback_days=None 일 때 oldestPostDate 키 없음 (Standard 동작)",
    "oldestPostDate" not in _captured_inputs[0],
    f"got keys {list(_captured_inputs[0].keys())}")
chk("CALL-02: 그 외 input 은 기존 그대로 (channels/maxResultsShorts/sortChannelShortsBy)",
    _captured_inputs[0] == {"channels": ["c1","c2"], "maxResultsShorts": 50,
                            "sortChannelShortsBy": "POPULAR"})


# (2b) lookback_days=8 → oldestPostDate = (now - 8d).date()
_captured_inputs.clear()
before = datetime.now(timezone.utc)
benchmark.apify_collect("TOKEN", ["c1"], 50, "NEWEST", lookback_days=8)
after = datetime.now(timezone.utc)
got = _captured_inputs[0].get("oldestPostDate")
chk("CALL-03: lookback_days=8 → oldestPostDate 키 포함",
    "oldestPostDate" in _captured_inputs[0])
# 날짜 검증 — (now - 8d) 날짜 값
expected_a = (before - timedelta(days=8)).strftime("%Y-%m-%d")
expected_b = (after - timedelta(days=8)).strftime("%Y-%m-%d")
chk("CALL-04: oldestPostDate = (now_utc - 8d).strftime('%Y-%m-%d')",
    got in (expected_a, expected_b),
    f"got {got!r} / expected {expected_a!r} or {expected_b!r}")
chk("CALL-05: 다른 input 값은 기존 그대로",
    _captured_inputs[0]["channels"] == ["c1"]
    and _captured_inputs[0]["maxResultsShorts"] == 50
    and _captured_inputs[0]["sortChannelShortsBy"] == "NEWEST")


# (2c) lookback_days 미지정 (default) → oldestPostDate 미포함 (후방호환)
_captured_inputs.clear()
benchmark.apify_collect("TOKEN", ["c1"], 50, "NEWEST")
chk("CALL-06: lookback_days 미지정 (default) → oldestPostDate 없음 (후방호환)",
    "oldestPostDate" not in _captured_inputs[0])

benchmark.http_json = _orig_http


# ═══════════════════════════════════════════════════════════════════
# 3) Hard filter 7일 조건 그대로 (조회수·길이·age 변화 없음)
# ═══════════════════════════════════════════════════════════════════
import benchmark_config
now = datetime.now(timezone.utc)
def _mk(dur=30, days_ago=1, views=100_000, seconds_extra=0):
    pub = now - timedelta(days=days_ago, seconds=seconds_extra)
    return {"video_id":"x","title":"t","channel_name":"ref","channel_id":"u",
            "views":views,"likes":0,"comments":0,"subscribers":1,
            "duration_sec":dur,"published_at":pub.isoformat(),
            "url":"https://youtu.be/x"}

def _run(slug, mode, v):
    cfg = resolve_mode(slug, mode)
    for k, val in benchmark_config.BENCHMARK_CONFIG.items():
        cfg.setdefault(k, val)
    benchmark.CFG.clear(); benchmark.CFG.update(cfg)
    passed, _ = benchmark.hard_filter([v], now)
    return len(passed) == 1

# Weekly 7일 경계 (변화 없음 확인)
chk("HF-WK-01: teoldeurim weekly exact 7d → PASS",
    _run("teoldeurim","weekly", _mk(days_ago=7)))
chk("HF-WK-02: teoldeurim weekly 7d + 1s → FAIL",
    not _run("teoldeurim","weekly", _mk(days_ago=7, seconds_extra=1)))
chk("HF-WK-03: myohanduk weekly 7d → PASS",
    _run("myohanduk","weekly", _mk(days_ago=7, views=200_000)))
chk("HF-WK-04: jjalduk weekly 7d + 1s → FAIL",
    not _run("jjalduk","weekly", _mk(days_ago=7, seconds_extra=1, views=200_000)))

# Weekly 조회수·길이 조건 그대로
chk("HF-VIEWS-01: teoldeurim weekly 49,999 → FAIL (min 50k)",
    not _run("teoldeurim","weekly", _mk(views=49_999)))
chk("HF-VIEWS-02: teoldeurim weekly 50,000 → PASS",
    _run("teoldeurim","weekly", _mk(views=50_000)))
chk("HF-VIEWS-03: teoldeurim weekly 3,000,001 → MAX_VIEWS_CUT (apply_max_views)",
    True)  # apply_max_views_filter 별도. hard_filter 는 상한 처리 안 함.
chk("HF-DUR-01: teoldeurim weekly 14초 → FAIL",
    not _run("teoldeurim","weekly", _mk(dur=14)))
chk("HF-DUR-02: teoldeurim weekly 15초 → PASS",
    _run("teoldeurim","weekly", _mk(dur=15)))
chk("HF-DUR-03: 짤덕방 weekly 181초 → PASS (max 없음)",
    _run("jjalduk","weekly", _mk(dur=181, views=200_000)))

# Standard 는 완전 무변경
chk("HF-STD-01: teoldeurim standard exact 30d → FAIL",
    not _run("teoldeurim","standard", _mk(days_ago=30, views=600_000)))
chk("HF-STD-02: teoldeurim standard 30d+1s → PASS",
    _run("teoldeurim","standard", _mk(days_ago=30, seconds_extra=1, views=600_000)))
chk("HF-STD-03: teoldeurim standard 365d → PASS",
    _run("teoldeurim","standard", _mk(days_ago=365, views=600_000)))


# ═══════════════════════════════════════════════════════════════════
# 4) Weekly 다른 필드 (조회수/길이/age) · Standard 전체 무변경
# ═══════════════════════════════════════════════════════════════════
for slug, mode in [("teoldeurim","weekly"),("teoldeurim","standard"),
                   ("myohanduk","weekly"),("jjalduk","weekly")]:
    cfg = resolve_mode(slug, mode)
    chk(f"UNCHANGED[{slug}/{mode}]: SORT_BY · MAX_SHORTS 유지",
        cfg["SORT_BY"] in ("NEWEST","POPULAR")
        and cfg["MAX_SHORTS_PER_CHANNEL"] == 50)
    chk(f"UNCHANGED[{slug}/{mode}]: MIN_VIEWS·MAX_VIEWS·MAX_VIEWS_INCLUSIVE 유지",
        cfg["MIN_VIEWS"] > 0 and cfg["MAX_VIEWS"] > 0
        and cfg.get("MAX_VIEWS_INCLUSIVE") is True)


# ═══════════════════════════════════════════════════════════════════
# 5) 기존 Actor / sent history / target namespace 유지 (sanity)
# ═══════════════════════════════════════════════════════════════════
import benchmark_config as bcfg
chk("ACTOR-01: APIFY_DISCOVERY_ACTOR 는 streamers/youtube-shorts-scraper 그대로",
    bcfg.BENCHMARK_CONFIG["APIFY_DISCOVERY_ACTOR"] == "streamers/youtube-shorts-scraper")
chk("ACTOR-02: APIFY_DETAIL_ACTOR 는 config 에 없음 (제거 상태 유지)",
    "APIFY_DETAIL_ACTOR" not in bcfg.BENCHMARK_CONFIG)
chk("TARGET-01: 세 target 모두 history_namespace 정상",
    get_target("teoldeurim")["history_namespace"] == "teoldeurim"
    and get_target("myohanduk")["history_namespace"] == "myohanduk"
    and get_target("jjalduk")["history_namespace"] == "jjalduk")


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" Weekly Apify oldestPostDate (lookback 8d) 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
