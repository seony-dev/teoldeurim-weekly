# -*- coding: utf-8 -*-
"""(2026-08-26) discovery-only 구조 회귀 테스트.

배경:
  streamers/youtube-shorts-scraper 자체가 응답에 title(원본) + translatedTitle(번역)
  을 모두 포함한다는 것을 실측(1,124건 · A/C actor output 필드 완전 동일)으로 확인.
  별도 detail actor(streamers/youtube-scraper) 2차 호출은 오히려 8건의 mojibake 를
  만들어냈고, Weekly Bundle Apify 실행 시간을 두 배로 늘렸다.
  → detail actor 및 관련 STEP 5.9 enrichment 로직 완전 제거.

지키는 원칙:
  · normalize_video() 가 discovery item 의 title 을 title_original 로,
    translatedTitle 을 title_translated 로 저장한다.
  · Claude 프롬프트·HTML 표시에 사용되는 v["title"] 은 항상 title_original.
  · translatedTitle 이 None 이어도 정상 동작.
  · production code 에서 APIFY_DETAIL_ACTOR / apify_enrich_titles /
    enrich_video_titles_in_place / STEP 5.9 참조가 완전히 사라졌는지 grep 확인.

실행:
  python tests/test_title_enrichment.py
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
# 1) discovery item → normalize_video 결과 검증 (known fixture 포함)
# ═══════════════════════════════════════════════════════════════════
# 사용자 명시 fixture: cZwgBQHn740 · 한국어 원본 + 영어 번역
_discovery_item = {
    "id": "cZwgBQHn740",
    "url": "https://www.youtube.com/shorts/cZwgBQHn740",
    "title": "콘서트 중에 무릎 꿇었던 설윤",
    "translatedTitle": "Sullyoon kneeling during the concert",
    "channelName": "짤덕방",
    "channelId": "UCcerVbAluh-1ifuEH6ZMqsw",
    "viewCount": 31401,
    "likes": 684,
    "commentsCount": 2,
    "duration": "00:00:16",
    "date": "2026-08-13T10:00:16.000Z",
    "numberOfSubscribers": 13300,
    "hashtags": ["엔믹스", "설윤", "kpop"],
    "thumbnailUrl": "https://i.ytimg.com/vi/cZwgBQHn740/maxresdefault.jpg",
    "text": "#엔믹스 #설윤 #shorts",
}

v = benchmark.normalize_video(_discovery_item)

chk("KNOWN-01: title_original = 한국어 원본",
    v["title_original"] == "콘서트 중에 무릎 꿇었던 설윤",
    f"got {v['title_original']!r}")
chk("KNOWN-02: title_translated = 영어 번역",
    v["title_translated"] == "Sullyoon kneeling during the concert",
    f"got {v['title_translated']!r}")
chk("KNOWN-03: title = title_original (Claude / HTML 원본 사용)",
    v["title"] == "콘서트 중에 무릎 꿇었던 설윤")
chk("KNOWN-04: title ≠ title_translated (번역본 미사용)",
    v["title"] != v["title_translated"])
chk("KNOWN-05: video_id 정확",
    v["video_id"] == "cZwgBQHn740")


# ═══════════════════════════════════════════════════════════════════
# 2) translatedTitle 이 None 이어도 정상 (유튜브가 번역 미제공)
# ═══════════════════════════════════════════════════════════════════
_no_translated = {
    "id": "abc123XYZ00",
    "title": "믹스틱 나눔해요!",
    "translatedTitle": None,   # 실측 케이스 재현
    "channelName": "엔믹스자리표",
    "duration": "00:00:21",
}
v2 = benchmark.normalize_video(_no_translated)
chk("NONE-01: translatedTitle=None → title_translated=None",
    v2["title_translated"] is None)
chk("NONE-02: title_original 는 그대로 원본",
    v2["title_original"] == "믹스틱 나눔해요!")
chk("NONE-03: title = title_original 유지",
    v2["title"] == "믹스틱 나눔해요!")


# ═══════════════════════════════════════════════════════════════════
# 3) translatedTitle 필드 자체가 없어도 정상 (스키마 방어)
# ═══════════════════════════════════════════════════════════════════
_no_field = {
    "id": "noFieldXYZ",
    "title": "번역 필드 자체가 없는 케이스",
    # translatedTitle 키 자체 없음
    "channelName": "테스트",
}
v3 = benchmark.normalize_video(_no_field)
chk("MISS-01: translatedTitle 키 없음 → title_translated=None",
    v3["title_translated"] is None)
chk("MISS-02: title_original 는 원본 그대로",
    v3["title_original"] == "번역 필드 자체가 없는 케이스")


# ═══════════════════════════════════════════════════════════════════
# 4) Claude analyze_video 는 v["title"] 을 프롬프트에 사용 → 원본 사용 확인
# ═══════════════════════════════════════════════════════════════════
# analyze_video 는 실제로 Anthropic 호출을 하지만 여기서는 프롬프트 조립 로직만 검증.
# 소스를 열어 'title' 참조가 v["title"] 임을 확인 (원본 사용 contract).
_src = Path("scripts/benchmark.py").read_text(encoding="utf-8")
chk("CLAUDE-01: analyze_video user prompt 에 v['title'] 사용",
    "제목: {v['title']}" in _src)
chk("CLAUDE-02: analyze_video 가 v['title_translated'] 를 프롬프트에 넣지 않음 (번역본 미사용)",
    "v['title_translated']" not in _src
    and "translated" not in _src.split("def analyze_video", 1)[1].split("def analyze_patterns", 1)[0])


# ═══════════════════════════════════════════════════════════════════
# 5) HTML 렌더도 v["title"] 사용 → 원본 표시 확인
# ═══════════════════════════════════════════════════════════════════
# render_report 계열 (_render_candidate, _render_si_row) 는 v["title"] 을 표시.
chk("HTML-01: _render_candidate 는 v['title'] 표시",
    "esc_html(v[\"title\"])" in _src or "esc_html(v['title'])" in _src)
chk("HTML-02: _render_si_row 도 v.get('title'...) 표시",
    "v.get(\"title\"" in _src or "v.get('title'" in _src)


# ═══════════════════════════════════════════════════════════════════
# 6) production code 에서 detail actor 참조 완전 제거 grep
# ═══════════════════════════════════════════════════════════════════
_bench_src = Path("scripts/benchmark.py").read_text(encoding="utf-8")
_config_src = Path("config/benchmark_config.py").read_text(encoding="utf-8")

def _has_code_ref(src, needle):
    """이력 코멘트 제외하고 실 코드 참조가 있는지 대략 판정.
    라인 단위로 훑되, `#` 로 시작하는 pure 코멘트 라인은 무시."""
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if needle in line:
            return line
    return None

for needle in ["APIFY_DETAIL_ACTOR", "apify_enrich_titles",
               "enrich_video_titles_in_place", "youtube-scraper"]:
    b = _has_code_ref(_bench_src, needle)
    c = _has_code_ref(_config_src, needle)
    chk(f"GREP-BENCH[{needle}]: production benchmark.py 에 실 참조 없음",
        b is None, f"line: {b!r}" if b else "")
    chk(f"GREP-CONF[{needle}]: production config/benchmark_config.py 에 실 참조 없음",
        c is None, f"line: {c!r}" if c else "")

# 함수 자체 삭제 확인 (module attr 접근 시 AttributeError)
chk("REMOVE-01: apify_enrich_titles 함수 제거됨",
    not hasattr(benchmark, "apify_enrich_titles"))
chk("REMOVE-02: enrich_video_titles_in_place 함수 제거됨",
    not hasattr(benchmark, "enrich_video_titles_in_place"))


# ═══════════════════════════════════════════════════════════════════
# 7) apify_collect 는 discovery actor 만 호출 → Weekly Bundle 총 3 runs 예상
# ═══════════════════════════════════════════════════════════════════
_ac_src = _bench_src.split("def apify_collect", 1)[1].split("def normalize_video", 1)[0]
chk("SINGLE-01: apify_collect 는 APIFY_DISCOVERY_ACTOR 만 참조",
    "APIFY_DISCOVERY_ACTOR" in _ac_src)
chk("SINGLE-02: apify_collect 는 APIFY_DETAIL_ACTOR 참조 없음",
    "APIFY_DETAIL_ACTOR" not in _ac_src)

# Weekly Bundle 예상 흐름 재확인 (send_report.run_weekly_bundle 은 3 target 순차)
_sr = Path("scripts/send_report.py").read_text(encoding="utf-8")
_targets = _sr.split("_WEEKLY_BUNDLE_TARGETS = [", 1)[1].split("]", 1)[0]
for slug in ["teoldeurim", "myohanduk", "jjalduk"]:
    chk(f"BUNDLE[{slug}]: Weekly Bundle 3 target 에 포함",
        f'"{slug}"' in _targets)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" discovery-only + normalize_video 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
