# -*- coding: utf-8 -*-
"""Target 채널 profile 레지스트리.

Benchmark 엔진 (scripts/benchmark.py) 은 이 레지스트리를 통해 target profile 을
로드한 뒤 identity / reference_channels / modes 를 주입받아 실행됩니다.

profile 은 아래 최소 필드를 dict 로 제공해야 합니다:
    slug                        : "teoldeurim" 등
    display_name                : "털어드림"
    identity                    : {channel_context, content_types, fit_criteria,
                                   benchmark_value_criteria, angle_field_label,
                                   analyze_video_system_prompt,
                                   analyze_video_user_prompt_addendum}
    soft_guidance               : {positive_signals: [...], negative_signals: [...]}
    reference_channels          : [{name, channel}, ...]
    modes                       : {"weekly": {...}, "standard": {...}}
    history_namespace           : "teoldeurim" (sent history 서브디렉명)
    history_shared_across_modes : True/False
    legacy_history_files        : (optional) 털어드림 backward-compat 용
                                  기존 파일들에 대한 glob 패턴 리스트

mode dict 는 hard filter · Claude 상한 · Apify 옵션을 담습니다:
    SORT_BY, MAX_SHORTS_PER_CHANNEL
    MIN_VIEWS, MAX_VIEWS, MAX_VIEWS_INCLUSIVE
    MIN_DURATION_SEC, MAX_DURATION_SEC (None 이면 상한 미적용)
    MIN_AGE_DAYS_EXCLUSIVE, UPLOADED_WITHIN_DAYS
    MAX_ANALYSIS_CANDIDATES, FINAL_CANDIDATES
    PRE_ANALYSIS_DEDUP (bool)
"""
from importlib import import_module

# Target slug → module name
_REGISTRY = {
    "teoldeurim": "teoldeurim",
    "myohanduk":  "myohanduk",
    "jjalduk":    "jjalduk",
}


def list_targets():
    """등록된 target slug 리스트."""
    return sorted(_REGISTRY.keys())


def get_target(slug):
    """slug 에 해당하는 target profile dict 반환.

    ImportError / AttributeError / ModuleNotFoundError 는 그대로 raise 하여
    잘못된 target 을 즉시 알아채도록 한다.
    """
    slug = (slug or "").strip().lower()
    if slug not in _REGISTRY:
        raise ValueError(
            f"Unknown target={slug!r}. Available: {list_targets()}"
        )
    mod_name = _REGISTRY[slug]
    mod = import_module(f"targets.{mod_name}")
    profile = getattr(mod, "TARGET")
    if profile["slug"] != slug:
        raise ValueError(
            f"Target module {mod_name!r} slug mismatch: "
            f"expected {slug!r}, got {profile['slug']!r}"
        )
    return profile


def resolve_mode(target_slug, mode):
    """target + mode → 하드필터·Claude 상한·Apify 옵션이 병합된 dict 반환.

    사용:
        cfg = resolve_mode("teoldeurim", "weekly")
        cfg["MIN_VIEWS"]   # target·mode 값 반영
        cfg["_TARGET"]     # target profile dict (identity/reference 등 접근)
        cfg["_MODE"]       # "weekly" / "standard"
    """
    target = get_target(target_slug)
    if mode not in target["modes"]:
        raise ValueError(
            f"Target {target_slug!r} 에는 mode={mode!r} 가 없습니다. "
            f"Available: {sorted(target['modes'].keys())}"
        )
    merged = dict(target["modes"][mode])
    merged["_TARGET"] = target
    merged["_MODE"] = mode
    return merged
