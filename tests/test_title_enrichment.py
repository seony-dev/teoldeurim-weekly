# -*- coding: utf-8 -*-
"""원본 title enrichment fixture 회귀.

지키는 원칙:
  · discovery actor 의 title(번역본일 수 있음)을 그대로 신뢰하지 않는다.
  · streamers/youtube-scraper 로부터
      · title             = 원본 (예: '콘서트 중에 무릎 꿇었던 설윤')
      · translatedTitle   = 번역 (예: 'Sullyoon kneeling during the concert')
    를 받아 title_original / title_translated 로 분리 저장.
  · Claude input · HTML 표시에는 반드시 title_original 사용.
  · enrichment 실패 시 fallback 은 조용히 처리하지 않고 warning + fallback 플래그.

fixture 기반이라 실제 Apify 호출 없이 검증.

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
# monkeypatch: apify_enrich_titles 를 fixture 응답으로 대체
# ═══════════════════════════════════════════════════════════════════
_FIXTURE = {
    "cZwgBQHn740": {
        "title_original":  "콘서트 중에 무릎 꿇었던 설윤",
        "title_translated": "Sullyoon kneeling during the concert",
    },
    "vAAABBBBCCC": {
        "title_original":  "잘 알려진 반전형 짤",
        "title_translated": "Well-known plot-twist meme",
    },
    # 아래 영상은 enricher 가 응답하지 않음 → fallback path
    # "vFAILfallback" 는 _FIXTURE 에 없음
}


def _fake_enrich(_token, urls):
    """실제 API 호출 없이 fixture 로 응답. url → video_id (fixture key 포함 여부로)."""
    out = {}
    for u in urls:
        for vid, rec in _FIXTURE.items():
            if vid in u:
                out[vid] = dict(rec)
                break
    return out


benchmark.apify_enrich_titles = _fake_enrich


# ═══════════════════════════════════════════════════════════════════
# fixture videos — 하나는 성공 enrichment, 하나는 API 응답 없음(=fallback)
# ═══════════════════════════════════════════════════════════════════
videos = [
    {
        "video_id": "cZwgBQHn740",
        "title": "Sullyoon kneeling during the concert",  # discovery = 번역본 (신뢰 X)
        "url": "https://www.youtube.com/shorts/cZwgBQHn740",
    },
    {
        "video_id": "vAAABBBBCCC",
        "title": "Well-known plot-twist meme",           # discovery = 번역본
        "url": "https://www.youtube.com/shorts/vAAABBBBCCC",
    },
    {
        "video_id": "vFAILfallback",
        "title": "Discovery English title (fallback path)",  # enricher 미응답
        "url": "https://www.youtube.com/shorts/vFAILfallback",
    },
]


stats = benchmark.enrich_video_titles_in_place("token-x", videos)
chk("STAT-01: targets=3 (unique)",
    stats["targets"] == 3, f"got {stats}")
chk("STAT-02: enriched=2",
    stats["enriched"] == 2, f"got {stats}")
chk("STAT-03: fallback=1",
    stats["fallback"] == 1, f"got {stats}")


# ═══════════════════════════════════════════════════════════════════
# 성공 enrichment case: title 이 원본(한국어) 으로 교체됨
# ═══════════════════════════════════════════════════════════════════
v0 = videos[0]
chk("EN-01: title_original 저장",
    v0.get("title_original") == "콘서트 중에 무릎 꿇었던 설윤")
chk("EN-02: title_translated 저장",
    v0.get("title_translated") == "Sullyoon kneeling during the concert")
chk("EN-03: title 자체가 title_original 으로 덮어씀 (Claude/HTML 원본 사용)",
    v0.get("title") == "콘서트 중에 무릎 꿇었던 설윤")
chk("EN-04: title_discovery 에 원본 discovery 값 보존",
    v0.get("title_discovery") == "Sullyoon kneeling during the concert")
chk("EN-05: fallback 플래그 없음",
    not v0.get("title_enrich_fallback"))

v1 = videos[1]
chk("EN-06: 두 번째 영상도 원본으로 교체",
    v1.get("title") == "잘 알려진 반전형 짤"
    and v1.get("title_original") == "잘 알려진 반전형 짤"
    and v1.get("title_translated") == "Well-known plot-twist meme")


# ═══════════════════════════════════════════════════════════════════
# fallback case: enricher 응답 없음 → discovery title 을 원본으로 취급하되 플래그
# ═══════════════════════════════════════════════════════════════════
v2 = videos[2]
chk("FB-01: fallback 플래그 True",
    v2.get("title_enrich_fallback") is True)
chk("FB-02: title 은 discovery 값 그대로",
    v2.get("title") == "Discovery English title (fallback path)")
chk("FB-03: title_original 은 discovery 값으로 fallback",
    v2.get("title_original") == "Discovery English title (fallback path)")
chk("FB-04: title_translated = None (모름)",
    v2.get("title_translated") is None)
chk("FB-05: title_discovery 보존",
    v2.get("title_discovery") == "Discovery English title (fallback path)")


# ═══════════════════════════════════════════════════════════════════
# dedup: 동일 video_id 가 목록에 두 번 들어와도 API 호출은 1회
# ═══════════════════════════════════════════════════════════════════
call_count = {"n": 0, "urls_seen": []}
def _counting_enrich(_token, urls):
    call_count["n"] += 1
    call_count["urls_seen"] = list(urls)
    out = {}
    for u in urls:
        for vid, rec in _FIXTURE.items():
            if vid in u:
                out[vid] = dict(rec)
                break
    return out

benchmark.apify_enrich_titles = _counting_enrich

dup_videos = [
    {"video_id": "cZwgBQHn740",  "title": "translated1",
     "url": "https://www.youtube.com/shorts/cZwgBQHn740"},
    {"video_id": "cZwgBQHn740",  "title": "translated1",
     "url": "https://www.youtube.com/shorts/cZwgBQHn740"},  # 중복
    {"video_id": "vAAABBBBCCC",  "title": "translated2",
     "url": "https://www.youtube.com/shorts/vAAABBBBCCC"},
]
stats2 = benchmark.enrich_video_titles_in_place("t", dup_videos)
chk("DEDUP-01: unique targets = 2 (중복 dedup)",
    stats2["targets"] == 2, f"got {stats2}")
chk("DEDUP-02: enrich 함수 1회 호출",
    call_count["n"] == 1, f"got {call_count['n']}")
chk("DEDUP-03: 호출된 URL 은 2개 (unique)",
    len(call_count["urls_seen"]) == 2, f"got {call_count['urls_seen']}")
chk("DEDUP-04: 중복 항목도 각각 title_original 반영",
    dup_videos[0]["title_original"] == dup_videos[1]["title_original"]
    == "콘서트 중에 무릎 꿇었던 설윤")


# ═══════════════════════════════════════════════════════════════════
# _target_angle / analyze_video system 은 원본 title 사용해야 (contract 확인)
# ═══════════════════════════════════════════════════════════════════
# analyze_video 는 v["title"] 을 그대로 프롬프트에 넣는다.
# enrichment 이후 v["title"] == title_original 임을 위에서 확인했으므로
# Claude 입력은 자동으로 원본이 됨. 이 로직 자체를 명시적으로 재확인.
sample = {
    "video_id": "cZwgBQHn740",
    "title": "Sullyoon kneeling during the concert",
    "url": "https://www.youtube.com/shorts/cZwgBQHn740",
}
benchmark.apify_enrich_titles = _fake_enrich   # counting_enrich 뒤에 원 fixture 로 복원
benchmark.enrich_video_titles_in_place("t", [sample])
chk("CLAUDE-01: enrichment 후 v['title'] 이 원본",
    sample["title"] == "콘서트 중에 무릎 꿇었던 설윤")
chk("CLAUDE-02: v['title_original'] 도 원본",
    sample["title_original"] == "콘서트 중에 무릎 꿇었던 설윤")


# ═══════════════════════════════════════════════════════════════════
# Hard-cut · MAX_VIEWS 초과 카드도 HTML 에 title 이 노출되므로 원본 title 사용
# → benchmark.py 는 all_collected_pool 전체를 enrichment 대상으로 삼아,
#   pool 내 dict 를 공유 참조하는 hard_excluded / max_views_excluded 카드에도
#   원본 title 이 반영되는지 확인 (in-place 반영 원칙).
# ═══════════════════════════════════════════════════════════════════
_pool_video = {
    "video_id": "vAAABBBBCCC",
    "title": "Well-known plot-twist meme",       # discovery (번역본)
    "url": "https://www.youtube.com/shorts/vAAABBBBCCC",
    # 이 영상이 Hard 컷된다고 가정: exclusion_reason 이 붙어있음
    "exclusion_reason": "길이 초과 (200초)",
}
# hard_excluded_list 는 pool 안의 같은 dict 를 참조 (실제 코드 흐름과 동일)
hard_excluded_list = [_pool_video]
all_collected_pool = [_pool_video]

# STEP 5.9 시뮬레이션 — all_collected_pool 전체 enrichment
benchmark.enrich_video_titles_in_place("t", all_collected_pool)

chk("POOL-01: pool 내 영상 원본 title 로 in-place 갱신",
    _pool_video["title"] == "잘 알려진 반전형 짤")
chk("POOL-02: hard_excluded_list 도 같은 dict 참조 → 원본 반영",
    hard_excluded_list[0]["title"] == "잘 알려진 반전형 짤"
    and hard_excluded_list[0]["title_original"] == "잘 알려진 반전형 짤")
chk("POOL-03: Hard 컷 사유는 유지",
    hard_excluded_list[0]["exclusion_reason"] == "길이 초과 (200초)")
chk("POOL-04: title_discovery 는 번역본 그대로 보존",
    _pool_video["title_discovery"] == "Well-known plot-twist meme")


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" title enrichment fixture 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
