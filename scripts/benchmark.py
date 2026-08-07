# -*- coding: utf-8 -*-
"""
털어드림 타 채널 벤치마크 모듈.

외부 참고 채널들의 인기 Shorts를 Apify로 수집해서
  1) 털어드림에 쓸 만한 '참고 후보 리스트'를 골라 fit_score 순으로 순위 매기고
  2) 그 후보들에서 뽑은 '기획 포인트'를 정리한
HTML 리포트를 생성한다.

weekly.py를 대체하지 않는다 — 후보 풀을 보완하는 보조 모듈.
weekly.py 코드/history는 일절 건드리지 않는다.

실행:
  python scripts/benchmark.py
  (.env에서 APIFY_TOKEN / ANTHROPIC_API_KEY 자동 로드)

필요 환경변수:
  APIFY_TOKEN        - Apify API 토큰
  ANTHROPIC_API_KEY  - Claude API 키

산출물 (benchmark/ 디렉터리):
  YYYY-MM-DD_filtered_raw.json  - 제외 채널 필터링이 끝난 수집 데이터
  YYYY-MM-DD_report.html        - 후보 리스트 + 기획 포인트 리포트
"""
import os
import re
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import anthropic

# ----------------------------------------------------------------------------
# 콘솔 한국어 출력 깨짐 방지 (Windows PowerShell 등)
# ----------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = ROOT / "benchmark"
BENCHMARK_DIR.mkdir(exist_ok=True)
HISTORY_DIR = ROOT / "history"  # weekly.py 산출물 — 읽기 전용으로만 접근

# config/benchmark_config.py 로드
sys.path.insert(0, str(ROOT / "config"))
import benchmark_config  # noqa: E402

# CFG는 프로파일 미확정 상태의 기본값(=standard 필드가 이미 포함된 BENCHMARK_CONFIG).
# main() 시작 시 PROFILE env 기준으로 resolve_config() 결과를 CFG에 병합함.
# 임포트 시점의 backward compat: 프로파일 지정 없는 실행도 standard와 동일값.
CFG = dict(benchmark_config.BENCHMARK_CONFIG)


# ============================================================================
# 환경변수 / .env 로딩
# ============================================================================
def load_dotenv():
    """ROOT/.env 파일이 있으면 os.environ에 채워 넣는다 (이미 있는 키는 유지)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ============================================================================
# 작은 유틸
# ============================================================================
def esc_html(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def to_int(v):
    """'1.2M', '12,345', 12345 등 다양한 형태를 정수로."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().upper().replace(",", "").replace(" ", "")
    if not s:
        return 0
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("B"):
        mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def parse_duration(v):
    """'0:34', '1:02:03', 34, '34s' 등을 초 단위 정수로."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower().replace("s", "")
    if ":" in s:
        sec = 0
        for part in s.split(":"):
            try:
                sec = sec * 60 + int(part)
            except ValueError:
                return 0
        return sec
    try:
        return int(float(s))
    except ValueError:
        return 0


def parse_date(v):
    """ISO 날짜 문자열을 UTC datetime으로. 실패 시 None.

    ⚠️ date-only 파싱 (시간 부분 무시) — 리포트의 days_since_upload 표시용 유지.
    기간 경계 필터에는 사용하지 않는다. 정확한 timestamp가 필요하면 parse_timestamp() 사용.
    """
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def parse_timestamp(v):
    """ISO 8601 timestamp → aware UTC datetime. 실패 시 None.

    지원 형식:
      - Weekly (YouTube API):     "2026-06-25T11:01:33Z"
      - Benchmark (Apify):        "2026-06-09T07:00:21.000Z"
      - date-only fallback:       "2026-06-09"  (parse_date와 동일)

    기간 경계 필터의 source of truth. 정수 days_since_upload는 리포트 표시용.
    반환값은 항상 tzinfo=UTC aware.
    """
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # date-only fallback (레거시 데이터)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def fmt_pct(x):
    return f"{x * 100:.2f}%"


def fmt_int(n):
    return f"{n:,}"


# ============================================================================
# Apify 수집
# ============================================================================
def http_json(url, data=None, method="GET", timeout=60):
    headers = {"User-Agent": "teoldeurim-benchmark/1.0"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {e.reason} — {detail[:300]}") from None


def apify_collect(token, channels, max_per_channel, sort_by):
    """Apify 액터를 실행해 채널별 인기 Shorts를 수집한다.

    비동기로 실행한 뒤 상태를 폴링하고, 완료되면 데이터셋 아이템을 반환.
    """
    actor_path = CFG["APIFY_ACTOR"].replace("/", "~")
    run_input = {
        "channels": channels,
        "maxResultsShorts": max_per_channel,
        "sortChannelShortsBy": sort_by,
    }

    print(f"  [Apify] 액터 실행: {CFG['APIFY_ACTOR']}")
    print(f"  [Apify] 채널 {len(channels)}개 / 채널당 최대 {max_per_channel}개 / 정렬 {sort_by}")

    start_url = f"https://api.apify.com/v2/acts/{actor_path}/runs?token={quote(token)}"
    run = http_json(start_url, data=run_input, method="POST", timeout=60)["data"]
    run_id = run["id"]
    print(f"  [Apify] run 시작: {run_id}")

    # 폴링 (최대 12분)
    deadline = time.time() + 720
    status = run.get("status", "")
    while time.time() < deadline:
        time.sleep(12)
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={quote(token)}"
        run = http_json(status_url, timeout=60)["data"]
        status = run.get("status", "")
        print(f"  [Apify] 상태: {status}")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT"):
            break

    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run이 정상 종료되지 않음 (상태: {status})")

    dataset_id = run["defaultDatasetId"]
    items_url = (f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                 f"?token={quote(token)}&format=json&clean=true")
    items = http_json(items_url, timeout=120)
    items = items or []
    print(f"  [Apify] 수집 아이템: {len(items)}개")
    return items


def normalize_video(item):
    """Apify 액터의 원본 아이템을 일관된 dict로 변환 (필드명 변형 방어)."""
    def first(*keys):
        for k in keys:
            if k in item and item[k] not in (None, "", []):
                return item[k]
        return None

    video_id = first("id", "videoId", "shortId") or ""
    url = first("url", "videoUrl", "shortUrl") or ""
    if not url and video_id:
        url = f"https://www.youtube.com/shorts/{video_id}"

    vid = {
        "video_id": video_id,
        "title": first("title", "name") or "(제목 없음)",
        "url": url,
        "channel_name": first("channelName", "channelTitle", "channel", "author") or "",
        "channel_id": first("channelId", "channelID", "channelId") or "",
        "channel_url": first("channelUrl", "channelLink") or "",
        "subscribers": to_int(first("numberOfSubscribers", "subscriberCount",
                                    "channelSubscriberCount", "subscribers")),
        "views": to_int(first("viewCount", "views", "numberOfViews")),
        "likes": to_int(first("likes", "likeCount", "numberOfLikes")),
        "comments": to_int(first("commentsCount", "commentCount",
                                 "numberOfComments", "comments")),
        "duration_sec": parse_duration(first("duration", "lengthSeconds",
                                             "durationSeconds")),
        "published_at": first("date", "uploadDate", "publishedAt", "publishDate") or "",
        "hashtags": first("hashtags") or [],
        "thumbnail": first("thumbnailUrl", "thumbnail", "thumbnailLink") or "",
        "description": first("text", "description") or "",
    }
    return vid


# ============================================================================
# 제외 채널 검증 / 필터링
# ============================================================================
ABORT_EXCLUDE_MSG = "벤치마크 대상에 제외 채널이 포함되어 실행을 중단했습니다"


def ref_channel_id(ref):
    """참고 채널 dict에서 channel_id를 뽑는다 (검증의 주 기준).

    자동 추출 채널은 _channel_id를 갖고, 수동 채널은 channel URL에
    UC... ID가 있으면 정규식으로 추출. 없으면 None.
    """
    cid = (ref.get("_channel_id") or "").strip()
    if cid:
        return cid
    m = re.search(r"(UC[\w-]{22})", str(ref.get("channel", "")))
    return m.group(1) if m else None


def ref_channel_name(ref):
    """참고 채널 dict의 채널명 (검증의 보조 기준)."""
    return (ref.get("name") or "").strip()


def find_excluded_conflicts(channels):
    """채널 목록에서 제외 대상을 찾는다.

    주 기준 : channel_id 가 EXCLUDE_CHANNEL_IDS 와 정확히 일치
    보조 기준: channel_name 이 EXCLUDE_CHANNELS 와 정확히 일치
    (substring이 아닌 정확 일치 — 오탐 방지)
    반환: [(ref, 사유), ...]
    """
    excl_ids = {i.strip() for i in CFG.get("EXCLUDE_CHANNEL_IDS", set()) if i.strip()}
    excl_names = {n.strip().lower() for n in CFG.get("EXCLUDE_CHANNELS", set()) if n.strip()}
    conflicts = []
    for ref in channels:
        cid = ref_channel_id(ref)
        if cid and cid in excl_ids:
            conflicts.append((ref, f"channel_id '{cid}' 가 제외 목록과 일치 (주 기준)"))
            continue
        cname = ref_channel_name(ref)
        if cname and cname.lower() in excl_names:
            conflicts.append((ref, f"channel_name '{cname}' 가 제외 목록과 일치 (보조 기준)"))
    return conflicts


def validate_manual_channels(manual):
    """수동 지정 채널 검증 — 제외 채널이 섞이면 사용자 실수이므로 즉시 중단."""
    conflicts = find_excluded_conflicts(manual)
    if conflicts:
        print(f"❌ {ABORT_EXCLUDE_MSG}.", file=sys.stderr)
        for ref, why in conflicts:
            print(f"   - [수동] {ref.get('name', '?')} ({ref.get('channel', '?')}) → {why}",
                  file=sys.stderr)
        print("   config/benchmark_config.py의 REFERENCE_CHANNELS를 수정하세요.",
              file=sys.stderr)
        sys.exit(1)


def purge_excluded_channels(channels, label):
    """자동 추출 등 목록에서 제외 채널을 한 번 더 걸러낸다 (방어적 재검증).

    자동 추출분은 추출 단계에서 이미 1차 필터링되지만, Apify 수집 대상에
    들어가기 전에 다시 검증한다. 제외 대상이 잡히면 중단하지 않고
    드롭 + 경고 (자동 추출은 사용자 실수가 아니므로).
    """
    conflicts = find_excluded_conflicts(channels)
    if not conflicts:
        return channels
    bad_ids = {id(ref) for ref, _ in conflicts}
    for ref, why in conflicts:
        print(f"  ⚠️ [{label}] 제외 채널 감지 → 드롭: {ref.get('name', '?')} ({why})")
    return [c for c in channels if id(c) not in bad_ids]


def validate_reference_channels(refs):
    """최종 병합 목록 — Apify 수집에 들어가기 직전 마지막 검증.

    채널이 0개거나 제외 채널이 한 개라도 있으면 중단.
    """
    if not refs:
        print("❌ 참고 채널이 한 개도 없습니다.", file=sys.stderr)
        print("   config/benchmark_config.py의 REFERENCE_CHANNELS에 직접 등록하거나,",
              file=sys.stderr)
        print("   AUTO_REFERENCE_FROM_HISTORY=True로 두고 history/*.json을 쌓으세요.",
              file=sys.stderr)
        sys.exit(1)

    conflicts = find_excluded_conflicts(refs)
    if conflicts:
        print(f"❌ {ABORT_EXCLUDE_MSG}.", file=sys.stderr)
        for ref, why in conflicts:
            src = "자동" if ref.get("_auto") else "수동"
            print(f"   - [{src}] {ref.get('name', '?')} ({ref.get('channel', '?')}) → {why}",
                  file=sys.stderr)
        sys.exit(1)

    print(f"  ✅ 참고 채널 {len(refs)}개 최종 검증 통과 (제외 채널 없음)")


# ============================================================================
# history 기반 자동 참고 채널 추출 (읽기 전용)
# ============================================================================
def load_history_records():
    """history/*.json을 읽어 주차별 레코드와 발송 영상 ID 집합을 반환.

    ⚠️ 읽기 전용 — history 파일을 절대 수정/삭제하지 않는다.
    반환: (weeks, sent_video_ids)
      weeks         = [{"file", "candidates":[...], "hard_passed":[...]}, ...]
      sent_video_ids = weekly가 실제 발송한 영상(candidates) ID 집합
    """
    weeks, sent_ids = [], set()
    if not HISTORY_DIR.exists():
        return weeks, sent_ids
    for f in sorted(HISTORY_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️ history 읽기 실패 {f.name}: {e}")
            continue
        candidates = d.get("candidates") or []
        rl = d.get("report_lists") or {}
        hard_passed = rl.get("hard_passed") or []
        for it in candidates:
            vid = it.get("video_id")
            if vid:
                sent_ids.add(vid)
        weeks.append({"file": f.name, "candidates": candidates,
                      "hard_passed": hard_passed})
    return weeks, sent_ids


def load_benchmark_sent_history():
    """benchmark/history/sent/*.json을 읽어 이전 benchmark 리포트(recent/standard)에서
    발송된 영상 id 집합을 반환.

    orchestrator(send_report.py)가 실제 메일 발송 성공 후에만 이 디렉터리에 파일을 쓴다.
    → 발송 실패한 실행의 후보는 여기 없음 → 다음 실행에서 정상적으로 다시 후보로 검토됨.

    weekly history와 완전 분리 — 이 함수는 weekly history를 참조하지 않음.
    weekly history의 sent_ids는 별도로 load_history_records()가 담당.
    """
    result = set()
    root_dir = ROOT / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
    if not root_dir.exists():
        return result
    for f in sorted(root_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️ benchmark sent history 읽기 실패 {f.name}: {e}")
            continue
        for vid in (d.get("candidates_video_ids") or []):
            if vid:
                result.add(vid)
    return result


def _accumulate_channel(scores, item, weight, week_file):
    """channel_id 기준으로 채널 점수를 누적."""
    cid = (item.get("channel_id") or "").strip()
    if not cid:
        return  # channel_id 없으면 안정적 식별 불가 → 스킵
    name = (item.get("channel") or "").strip()
    s = scores.setdefault(cid, {"name": name, "score": 0,
                                "weeks": set(), "videos": 0})
    if name:
        s["name"] = name  # 최신 채널명으로 갱신
    s["score"] += weight
    s["weeks"].add(week_file)
    s["videos"] += 1


def extract_reference_channels_from_history(weeks):
    """history 주차 레코드에서 참고 채널을 빈도순으로 추출.

    candidates(최종 채택)는 가중치 높게, hard_passed(통과 풀)는 낮게 집계.
    제외 채널은 결과에서 빼고, 최소 점수 미만은 컷한 뒤 상위 N개 반환.
    """
    excl_names = {n.strip().lower() for n in CFG.get("EXCLUDE_CHANNELS", set()) if n.strip()}
    excl_ids = {i.strip() for i in CFG.get("EXCLUDE_CHANNEL_IDS", set()) if i.strip()}
    cw = CFG["HISTORY_CANDIDATE_WEIGHT"]
    hw = CFG["HISTORY_HARDPASS_WEIGHT"]

    scores = {}
    for wk in weeks:
        cand_vids = {it.get("video_id") for it in wk["candidates"] if it.get("video_id")}
        for it in wk["candidates"]:
            _accumulate_channel(scores, it, cw, wk["file"])
        for it in wk["hard_passed"]:
            # 같은 영상이 candidates에도 있으면 중복 집계 방지
            if it.get("video_id") in cand_vids:
                continue
            _accumulate_channel(scores, it, hw, wk["file"])

    # 제외 채널 제거 + 최소 점수 컷
    ranked = []
    for cid, info in scores.items():
        if cid in excl_ids:
            continue
        if info["name"].strip().lower() in excl_names:
            continue
        if info["score"] < CFG["AUTO_REFERENCE_MIN_SCORE"]:
            continue
        ranked.append((cid, info))

    # 점수 → 등장 주차 수 순 정렬, 상위 N개
    ranked.sort(key=lambda x: (x[1]["score"], len(x[1]["weeks"]), x[1]["videos"]),
                reverse=True)
    ranked = ranked[:CFG["AUTO_REFERENCE_TOP_N"]]

    channels = []
    for cid, info in ranked:
        channels.append({
            "name": info["name"] or "(채널명 미상)",
            "channel": f"https://www.youtube.com/channel/{cid}",
            "_auto": True,
            "_channel_id": cid,
            "_score": info["score"],
            "_weeks": len(info["weeks"]),
            "_videos": info["videos"],
        })
    return channels


def _channel_key(s):
    """채널 문자열을 비교용 키로 정규화."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def build_reference_channels():
    """수동 지정 + history 자동 추출을 합쳐 최종 참고 채널 목록을 만든다.

    처리 순서:
      1. 수동 채널(REFERENCE_CHANNELS) — 제외 채널 섞이면 즉시 중단
      2. history 자동 추출 → auto_discovered_reference_candidates
      3. 자동 추출분 제외 검증 한 번 더 (드롭 + 경고)
      4. 병합 — channel_id 기준 중복 제거, 수동 우선

    반환: dict {
      "manual": [...],
      "auto_discovered_reference_candidates": [...],
      "merged": [...],
      "sent_video_ids": set,
    }
    """
    # 1. 수동 채널 — 제외 검증 (사용자 실수 → 중단)
    manual = [dict(r) for r in CFG.get("REFERENCE_CHANNELS", [])]
    for r in manual:
        r.setdefault("_auto", False)
    validate_manual_channels(manual)

    # 2~3. history 자동 추출 + 재검증
    auto_discovered_reference_candidates, sent_ids = [], set()
    if CFG.get("AUTO_REFERENCE_FROM_HISTORY"):
        weeks, sent_ids = load_history_records()
        auto_discovered_reference_candidates = extract_reference_channels_from_history(weeks)
        print(f"  history {len(weeks)}주차 → 자동 추출 후보 "
              f"{len(auto_discovered_reference_candidates)}개 "
              f"(발송 영상 {len(sent_ids)}개 식별)")
        # Apify 수집 대상에 들어가기 전 제외 검증 한 번 더
        before = len(auto_discovered_reference_candidates)
        auto_discovered_reference_candidates = purge_excluded_channels(
            auto_discovered_reference_candidates, "자동추출")
        if len(auto_discovered_reference_candidates) != before:
            print(f"  자동 추출 채널 재검증: {before}개 → "
                  f"{len(auto_discovered_reference_candidates)}개")

    # 4. 병합 — channel_id 주 기준 중복 제거, 수동 우선
    merged, seen_ids, seen_keys = [], set(), set()
    for ref in manual:
        merged.append(ref)
        cid = ref_channel_id(ref)
        if cid:
            seen_ids.add(cid)
        seen_keys.add(_channel_key(ref.get("channel", "")))
    dup = 0
    for ref in auto_discovered_reference_candidates:
        cid = ref_channel_id(ref)
        ckey = _channel_key(ref.get("channel", ""))
        if (cid and cid in seen_ids) or (ckey and ckey in seen_keys):
            dup += 1
            continue
        merged.append(ref)
        if cid:
            seen_ids.add(cid)
        seen_keys.add(ckey)
    if dup:
        print(f"  수동/자동 channel_id 중복 {dup}개 제거")

    return {
        "manual": manual,
        "auto_discovered_reference_candidates": auto_discovered_reference_candidates,
        "merged": merged,
        "sent_video_ids": sent_ids,
    }


def filter_excluded_channels(videos):
    """수집된 영상 중 제외 채널 소속을 골라낸다.

    정상 동작이라면 참고 채널만 수집되므로 제거 대상이 0개여야 한다.
    하나라도 제거 대상이 잡히면 (= 참고 채널에 제외 채널이 숨어 있었던 것)
    저장/리포트 생성을 진행하지 않고 중단한다.
    """
    excl_names = {n.strip().lower() for n in CFG.get("EXCLUDE_CHANNELS", set()) if n.strip()}
    excl_ids = {i.strip() for i in CFG.get("EXCLUDE_CHANNEL_IDS", set()) if i.strip()}

    kept, removed = [], []
    for v in videos:
        cid = (v.get("channel_id") or "").strip()
        cname = (v.get("channel_name") or "").strip()
        if cid and cid in excl_ids:
            removed.append((v, f"제외 채널 ID {cid}"))
        elif cname and cname.lower() in excl_names:
            removed.append((v, f"제외 채널명 {cname}"))
        else:
            kept.append(v)
    return kept, removed


# ============================================================================
# Hard 필터 / 지표 계산
# ============================================================================
def hard_filter(videos, now):
    """조회수 / 길이 / 업로드 age 기준 1차 컷.

    업로드 age는 실제 published_at UTC timestamp 기준 (초 단위) — 정수 days_since_upload
    는 리포트 표시용이며, 여기 필터 판정에는 사용하지 않는다.

    - recent (MIN_AGE_DAYS_EXCLUSIVE=None, UPLOADED_WITHIN_DAYS=30):
        pub >= now - 30일 통과 (정확히 30일도 통과)
    - standard (MIN_AGE_DAYS_EXCLUSIVE=30, UPLOADED_WITHIN_DAYS=365):
        pub < now - 30일 AND pub >= now - 365일 통과 (정확히 30일은 컷)
    """
    min_views = CFG["MIN_VIEWS"]
    max_dur = CFG["MAX_DURATION_SEC"]
    min_dur = CFG.get("MIN_DURATION_SEC", 0)   # 26.07.15: 15초 미만 초단타 컷
    age_min = CFG.get("MIN_AGE_DAYS_EXCLUSIVE")   # None 또는 30
    age_max = CFG.get("UPLOADED_WITHIN_DAYS")     # 30 또는 365

    passed, excluded = [], []
    for v in videos:
        if v["views"] < min_views:
            excluded.append((v, f"조회수 부족 ({v['views']:,})"))
            continue
        # duration_sec 파싱 실패 시 0 (normalize_video 반환값) → min_dur=15면 여기서 컷.
        # 사유를 명확히 구분 (0초 = 파싱 실패, 그 외 = 실제 초단타).
        _d = v.get("duration_sec", 0)
        if min_dur > 0 and _d < min_dur:
            if _d <= 0:
                excluded.append((v, f"길이 파싱 실패 (duration_sec={_d})"))
            else:
                excluded.append((v, f"길이 {min_dur}초 미만 ({_d}초)"))
            continue
        if _d > max_dur:
            excluded.append((v, f"길이 초과 ({_d}초)"))
            continue
        # timestamp 기반 age 판정 (source of truth)
        pub = parse_timestamp(v["published_at"])
        if pub is None:
            excluded.append((v, "업로드 시간 파싱 실패 (age_parse)"))
            continue
        if age_min is not None and pub >= now - timedelta(days=age_min):
            # strict >= 컷 → 정확히 age_min일 = 컷 (standard에서 30일은 recent 소속)
            excluded.append((v, f"업로드 {age_min}일 이내 (범위 밖)"))
            continue
        if age_max is not None and pub < now - timedelta(days=age_max):
            excluded.append((v, f"업로드 {age_max}일 초과"))
            continue
        passed.append(v)
    return passed, excluded


def apply_max_views_filter(videos):
    """MAX_VIEWS 초과 영상을 분리한다 (Hard 필터 이전).

    제거가 아니라 분리 — 리포트 탭 '조회수 초과 제외'에 별도 표시.
    각 제외 영상에 exclusion_reason 필드를 부여한다.

    반환: (under_max, over_max)
      under_max = MAX_VIEWS 이하 (다음 단계로 진행)
      over_max  = MAX_VIEWS 초과 (별도 보관, 탭에만 노출)
    """
    max_views = CFG.get("MAX_VIEWS")
    if not max_views:
        return videos, []
    under, over = [], []
    for v in videos:
        if v["views"] > max_views:
            v["exclusion_reason"] = f"조회수 {max_views:,} 초과"
            over.append(v)
        else:
            under.append(v)
    return under, over


def compute_metrics(v, now):
    """영상 dict에 7개 벤치마크 지표를 채워 넣는다."""
    views = max(v["views"], 0)
    v["like_rate"] = (v["likes"] / views) if views else 0.0
    v["comment_rate"] = (v["comments"] / views) if views else 0.0
    v["engagement_rate"] = ((v["likes"] + v["comments"]) / views) if views else 0.0
    v["views_per_sub"] = (views / v["subscribers"]) if v["subscribers"] else 0.0
    dt = parse_date(v["published_at"])
    v["days_since_upload"] = (now - dt).days if dt is not None else None
    return v


# ============================================================================
# Claude 분석
# ============================================================================
BENCHMARK_SYSTEM_PROMPT = """당신은 K-pop Shorts 채널 '털어드림'의 벤치마크 큐레이터입니다.
타 채널의 인기 Shorts를 보고, 털어드림에 후보로 쓸 만한지 평가하고 기획 포인트를 뽑습니다.

# 채널 정체성

'털어드림'은 K-pop 연예인 중심의 이슈·비하인드·관계성 분석형 Shorts 채널입니다.
단순 가십이나 장면 전달이 아니라, 인물의 행동·감정·관계·산업 구조 속에서
"왜 이런 장면이 나왔는지, 어떤 맥락이 있는지, 무엇이 반복 소비되는지"를 분석적으로 다룹니다.

핵심 공식 — 시의성 지연:
  1차(이슈 자체) → 2차(반응 정리·해석형) → 3차(여론 분석·산업 영향)
이슈 직후 단순 보도가 아닌, 2차/3차 해석으로 재가공해 시간이 지나도 유효한
구조적 분석을 추구합니다. 휘발성 콘텐츠 ❌, 반복 가능한 구조적 콘텐츠 ✅.

# 8가지 핵심 소재 유형

털어드림에 직접 사용할 후보는 아래 8가지 유형 중 하나에 명확히 속하는 것을 우선합니다.
다만 벤치마크 목적에서는 8가지 유형에 완전히 속하지 않더라도,
재사용 가능한 훅·제목 구조·관계성·페르소나·팬덤 반응 구조가 있으면 참고 가치가 있다고 판단할 수 있습니다.

1. K-pop 산업 분석: 계약·경제·정책·소속사 시스템
2. 아이돌 뒷이야기: 녹음실·촬영장·연습실 비하인드
3. 아이돌 일상 상황극: 실물 vs 무대, 기대 vs 현실 갭
4. 뮤직비디오 비하인드: 위험 촬영, NG 장면, 메이킹 디테일
5. 아이돌 고충 분석: 수면·다이어트·스케줄·건강·심리
6. **관계성·계보 분석**: 선배 → 후배 영향, 그룹 간 계보, 멤버 간 관계,
   팬-아이돌 상호작용에서 반복되는 관계 패턴
7. 실력/논란 해설: 안무·라이브·인성 논란의 구조적 이유
8. 연습생/데뷔 비하인드: 회사 결정·트레이닝 시스템·데뷔 과정

# 5가지 훅 패턴 (성공 영상의 첫 3초 구조)

1. 비교형: 과거 vs 현재 / 기대 vs 현실 / 선배 vs 후배
2. 의외성형: 충격적 사실, 위험한 상황, 예상 밖 폭로
3. 극한상황형: 수치·극단 강조 (72시간, 5년의 연습생, 죽기 살기)
4. 설명형: 기술·경제·구조적 배경을 분석적으로 해부
5. 유머형: 자조적 유머, 놀라운 장면, 위트 있는 표현

# 1군 아이돌 정의

대형/중대형 기획사 소속 + 대중 인지도 높은 현역 그룹의 멤버 또는 그룹 자체:
- HYBE: BTS, NewJeans/NJZ, TXT, ENHYPEN, ILLIT, LE SSERAFIM, SEVENTEEN, &TEAM
- SM: aespa, RIIZE, NCT(127/Dream/WayV), Red Velvet, Hearts2Hearts
- YG: BLACKPINK, BABYMONSTER, TREASURE
- JYP: TWICE, ITZY, NMIXX, Stray Kids
- 기타: IVE, (G)I-DLE, MAMAMOO, ATEEZ, TWS, KISS OF LIFE, BOYNEXTDOOR,
  fromis_9, ZEROBASEONE, RESCENE
- 솔로로 트렌드 1군 진입: (아이즈원 출신) 최예나, 권은비, 조유리, (우주소녀 출신) 다영
무명·소형 기획사·트로트·해외 K-pop은 1군 외.

동명이인 주의: 한국 아이돌 이름은 동명이인이 흔합니다(다영/지수/유나/하니 등).
메타데이터만으로 인물 식별이 100% 확실하지 않으면 1군 그룹 멤버 가능성을 우선
고려하고 "(추정)"으로 표시하세요.

# 좋은 후보 / 나쁜 후보 신호

좋은 직접 후보: 1군 인물 직접 등장, 의문형/부정형 제목, 시의성 낮고 반복 가능,
  8개 소재 유형에 명확히 속함, 분석 각도 존재, 한국어 제목.

나쁜 후보: 영어 제목, 직캠/뮤비/무대 본방 그 자체, 특정 시점에만 의미가 있는 휘발성 가십,
  콘텐츠의 주인공이 무명 아이돌·일반 유튜버 중심인 경우, 자극·낚시 톤,
  분석 각도와 재사용 가능한 기획 포인트가 모두 없음.

단순 짤·밈·리액션·모음형이라는 이유만으로 자동 탈락시키지 않습니다.
반복 가능한 제목 구조, 강한 훅, 멤버 페르소나, 관계성, 팬덤 반응 구조 등
털어드림식으로 변형 가능한 기획 포인트가 있다면 벤치마크 가치가 있는 것으로 판단합니다.

지시받은 출력 형식(JSON)을 정확히 따르세요. JSON 외 텍스트는 출력하지 마세요.

# 벤치마크 평가 원칙

벤치마크에서는 "털어드림에 그대로 사용할 수 있는가"와
"소재·훅·구성 방식을 털어드림식으로 변형할 가치가 있는가"를 구분해서 판단합니다.

단순 밈·리액션·모음형 콘텐츠라도,
반복 가능한 제목 구조, 강한 첫 3초 훅, 멤버 페르소나,
관계성, 팬덤 반응 구조 등 재사용 가능한 기획 포인트가 명확하면
벤치마크 가치는 있다고 판단할 수 있습니다.

단, weekly 실제 후보 선정 기준은 별개이며,
벤치마크 가치가 있다고 해서 실제 후보로 자동 채택되는 것은 아닙니다.
"""

VIDEO_SCHEMA = {
    "type": "object",
    "properties": {
        # 축 A — 직접 후보 적합도
        "fit_score": {"type": "integer"},           # 0-100
        "recommend": {"type": "boolean"},
        "fit_reason": {"type": "string"},
        # 축 B — 벤치마크 가치 (독립 축, fit_score와 상관관계 강제 없음)
        "benchmark_value_score": {"type": "integer"},   # 0-100
        "benchmark_value_reason": {"type": "string"},   # 무엇을 재사용 가능한지 구체적으로
        # 공통 메타
        "topic_type": {"type": "string"},
        "hook_type": {"type": "string"},
        "idol_tier": {"type": "string"},
        "risk": {"type": "string"},
        "teoldeurim_angle": {"type": "string"},
    },
    "required": ["fit_score", "recommend", "fit_reason",
                 "benchmark_value_score", "benchmark_value_reason",
                 "topic_type", "hook_type", "idol_tier", "risk", "teoldeurim_angle"],
    "additionalProperties": False,
}

PATTERN_SCHEMA = {
    "type": "object",
    "properties": {
        # A. 직접 후보 공통 패턴 — fit_score 축 관점
        "direct_common_patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["label", "detail"],
                "additionalProperties": False,
            },
        },
        # B. 벤치마크형 공통 패턴 — 훅·페르소나·관계성·팬덤 반응 관점
        "benchmark_common_patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["label", "detail"],
                "additionalProperties": False,
            },
        },
        # C. 채널별 인사이트 — 실제 표본에 근거. 표본 부족 시 note에 명시
        "per_channel_insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "sample_size": {"type": "integer"},
                    "note": {"type": "string"},
                    "insights": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "required": ["label", "detail"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["channel", "sample_size", "note", "insights"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["direct_common_patterns", "benchmark_common_patterns",
                 "per_channel_insights"],
    "additionalProperties": False,
}

# Claude Opus 4.7 단가 (USD per 1M tokens)
PRICING = {"input": 5.0, "cache_write": 6.25, "cache_read": 0.50, "output": 25.0}


def estimate_cost(usage):
    return (usage["input"] * PRICING["input"]
            + usage["cache_write"] * PRICING["cache_write"]
            + usage["cache_read"] * PRICING["cache_read"]
            + usage["out"] * PRICING["output"]) / 1_000_000


def _usage_of(response):
    u = response.usage
    return {
        "input": u.input_tokens or 0,
        "cache_write": u.cache_creation_input_tokens or 0,
        "cache_read": u.cache_read_input_tokens or 0,
        "out": u.output_tokens or 0,
    }


def _add_usage(total, u):
    for k in total:
        total[k] += u[k]


def analyze_video(client, v):
    """단일 영상 — 털어드림 적합도 평가."""
    hts = " ".join(v.get("hashtags") or [])[:200]
    days = v.get("days_since_upload")
    days_str = f"{days}일 전" if days is not None else "(업로드일 불명)"
    user_msg = (
        "다음 YouTube Shorts가 '털어드림' 채널에서 다룰 만한 후보인지 평가하세요.\n\n"
        f"제목: {v['title']}\n"
        f"채널: {v['channel_name']}\n"
        f"조회수: {v['views']:,}\n"
        f"좋아요: {v['likes']:,} (좋아요율 {fmt_pct(v['like_rate'])})\n"
        f"댓글: {v['comments']:,} (댓글률 {fmt_pct(v['comment_rate'])})\n"
        f"구독자: {v['subscribers']:,}\n"
        f"구독자 대비 조회수: {v['views_per_sub']:.2f}배\n"
        f"길이: {v['duration_sec']}초\n"
        f"업로드: {v['published_at'][:10]} ({days_str})\n"
        f"해시태그: {hts}\n"
        f"URL: {v['url']}\n\n"
        "## 출력할 JSON 필드 (두 점수를 독립적으로 평가)\n\n"
        "### 축 A — 직접 후보 적합도\n"
        "- fit_score: 0~100 정수. 이 영상을 털어드림 채널에 직접 후보로 가져와\n"
        "  2차/3차 분석형 콘텐츠로 변형하기 좋은가? (8소재·5훅·1군 인물 기준)\n"
        "- recommend: boolean. fit_score 60 이상이고 채널 톤에 맞으면 true\n"
        "- fit_reason: 왜 쓸 만한지(또는 아닌지) 1~2문장, 한국어\n\n"
        "### 축 B — 벤치마크 가치 (fit_score와 독립. 상관관계 강제 X)\n"
        "- benchmark_value_score: 0~100 정수. 직접 후보 적합도와 별개로,\n"
        "  소재·훅·제목 구조·관계성·페르소나·팬덤 반응 등에서 참고 가치 정도.\n"
        "  같은 영상이 fit=30/bv=80 이거나 fit=85/bv=40 이어도 됨.\n"
        "  Anchor:\n"
        "    0~29:  재사용 가능한 훅·구조·페르소나·관계성 포인트가 거의 없음\n"
        "    30~59: 일부 참고 요소는 있으나 일반적이거나 재사용성이 제한적\n"
        "    60~79: 털어드림식으로 변형 가능한 훅·제목 구조·관계성·페르소나 포인트가 명확\n"
        "    80~100: 직접 후보 적합도와 무관하게 매우 강한 벤치마크 가치.\n"
        "            반복 가능한 포맷·캐릭터성·팬덤 반응 구조 등 뚜렷한 재사용 요소\n"
        "- benchmark_value_reason: 구체적으로 무엇을 재사용 가능한지 명시.\n"
        "  '참고 가치 있음', '훅이 좋음' 같은 추상 표현 금지. 반드시 아래 중 하나 이상\n"
        "  명시적으로 지정:\n"
        "    · 제목 구조 (구체 패턴 명시)\n"
        "    · 첫 3초 훅 (어떤 형태의 훅인지)\n"
        "    · 멤버 페르소나 (어떤 캐릭터 각인)\n"
        "    · 팬-아이돌 상호작용 (어떤 상호작용 형태)\n"
        "    · 멤버 간 관계성 (어떤 관계 구도)\n"
        "    · 팬덤 인사이더 문화 (구체 요소)\n"
        "    · 반복 행동 패턴 (어떤 캐릭터 반복)\n"
        "    · 유머·리액션 구조 (어떤 유머 형태)\n"
        "    · 컴필레이션 방식 (모음 구조)\n"
        "    · 감정 서사 (어떤 감정 흐름)\n\n"
        "### 공통 메타\n"
        "- topic_type: 8개 소재 유형 중 하나 + 등장 인물/그룹 괄호 부기\n"
        "- hook_type: 5개 훅 패턴 중 하나\n"
        "- idol_tier: '1군' / '(추정) 1군' / '1군 외' 중 하나\n"
        "- risk: 동명이인·민감소재·저작권 등 주의점 (없으면 '없음')\n"
        "- teoldeurim_angle: 털어드림식 2차/3차 해석으로 어떻게 변형할지 한 줄\n"
    )
    try:
        resp = client.messages.create(
            model=CFG["ANALYSIS_MODEL"],
            max_tokens=2048,
            thinking={"type": "adaptive"},
            output_config={
                "format": {"type": "json_schema", "schema": VIDEO_SCHEMA},
                "effort": "medium",
            },
            system=[{
                "type": "text",
                "text": BENCHMARK_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        if not isinstance(data.get("fit_score"), int):
            data["fit_score"] = to_int(data.get("fit_score"))
        if not isinstance(data.get("benchmark_value_score"), int):
            data["benchmark_value_score"] = to_int(data.get("benchmark_value_score"))
        return data, _usage_of(resp)
    except Exception as e:
        print(f"  ⚠️ 분석 실패 [{v['title'][:30]}...]: {e}")
        return {
            "fit_score": 0,
            "recommend": False,
            "fit_reason": "(분석 실패 — 수동 확인 필요)",
            "benchmark_value_score": 0,
            "benchmark_value_reason": "(분석 실패 — 수동 확인 필요)",
            "topic_type": "(분석 실패)",
            "hook_type": "",
            "idol_tier": "",
            "risk": "분석 실패",
            "teoldeurim_angle": "",
        }, {"input": 0, "cache_write": 0, "cache_read": 0, "out": 0}


def analyze_patterns(client, candidates):
    """3섹션 패턴 분석 — 직접 공통 / 벤치마크형 공통 / 채널별 인사이트.

    candidates는 pattern_input(video_id unique 처리 완료된 리스트).
    두 축(direct_final + benchmark_final) 합쳐서 unique 처리한 표본을 기반으로
    3섹션 각각 생성. 채널별 sample_size 명시. 표본 부족 시 반드시 note에 명시.
    근거 없는 추측 금지.
    """
    # 채널별 sample_size 사전 계산 (프롬프트에 명시하여 표본 부족 안전장치)
    from collections import Counter
    channel_counts = Counter(v.get("channel_name", "?") for v in candidates)
    channel_lines = [
        f"  · {ch}: {n}개 표본" for ch, n in channel_counts.most_common()
    ]

    lines = []
    for i, v in enumerate(candidates, 1):
        a = v.get("analysis", {})
        lines.append(
            f"{i}. [{v.get('channel_name', '?')}] "
            f"fit={a.get('fit_score', 0)} / bv={a.get('benchmark_value_score', 0)} — "
            f"{v['title']}\n"
            f"   소재: {a.get('topic_type', '')} / 훅: {a.get('hook_type', '')}\n"
            f"   조회수 {v['views']:,} · 좋아요율 {fmt_pct(v['like_rate'])} · "
            f"댓글률 {fmt_pct(v['comment_rate'])} · 길이 {v['duration_sec']}초\n"
            f"   fit 이유: {a.get('fit_reason', '')}\n"
            f"   bv 이유: {a.get('benchmark_value_reason', '')}\n"
            f"   변형 각도: {a.get('teoldeurim_angle', '')}"
        )
    user_msg = (
        "아래는 '털어드림' 벤치마크 분석 대상으로 선별된 타 채널 인기 Shorts입니다.\n"
        "(direct_final + benchmark_final 두 축의 합집합, video_id 기준 unique 처리됨)\n\n"
        "## 표본 개요\n"
        f"총 표본: {len(candidates)}개\n"
        "채널별 분포:\n"
        + "\n".join(channel_lines) + "\n\n"
        "## 이 표본으로 3섹션을 생성하세요\n\n"
        + "\n\n".join(lines)
        + "\n\n## 출력할 JSON 필드 (3섹션)\n\n"
        "### A. direct_common_patterns (2~5개)\n"
        "털어드림 직접 활용도가 높은 영상(fit_score 높은 후보)들의 공통 구조.\n"
        "각 항목 {label, detail}. 제목 구조·훅·소재·반응 측면.\n\n"
        "### B. benchmark_common_patterns (2~5개)\n"
        "직접 적합도와 무관하게 훅·페르소나·관계성·팬덤 반응·유머·감정 서사 등에서 "
        "재사용 가치가 높은 패턴 (benchmark_value_score 높은 후보 중심).\n"
        "각 항목 {label, detail}. 반복 가능한 포맷·캐릭터성 강조.\n\n"
        "### C. per_channel_insights (모든 등장 채널)\n"
        "각 항목 {channel, sample_size, note, insights}.\n"
        "- channel: 채널명 (위 표본 개요 그대로)\n"
        "- sample_size: 위에 명시된 표본 수 (정확히 그 숫자)\n"
        "- note: 표본이 3개 미만이면 반드시 '분석 표본 부족 (N개) — "
        "제한적 관찰' 형식으로 명시. 표본 3개 이상이면 빈 문자열 또는 짧은 메모.\n"
        "- insights: 이 채널 특유의 패턴 (부정형 제목 / 페르소나 반복 / 팬덤 인사이더 문화 등).\n"
        "  각 인사이트는 {label, detail}. 근거 없는 추측 금지 — 반드시 표본에 실제로 관찰된 것만.\n"
        "  표본 부족 시 insights는 빈 리스트 또는 매우 제한적으로.\n\n"
        "## 필수 규칙\n"
        "1. 각 섹션은 표본 데이터에 실제 기반. 근거 없는 일반론 금지.\n"
        "2. per_channel_insights는 sample_size가 실제 표본 수와 정확히 일치해야 함.\n"
        "3. 표본 3개 미만 채널은 반드시 note에 부족 명시. insights를 억지로 채우지 말 것.\n"
        "4. 채널명은 위 표본 개요의 채널명과 정확히 일치시킬 것.\n"
    )
    try:
        resp = client.messages.create(
            model=CFG["ANALYSIS_MODEL"],
            max_tokens=6144,
            thinking={"type": "adaptive"},
            output_config={
                "format": {"type": "json_schema", "schema": PATTERN_SCHEMA},
                "effort": "high",
            },
            system=[{
                "type": "text",
                "text": BENCHMARK_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), _usage_of(resp)
    except Exception as e:
        print(f"  ⚠️ 패턴 분석 실패: {e}")
        return {"direct_common_patterns": [],
                "benchmark_common_patterns": [],
                "per_channel_insights": []}, \
               {"input": 0, "cache_write": 0, "cache_read": 0, "out": 0}


# ============================================================================
# HTML 리포트
# ============================================================================
REPORT_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
  background:#f4f4f5;color:#18181b;line-height:1.6;-webkit-font-smoothing:antialiased}
.page{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
.hero{background:#18181b;color:#fff;border-radius:16px;padding:38px 36px;margin-bottom:24px}
.hero .eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;
  color:#f5c518;font-weight:700;margin-bottom:12px}
.hero h1{font-size:30px;font-weight:900;line-height:1.25;margin-bottom:12px}
.hero h1 em{color:#f5c518;font-style:normal}
.hero p{font-size:14px;color:#d4d4d8;max-width:680px}

/* 필터 기준 */
.criteria-toggle{background:#fff;border:1px solid #e4e4e7;border-radius:10px;
  padding:10px 16px;margin-bottom:14px}
.criteria-toggle summary{font-size:13px;font-weight:700;color:#3f3f46;cursor:pointer;
  list-style:none}
.criteria-toggle summary::before{content:'▸ ';color:#8b1e3f;font-weight:900}
.criteria-toggle[open] summary::before{content:'▾ '}
.criteria-toggle .criteria{margin-top:10px;padding-top:10px;border-top:1px solid #e4e4e7}
.criteria ul{list-style:none;padding:0}
.criteria li{font-size:13px;color:#3f3f46;padding:3px 0}
.criteria li b{color:#8b1e3f;margin-right:6px}
.criteria li mark{background:#fef2f4;color:#8b1e3f;font-weight:700;padding:1px 4px;
  border-radius:4px}

/* 탭 카드 (상단) */
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 14px}
.stat{flex:1;min-width:130px;background:#fff;border:1px solid #e4e4e7;
  border-radius:12px;padding:14px 16px;cursor:pointer;text-align:left;
  font-family:inherit;transition:all 0.15s;color:inherit}
.stat:hover{border-color:#8b1e3f;transform:translateY(-1px)}
.stat.active{border-color:#8b1e3f;background:#fef2f4}
.stat.primary{background:#18181b;border-color:#18181b}
.stat.primary:hover{border-color:#f5c518}
.stat.primary.active{background:#27272a;border-color:#f5c518}
.stat-n{font-size:26px;font-weight:900;color:#18181b}
.stat.primary .stat-n{color:#f5c518}
.stat-k{font-size:12px;color:#71717a;margin-top:2px}
.stat.primary .stat-k{color:#d4d4d8}

/* 검색창 */
.search-bar{display:flex;align-items:center;gap:12px;margin:14px 0 18px;
  padding:10px 14px;background:#fff;border:1px solid #e4e4e7;border-radius:10px}
.search-input{flex:1;border:none;outline:none;font-size:14px;
  font-family:inherit;color:#18181b;background:transparent}
.search-input::placeholder{color:#a1a1aa}
.search-count{font-size:12px;color:#71717a;white-space:nowrap}
.search-count b{color:#18181b}

/* 스테이지 패널 */
.stage-panel-head{display:flex;align-items:baseline;gap:10px;margin:6px 0 4px}
.stage-panel-head h3{font-size:17px;font-weight:800;color:#18181b}
.stage-panel-head .cnt{font-size:12px;color:#71717a}
.stage-panel-desc{font-size:12px;color:#71717a;margin-bottom:14px}

/* 컴팩트 행 (.si) — 텍스트 중심, 썸네일 없음 */
.si-list{display:flex;flex-direction:column;gap:6px}
.si{background:#fff;border:1px solid #e4e4e7;border-radius:8px;
  padding:10px 14px;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.si:hover{border-color:#8b1e3f;background:#fafafa}
.si-rank{font-size:12px;font-weight:800;color:#a1a1aa;min-width:24px}
.si-fit{font-size:11px;font-weight:800;color:#fff;padding:2px 8px;border-radius:12px}
.si-fit.hi{background:#15803d}.si-fit.mid{background:#b45309}.si-fit.lo{background:#71717a}
.si-title{font-size:14px;font-weight:700;color:#18181b;text-decoration:none;
  flex:1;min-width:160px}
.si-title:hover{color:#8b1e3f}
.si-meta{font-size:11px;color:#71717a;white-space:nowrap}
.si-reason{font-size:11px;background:#fef2f4;color:#8b1e3f;
  border:1px solid #fee2e7;padding:2px 8px;border-radius:6px;font-weight:700}
.si-sent{font-size:11px;background:#fef9c3;color:#854d0e;
  border:1px solid #fde68a;padding:2px 8px;border-radius:6px;font-weight:700}

/* JS / 가시성 토글 */
.is-hidden{display:none !important}
.js-only{display:none}
html.js .js-only{display:inline}

/* 더보기 버튼 (긴 리스트 페이지네이션) */
.more-btn{display:block;margin:14px auto 4px;padding:10px 22px;
  background:#fff;border:1px solid #e4e4e7;border-radius:8px;
  font-size:13px;font-weight:700;color:#3f3f46;cursor:pointer;
  font-family:inherit;transition:all 0.15s}
.more-btn:hover{border-color:#8b1e3f;color:#8b1e3f;background:#fef2f4}

/* 섹션 헤더 */
.section-head{display:flex;align-items:baseline;gap:12px;margin:38px 0 6px}
.section-head .tag{font-size:11px;font-weight:800;letter-spacing:.06em;
  background:#8b1e3f;color:#fff;padding:4px 10px;border-radius:6px}
.section-head h2{font-size:21px;font-weight:900}
.section-desc{font-size:13px;color:#71717a;margin-bottom:18px}

/* 최종 후보 카드 (기존) */
.card{background:#fff;border:1px solid #e4e4e7;border-radius:14px;
  padding:20px 22px;margin-bottom:14px;display:flex;gap:18px}
.card .rank{font-size:22px;font-weight:900;color:#8b1e3f;min-width:34px}
.card .thumb{width:120px;height:auto;border-radius:8px;flex-shrink:0;
  aspect-ratio:9/16;object-fit:cover;background:#e4e4e7}
.card .body{flex:1;min-width:0}
.card .title{font-size:16px;font-weight:800;line-height:1.4;margin-bottom:6px}
.card .title a{color:#18181b;text-decoration:none}
.card .title a:hover{color:#8b1e3f}
.card .submeta{font-size:12px;color:#71717a;margin-bottom:10px}
.card .submeta b{color:#8b1e3f}
.fit{display:inline-block;font-size:13px;font-weight:900;color:#fff;
  padding:3px 11px;border-radius:20px;margin-bottom:8px;margin-right:6px;
  vertical-align:middle}
.fit.hi{background:#15803d}.fit.mid{background:#b45309}.fit.lo{background:#71717a}

/* 벤치마크 가치 primary badge — 파란 계열 (fit과 색상 구분) */
.bv{display:inline-block;font-size:13px;font-weight:900;color:#fff;
  padding:3px 11px;border-radius:20px;margin-bottom:8px;margin-right:6px;
  vertical-align:middle}
.bv.hi{background:#1e5f8b}.bv.mid{background:#6b9dc4}.bv.lo{background:#a1a1aa}

/* 보조 점수 badge — 회색 참고용 (다른 축 점수 표시) */
.score-sec{display:inline-block;font-size:11px;font-weight:700;color:#71717a;
  background:#f4f4f5;border:1px solid #e4e4e7;padding:2px 8px;border-radius:12px;
  margin-right:6px;vertical-align:middle}

/* Section 2 — 3섹션 기획 포인트 헤더 */
.pat-section-h{font-size:16px;font-weight:800;color:#8b1e3f;
  margin:24px 0 6px;padding-top:12px;border-top:2px solid #e4e4e7}
.pat-section-h:first-of-type{border-top:none;padding-top:0}
.pat-section-desc{font-size:12px;color:#71717a;margin-bottom:12px}

/* C. 채널별 인사이트 카드 */
.ch-card{background:#fff;border:1px solid #e4e4e7;border-radius:12px;
  padding:14px 18px;margin-bottom:10px}
.ch-card.low-sample{background:#fefce8;border-color:#facc15}
.ch-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.ch-name{font-size:14px;color:#18181b}
.ch-sample{font-size:11px;font-weight:700;color:#71717a;
  background:#f4f4f5;border:1px solid #e4e4e7;padding:2px 8px;border-radius:10px}
.ch-sample.low{color:#a16207;background:#fef9c3;border-color:#eab308}
.ch-note{font-size:12px;color:#a16207;font-weight:600;margin-bottom:8px}
.ch-insight{margin:6px 0;padding:8px 10px;background:#fafaf9;
  border-left:3px solid #8b1e3f;border-radius:0 6px 6px 0}
.ci-label{font-size:13px;font-weight:700;color:#8b1e3f}
.ci-detail{font-size:12px;color:#3f3f46;margin-top:2px}
.ch-empty{font-size:12px;color:#a1a1aa;font-style:italic;padding:4px 0}
.metrics{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}
.metrics .m{font-size:11px;background:#f4f4f5;border:1px solid #e4e4e7;
  border-radius:6px;padding:4px 9px;color:#3f3f46}
.metrics .m b{color:#18181b}
.field{font-size:13px;margin:6px 0;color:#3f3f46}
.field .lab{font-weight:800;color:#8b1e3f;margin-right:6px}
.angle{background:#fef2f4;border-left:3px solid #8b1e3f;padding:9px 12px;
  border-radius:0 8px 8px 0;font-size:13px;margin-top:10px}
.tags{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
.tags .t{font-size:11px;background:#18181b;color:#fff;padding:3px 9px;border-radius:6px}
.tags .t.warn{background:#b45309}

/* Section 2 (기획 포인트) */
.pat{background:#fff;border:1px solid #e4e4e7;border-radius:12px;
  padding:16px 20px;margin-bottom:12px}
.pat .pl{font-size:14px;font-weight:800;color:#8b1e3f;margin-bottom:4px}
.pat .pd{font-size:13px;color:#3f3f46}
.plan{background:#18181b;color:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px}
.plan .pt{font-size:15px;font-weight:800;color:#f5c518;margin-bottom:5px}
.plan .pd{font-size:13px;color:#d4d4d8}

.footer{margin-top:40px;padding-top:18px;border-top:1px solid #e4e4e7;
  font-size:12px;color:#a1a1aa;display:flex;justify-content:space-between;flex-wrap:wrap}
.empty{background:#fff;border:1px dashed #d4d4d8;border-radius:12px;
  padding:30px;text-align:center;color:#a1a1aa;font-size:14px}
@media(max-width:640px){
  .card{flex-direction:column}.card .thumb{width:100%;max-width:200px}
  .stats{gap:6px}.stat{min-width:calc(50% - 3px);flex:0 0 calc(50% - 3px)}
  .si{font-size:13px}.si-meta{flex-basis:100%;margin-left:24px}
}
"""


def _fit_class(score):
    if score >= 80:
        return "hi"
    if score >= 60:
        return "mid"
    return "lo"


def _render_candidate(rank, v, mode="direct"):
    """카드 렌더 — mode에 따라 primary 점수와 primary 사유가 달라짐.

    mode="direct":    fit_score badge primary, fit_reason 강조
    mode="benchmark": benchmark_value_score badge primary, benchmark_value_reason 강조

    26.07.14 정책 변경: direct_final과 benchmark_final은 상호 배타적 (dedup 후 승격).
    이전의 "다른 리스트에도 선정됨" dup badge는 더 이상 발생하지 않아 제거.
    """
    a = v.get("analysis", {})
    fit_score = a.get("fit_score", 0)
    bv_score = a.get("benchmark_value_score", 0)
    days = v.get("days_since_upload")
    days_str = f"{days}일 전" if days is not None else "업로드일 불명"
    thumb = (f'<img class="thumb" src="{esc_html(v["thumbnail"])}" alt="">'
             if v.get("thumbnail") else "")
    metrics = "".join(f'<span class="m">{lab} <b>{val}</b></span>' for lab, val in [
        ("조회수", fmt_int(v["views"])),
        ("좋아요율", fmt_pct(v["like_rate"])),
        ("댓글률", fmt_pct(v["comment_rate"])),
        ("반응률", fmt_pct(v["engagement_rate"])),
        ("구독자대비", f"{v['views_per_sub']:.2f}배"),
        ("길이", f"{v['duration_sec']}초"),
        ("업로드", days_str),
    ])
    risk = a.get("risk", "").strip()
    risk_tag = (f'<span class="t warn">⚠ {esc_html(risk)}</span>'
                if risk and risk not in ("없음", "") else "")
    raw_url = (v.get("url") or "").strip()
    if raw_url and raw_url != "#":
        title_el = f'<a href="{esc_html(raw_url)}">{esc_html(v["title"])}</a>'
    else:
        title_el = esc_html(v["title"])  # 비클릭 plain text

    # primary vs secondary badge 결정 (mode별)
    if mode == "benchmark":
        primary_badge = (f'<span class="bv {_fit_class(bv_score)}">'
                         f'벤치마크 가치 {bv_score}</span>')
        secondary_badge = (f'<span class="score-sec">'
                           f'직접 적합도 {fit_score}</span>')
        primary_reason_label = "왜 참고 가치 있는가"
        primary_reason = a.get("benchmark_value_reason", "") or "(사유 누락)"
    else:  # direct (default)
        primary_badge = (f'<span class="fit {_fit_class(fit_score)}">'
                         f'적합도 {fit_score}</span>')
        secondary_badge = (f'<span class="score-sec">'
                           f'벤치마크 가치 {bv_score}</span>')
        primary_reason_label = "왜 쓸 만한가"
        primary_reason = a.get("fit_reason", "")

    return f"""
  <div class="card">
    <div class="rank">{rank:02d}</div>
    {thumb}
    <div class="body">
      {primary_badge}{secondary_badge}
      <div class="title">{title_el}</div>
      <div class="submeta"><b>{esc_html(v['channel_name'])}</b>
        &nbsp;·&nbsp; 구독자 {fmt_int(v['subscribers'])}</div>
      <div class="metrics">{metrics}</div>
      <div class="field"><span class="lab">{primary_reason_label}</span>{esc_html(primary_reason)}</div>
      <div class="field"><span class="lab">소재 유형</span>{esc_html(a.get('topic_type',''))}</div>
      <div class="tags">
        <span class="t">훅: {esc_html(a.get('hook_type',''))}</span>
        <span class="t">{esc_html(a.get('idol_tier',''))}</span>
        {risk_tag}
      </div>
      <div class="angle"><b>▸ 털어드림식 변형 각도</b><br>{esc_html(a.get('teoldeurim_angle',''))}</div>
    </div>
  </div>"""


# 탭 정의: (key, label, title_template, desc)
_BENCHMARK_STAGE_TABS = [
    ("collected",          "수집 전체",
     "수집 전체 ({n}개)",
     "Apify로 수집된 후 제외채널 필터를 통과한 모든 영상. "
     "(MAX_VIEWS 초과·Hard 컷·기발송 제외 영상 포함 — 분석 대상은 별도 탭으로 분리)"),
    ("max_views_excluded", "조회수 초과 제외",
     "조회수 {max_views_label} 초과로 제외 ({n}개)",
     "대표님 요청 기준 — 너무 뻔한 초대형 조회수 영상은 후보에서 제외합니다. "
     "분리만 했고 데이터는 보관되어 있습니다."),
    ("hard_excluded",      "Hard 컷",
     "Hard 필터 컷 ({n}개)",
     "조회수 하한·길이·업로드 기간 기준으로 컷된 영상 (제외 사유 표기)."),
    ("analyzed",           "Claude 분석",
     "Claude 분석 대상 ({n}개)",
     "Hard 통과 영상 중 상위를 Claude로 적합도 분석한 풀 (기발송 포함)."),
    ("sent_excluded",      "기발송 제외",
     "weekly 기발송 영상 ({n}개)",
     "history에서 발견된 weekly 발송 이력 — 분석은 했지만 후보 리스트에서 제외됨."),
    ("direct_final",       "직접 후보 TOP {n}",
     "직접 후보 TOP {n}",
     "털어드림에 그대로 또는 2차/3차 해석으로 변형해 활용하기 좋은 후보 "
     "(fit_score 상위, 기발송 제외)."),
    ("benchmark_final",    "벤치마크 가치 TOP {n}",
     "벤치마크 가치 TOP {n}",
     "직접 후보 적합도와 무관하게 소재·훅·제목 구조·관계성·페르소나·팬덤 반응 측면에서 "
     "재사용 가치가 높은 후보 (benchmark_value_score 상위, 기발송 제외). "
     "직접 후보 TOP과 중복되는 영상은 제외되어 별도 신규 후보만 표시."),
]


def _fmt_max_views(n):
    """4_000_000 → '400만' 형식 (없으면 빈 문자열)."""
    if not n:
        return ""
    if n >= 10_000:
        return f"{n // 10_000:,}만"
    return f"{n:,}"


def _si_search_text(v):
    """컴팩트 행 검색용 문자열 (소문자, 공백 구분)."""
    bits = [
        v.get("title", ""),
        v.get("channel_name", ""),
        v.get("exclusion_reason", ""),
    ]
    a = v.get("analysis") or {}
    if isinstance(a, dict):
        bits.extend([a.get("topic_type", ""), a.get("hook_type", ""),
                     a.get("idol_tier", ""), a.get("risk", "")])
    return " ".join(b for b in bits if b).lower()


def _render_si_row(rank, v):
    """컴팩트 행 (썸네일 없음, 텍스트 중심)."""
    days = v.get("days_since_upload")
    days_str = f"{days}일 전" if days is not None else "?"
    reason = (v.get("exclusion_reason") or "").strip()
    reason_html = (f'<span class="si-reason">{esc_html(reason)}</span>'
                   if reason else "")
    sent_html = ('<span class="si-sent">기발송</span>'
                 if v.get("already_sent") else "")
    fit_html = ""
    a = v.get("analysis") or {}
    if isinstance(a, dict) and a.get("fit_score") is not None:
        s = a.get("fit_score", 0)
        fit_html = f'<span class="si-fit {_fit_class(s)}">적합도 {s}</span>'
    search = _si_search_text(v)
    title = esc_html(v.get("title", "(제목 없음)"))
    channel = esc_html(v.get("channel_name", ""))
    views_str = fmt_int(v.get("views", 0))
    dur = v.get("duration_sec", 0)
    raw_url = (v.get("url") or "").strip()
    # URL이 비었거나 placeholder("#")면 클릭 가능한 <a> 대신 <span>으로 렌더
    if raw_url and raw_url != "#":
        title_el = f'<a class="si-title" href="{esc_html(raw_url)}">{title}</a>'
    else:
        title_el = f'<span class="si-title">{title}</span>'
    return (
        f'<div class="si" data-search="{esc_html(search)}">'
        f'<span class="si-rank">{rank:02d}</span>'
        f'{fit_html}'
        f'{title_el}'
        f'<span class="si-meta">{channel} · {views_str}회 · {dur}초 · {days_str}</span>'
        f'{reason_html}{sent_html}'
        f'</div>'
    )


def _render_stage_body(key, items):
    """탭 패널 본문 — direct_final/benchmark_final은 카드, 그 외는 컴팩트 리스트.

    각 행은 모두 렌더링하되, JS가 INITIAL=30개만 표시하고 나머지는 숨김.
    '더보기' 버튼 클릭 시 STEP=50개씩 추가 노출 (weekly.py 패턴).

    26.07.14 정책 변경: direct_final과 benchmark_final은 상호 배타적 (dedup 후 승격)
    이라 dup 참고 badge 로직 제거됨.
    """
    if not items:
        return '<div class="empty">해당 단계의 영상이 없습니다.</div>'
    if key in ("direct_final", "benchmark_final", "final"):
        mode = "benchmark" if key == "benchmark_final" else "direct"
        return "".join(
            _render_candidate(i, v, mode=mode)
            for i, v in enumerate(items, 1)
        )
    rows = "".join(_render_si_row(i, v) for i, v in enumerate(items, 1))
    return f'<div class="si-list">{rows}</div>'


def render_report(stage_data, patterns, today_label, ref_channels, config_used):
    """탭 UI 형태의 벤치마크 리포트 HTML 생성.

    stage_data: dict
      "collected", "max_views_excluded", "hard_excluded",
      "analyzed", "sent_excluded", "final" 각각의 영상 리스트
    config_used: filter 기준 표시용 config 스냅샷
    """
    ref_bits = []
    for r in ref_channels:
        mark = " <span style='color:#f5c518'>(자동)</span>" if r.get("_auto") else ""
        ref_bits.append(esc_html(r.get("name", "?")) + mark)
    ref_names = ", ".join(ref_bits)

    max_views = config_used.get("MAX_VIEWS", 0)
    max_views_label = _fmt_max_views(max_views)
    min_views = config_used.get("MIN_VIEWS", 0)
    max_dur = config_used.get("MAX_DURATION_SEC", 0)
    min_dur = config_used.get("MIN_DURATION_SEC", 0)
    within_days = config_used.get("UPLOADED_WITHIN_DAYS", 0)
    # 사용자-facing 업로드 age 범위 문구 — 실제 local timestamp 필터 기준 반영.
    # (내부 API pull buffer 는 노출 안 함)
    _age_min = config_used.get("MIN_AGE_DAYS_EXCLUSIVE")
    if _age_min is None:
        _age_head = "업로드 직후"
    else:
        _age_head = f"{_age_min}일 초과"
    _age_tail = f"{within_days}일 이내" if within_days else "제한 없음"
    age_range_label = f"{_age_head} ~ {_age_tail}"

    default_stage = "direct_final"  # 기본 탭 = 직접 후보 (기존 "final" 대체)

    stat_buttons = []
    panels = []
    for key, label, ptitle_tmpl, pdesc in _BENCHMARK_STAGE_TABS:
        items = stage_data.get(key, []) or []
        n = len(items)
        primary = " primary" if key == "direct_final" else ""
        active = " active" if key == default_stage else ""
        # label에도 {n} 플레이스홀더가 들어갈 수 있음 (예: "직접 후보 TOP {n}")
        label_rendered = label.format(n=n) if "{n}" in label else label
        stat_buttons.append(
            f'<button class="stat{primary}{active}" data-stage="{key}" type="button">'
            f'<div class="stat-n">{n}</div>'
            f'<div class="stat-k">{esc_html(label_rendered)}</div>'
            f'</button>'
        )
        ptitle = ptitle_tmpl.format(n=n, max_views_label=max_views_label)
        body = _render_stage_body(key, items)
        hidden = "" if key == default_stage else " is-hidden"
        # 더보기 버튼 — JS가 초기 30개 초과분에 대해 동적으로 노출/숨김 처리
        more_btn = '<button class="more-btn is-hidden" type="button">더보기</button>'
        panels.append(
            f'<div class="stage-panel{hidden}" data-stage="{key}">'
            f'<div class="stage-panel-head">'
            f'<h3>{esc_html(ptitle)}</h3>'
            f'<span class="cnt">총 {n}개</span>'
            f'</div>'
            f'<p class="stage-panel-desc">{esc_html(pdesc)}</p>'
            f'{body}'
            f'{more_btn}'
            f'</div>'
        )

    # 3섹션 기획 포인트 리포트 (Phase 4 개편)
    # A. 직접 후보 공통 패턴 (fit_score 축)
    def _render_pat_list(items, empty_msg):
        if not items:
            return f'<div class="empty">{esc_html(empty_msg)}</div>'
        return "".join(
            f'<div class="pat"><div class="pl">{esc_html(p.get("label",""))}</div>'
            f'<div class="pd">{esc_html(p.get("detail",""))}</div></div>'
            for p in items)

    # 하위 호환: 구버전 patterns dict가 "common_patterns"/"planning_points"만 있는 경우
    # 3섹션 스키마로 폴백 매핑 (기존 filtered_raw 재렌더 대비)
    if "direct_common_patterns" not in patterns and "common_patterns" in patterns:
        patterns = {
            "direct_common_patterns": patterns.get("common_patterns", []),
            "benchmark_common_patterns": [],
            "per_channel_insights": [],
        }

    direct_pat_html = _render_pat_list(
        patterns.get("direct_common_patterns", []),
        "도출된 직접 후보 공통 패턴이 없습니다.")
    bench_pat_html = _render_pat_list(
        patterns.get("benchmark_common_patterns", []),
        "도출된 벤치마크형 공통 패턴이 없습니다.")

    # C. 채널별 인사이트 — 표본 부족 시 note 강조
    channel_insights = patterns.get("per_channel_insights", []) or []
    if channel_insights:
        ch_cards = []
        for entry in channel_insights:
            ch = esc_html(entry.get("channel", "?"))
            n = entry.get("sample_size", 0)
            note = (entry.get("note") or "").strip()
            insights = entry.get("insights", []) or []
            # 표본 부족 배지 (샘플 3 미만이면 강조)
            low_sample = (n < 3)
            sample_badge = (
                f'<span class="ch-sample low">표본 {n}개 · 부족</span>'
                if low_sample else
                f'<span class="ch-sample">표본 {n}개</span>'
            )
            note_html = (f'<div class="ch-note">{esc_html(note)}</div>'
                         if note else "")
            if insights:
                ins_html = "".join(
                    f'<div class="ch-insight">'
                    f'<div class="ci-label">{esc_html(ii.get("label",""))}</div>'
                    f'<div class="ci-detail">{esc_html(ii.get("detail",""))}</div>'
                    f'</div>'
                    for ii in insights)
            else:
                ins_html = ('<div class="ch-empty">'
                            '표본 부족 — 근거 있는 인사이트 없음'
                            '</div>')
            ch_cards.append(
                f'<div class="ch-card{" low-sample" if low_sample else ""}">'
                f'<div class="ch-head"><b class="ch-name">{ch}</b>{sample_badge}</div>'
                f'{note_html}'
                f'{ins_html}'
                f'</div>'
            )
        channel_html = "".join(ch_cards)
    else:
        channel_html = ('<div class="empty">'
                        '도출된 채널별 인사이트가 없습니다.</div>')

    max_views_li = (f'<li><b>조회수</b> {min_views:,} 이상 ~ '
                    f'<mark>{max_views:,} 이하</mark> (대표님 요청 — 뻔한 영상 컷)</li>'
                    if max_views else
                    f'<li><b>조회수</b> {min_views:,} 이상</li>')

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<base target="_blank">
<title>털어드림 · 타 채널 벤치마크 리포트 {today_label}</title>
<style>{REPORT_CSS}</style>
</head><body><div class="page bm-root" data-report="benchmark">
  <section class="hero">
    <div class="eyebrow">Competitor Benchmark Report · {today_label}</div>
    <h1>털어드림 <em>타 채널 벤치마크</em><br>참고 후보 & 기획 포인트</h1>
    <p>외부 참고 채널의 인기 Shorts를 수집해 털어드림에 쓸 만한 후보를 골라
       순위를 매기고, 공통 패턴에서 기획 포인트를 뽑았습니다.<br>
       <b style="color:#fff">참고 채널:</b> {ref_names or "(없음)"}
       <span class="js-only"><br><b style="color:#f5c518">▸ 상단 카드를 클릭하면 단계별 영상 리스트를 볼 수 있습니다.</b></span></p>
  </section>

  <details class="criteria-toggle">
    <summary>필터 기준 보기</summary>
    <div class="criteria">
      <ul>
        {max_views_li}
        <li><b>길이</b> {(str(min_dur)+"초 이상 ~ ") if min_dur else ""}{max_dur}초 이하 (Shorts 형식)</li>
        <li><b>업로드</b> {age_range_label}</li>
        <li><b>중복</b> weekly 기발송 영상 자동 dedup (history 기반)</li>
        <li><b>채널</b> 제외 채널 자동 차단 (우리 채널 + blocklist)</li>
      </ul>
    </div>
  </details>

  <div class="stats" id="bmStatTabs">
    {''.join(stat_buttons)}
  </div>

  <div class="search-bar">
    <input type="text" id="bmSearchInput" class="search-input"
           placeholder="현재 선택한 리스트에서 제목·채널·사유 검색" autocomplete="off">
    <span class="search-count" id="bmSearchCount"></span>
  </div>

  <div id="bmPanelWrap">
    {''.join(panels)}
  </div>

  <div class="section-head" style="margin-top:48px">
    <span class="tag">SECTION 2</span>
    <h2>기획 포인트 리포트 (3섹션)</h2>
  </div>
  <p class="section-desc">
    direct_final + benchmark_final 합집합(video_id unique)에서 도출.
    직접 활용 관점 / 벤치마크 재사용 관점 / 채널별 관점 3섹션.
  </p>

  <h3 class="pat-section-h">A. 직접 후보 공통 패턴</h3>
  <p class="pat-section-desc">
    털어드림 직접 활용도가 높은 영상(fit_score 상위)의 공통 구조.
  </p>
  {direct_pat_html}

  <h3 class="pat-section-h">B. 벤치마크형 공통 패턴</h3>
  <p class="pat-section-desc">
    직접 적합도와 무관하게 훅·페르소나·관계성·팬덤 반응 등에서 재사용 가치가 높은 패턴.
  </p>
  {bench_pat_html}

  <h3 class="pat-section-h">C. 채널별 인사이트</h3>
  <p class="pat-section-desc">
    각 참고 채널의 표본 기반 관찰. 표본 3개 미만 채널은 "부족" 배지로 표시되며,
    근거 없는 추측 없이 실제 관찰만 반영.
  </p>
  {channel_html}

  <div class="footer">
    <span>털어드림 · 타 채널 벤치마크 모듈 (보조)</span>
    <span>Apify · Claude Opus 4.7 · {today_label} KST</span>
  </div>
</div>
<script>
(function() {{
  // JS 활성화 표시 (CSS의 .js-only가 노출됨)
  document.documentElement.classList.add('js');
  // 통합 shell에서 다른 report(weekly)와 격리되도록 bm-root scope로 제한.
  var root = (document.currentScript && document.currentScript.closest('.bm-root'))
             || document.querySelector('.bm-root')
             || document.body;
  var INITIAL = 30, STEP = 50;  // 처음엔 30개 노출, '더보기' 클릭 시 +50
  var tabs = root.querySelectorAll('#bmStatTabs .stat');
  var panels = root.querySelectorAll('.stage-panel');
  var searchInput = root.querySelector('#bmSearchInput');
  var searchCount = root.querySelector('#bmSearchCount');
  var shownLimit = {{}};  // stage -> 현재 표시 한도

  function activePanel() {{
    for (var i = 0; i < panels.length; i++) {{
      if (!panels[i].classList.contains('is-hidden')) return panels[i];
    }}
    return panels[0];
  }}

  function items(panel) {{
    // 컴팩트 행은 .si, 최종 후보 카드는 .card
    var nodes = panel.querySelectorAll('.si, .card');
    return Array.prototype.slice.call(nodes);
  }}

  function render() {{
    var panel = activePanel();
    var stage = panel.getAttribute('data-stage');
    var q = (searchInput.value || '').trim().toLowerCase();
    var all = items(panel);
    var matched = [];
    for (var i = 0; i < all.length; i++) {{
      var hay = all[i].getAttribute('data-search') || '';
      // 카드에는 data-search가 없을 수 있으니 텍스트 추출 fallback
      if (!hay) {{
        var tEl = all[i].querySelector('.title') || all[i].querySelector('.si-title');
        hay = (tEl ? tEl.textContent : all[i].textContent || '').toLowerCase();
      }}
      if (!q || hay.indexOf(q) !== -1) matched.push(all[i]);
    }}
    // 표시 한도: 검색 중엔 매치 전부, 평시엔 INITIAL + STEP*click
    var limit = q ? matched.length : (shownLimit[stage] || INITIAL);
    var shownN = 0;
    for (var j = 0; j < all.length; j++) all[j].classList.add('is-hidden');
    for (var k = 0; k < matched.length && k < limit; k++) {{
      matched[k].classList.remove('is-hidden');
      shownN++;
    }}
    // 더보기 버튼 노출/숨김
    var moreBtn = panel.querySelector('.more-btn');
    if (moreBtn) {{
      if (!q && matched.length > shownN) {{
        moreBtn.classList.remove('is-hidden');
        moreBtn.textContent = '더보기 (' + (matched.length - shownN) + '개 남음)';
      }} else {{
        moreBtn.classList.add('is-hidden');
      }}
    }}
    // 검색 카운트
    if (q) {{
      searchCount.innerHTML = (matched.length > 0)
        ? ('전체 ' + all.length + '개 중 <b>' + matched.length + '개</b> 표시')
        : ('<b>0개</b> — \\u2018' + q.replace(/</g,'&lt;') + '\\u2019 검색 결과 없음');
    }} else {{
      var moreInfo = (matched.length > shownN)
        ? ' (현재 ' + shownN + '개 노출)' : '';
      searchCount.innerHTML = '전체 <b>' + all.length + '개</b>' + moreInfo;
    }}
  }}

  function switchTab(stage) {{
    for (var i = 0; i < tabs.length; i++) {{
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-stage') === stage);
    }}
    for (var j = 0; j < panels.length; j++) {{
      panels[j].classList.toggle('is-hidden', panels[j].getAttribute('data-stage') !== stage);
    }}
    searchInput.value = '';
    render();
  }}

  for (var t = 0; t < tabs.length; t++) {{
    (function(tab) {{
      tab.addEventListener('click', function() {{
        switchTab(tab.getAttribute('data-stage'));
      }});
    }})(tabs[t]);
  }}

  // 더보기 버튼 클릭 핸들러
  for (var p = 0; p < panels.length; p++) {{
    (function(panel) {{
      var moreBtn = panel.querySelector('.more-btn');
      if (moreBtn) {{
        moreBtn.addEventListener('click', function() {{
          var stage = panel.getAttribute('data-stage');
          shownLimit[stage] = (shownLimit[stage] || INITIAL) + STEP;
          render();
        }});
      }}
    }})(panels[p]);
  }}

  searchInput.addEventListener('input', render);
  render();  // 초기 렌더 (final 탭)
}})();
</script>
</body></html>"""


# ============================================================================
# main
# ============================================================================
def main():
    load_dotenv()
    # 실제 실행 시각(=KST 오늘) — age 계산·필터·generated_at·API 기준 시각에 사용.
    #   REPORT_DATE가 있어도 now_kst / now_utc 는 절대 override 되지 않는다.
    now_kst = datetime.now(KST)
    now_utc = datetime.now(timezone.utc)
    # ── REPORT_DATE override — 원래 발송 예정 슬롯 (파일명·리포트 헤더 slot 표기용) ──
    _report_date_env = os.environ.get("REPORT_DATE", "").strip()
    if _report_date_env:
        # 엄격 YYYY-MM-DD (zero-padded)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _report_date_env):
            print(f"❌ REPORT_DATE 형식 오류: {_report_date_env!r} — "
                  f"정확한 YYYY-MM-DD (zero-padded) 필요", file=sys.stderr)
            sys.exit(1)
        try:
            datetime.strptime(_report_date_env, "%Y-%m-%d")
        except ValueError:
            print(f"❌ REPORT_DATE 형식 오류: {_report_date_env!r} — 존재하지 않는 날짜",
                  file=sys.stderr)
            sys.exit(1)
        today_label = _report_date_env   # slot — raw_path / report_path / HTML 헤더에 사용
    else:
        today_label = now_kst.strftime("%Y-%m-%d")

    # PROFILE env 반영 (default = "standard")
    profile = (os.environ.get("PROFILE") or "standard").strip().lower()
    profile_cfg = benchmark_config.resolve_config(profile)
    CFG.clear()
    CFG.update(profile_cfg)

    print("=" * 60)
    print("털어드림 타 채널 벤치마크 모듈")
    print(f"실행 시각 (KST): {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"프로파일: {profile}  "
          f"(SORT_BY={CFG['SORT_BY']}, "
          f"조회수 {CFG['MIN_VIEWS']:,}~{CFG['MAX_VIEWS']:,}, "
          f"업로드 {CFG['UPLOADED_WITHIN_DAYS']}일)")
    print("=" * 60)

    apify_token = os.environ.get("APIFY_TOKEN", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not apify_token:
        print("❌ APIFY_TOKEN 환경변수가 없습니다 (.env 또는 시크릿 확인).", file=sys.stderr)
        sys.exit(1)
    if not anthropic_key:
        print("❌ ANTHROPIC_API_KEY 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    # ── STEP 0. 참고 채널 구성 + 검증 ──
    print("\n[STEP 0] 참고 채널 구성 (수동 + history 자동 추출)")
    refs = build_reference_channels()
    manual_channels = refs["manual"]
    auto_discovered_reference_candidates = refs["auto_discovered_reference_candidates"]
    ref_channels = refs["merged"]
    weekly_sent_ids = refs["sent_video_ids"]  # weekly history read-only
    print(f"  수동 {len(manual_channels)}개 + 자동 "
          f"{len(auto_discovered_reference_candidates)}개 → 병합 {len(ref_channels)}개")
    for r in ref_channels:
        tag = "자동" if r.get("_auto") else "수동"
        extra = (f" (점수 {r.get('_score')}, {r.get('_weeks')}주 등장)"
                 if r.get("_auto") else "")
        print(f"   [{tag}] {r.get('name', '?')}{extra}")
    # Apify 수집 직전 최종 검증
    validate_reference_channels(ref_channels)

    # benchmark 공용 sent history 로드 (recent + standard 통합 dedup)
    # → orchestrator(send_report.py)가 발송 성공 후에만 이 디렉터리에 파일을 쓴다.
    benchmark_sent_ids = load_benchmark_sent_history()
    # 최종 dedup 대상 = weekly history ∪ benchmark 공용 sent history
    sent_video_ids = weekly_sent_ids | benchmark_sent_ids
    print(f"  기발송 dedup: weekly {len(weekly_sent_ids)}개 "
          f"+ benchmark {len(benchmark_sent_ids)}개 → 합집합 {len(sent_video_ids)}개")

    # ── STEP 1. Apify 수집 ──
    print("\n[STEP 1] Apify로 참고 채널 인기 Shorts 수집")
    channels = []
    for ref in ref_channels:
        c = str(ref.get("channel", "")).strip()
        if c.startswith("@"):
            c = c[1:]
        if c:
            channels.append(c)
    raw_items = apify_collect(apify_token, channels,
                              CFG["MAX_SHORTS_PER_CHANNEL"], CFG["SORT_BY"])

    videos = [normalize_video(it) for it in raw_items]
    # 중복 영상 제거 (video_id 기준)
    seen, deduped = set(), []
    for v in videos:
        key = v["video_id"] or v["url"]
        if key and key in seen:
            continue
        seen.add(key)
        deduped.append(v)
    print(f"  정규화: {len(videos)}개 → 중복 제거 후 {len(deduped)}개")

    # ── STEP 2. 제외 채널 필터링 (안전망) ──
    print("\n[STEP 2] 제외 채널 필터링")
    kept, removed = filter_excluded_channels(deduped)
    if removed:
        # 참고 채널에 제외 채널이 숨어 있었던 것 → 저장/리포트 진행하지 않고 중단
        print(f"❌ {ABORT_EXCLUDE_MSG}.", file=sys.stderr)
        print(f"   수집 데이터에서 제외 채널 소속 영상 {len(removed)}개가 발견됨:", file=sys.stderr)
        for v, why in removed[:10]:
            print(f"   - {v['channel_name']} / {v['title'][:40]} → {why}", file=sys.stderr)
        print("   filtered_raw 저장 및 리포트 생성을 진행하지 않습니다.", file=sys.stderr)
        print("   config/benchmark_config.py의 REFERENCE_CHANNELS를 점검하세요.",
              file=sys.stderr)
        sys.exit(1)
    print(f"  ✅ 제외 채널 없음 — {len(kept)}개 통과")

    if not kept:
        print("⚠️ 수집된 영상이 없습니다. 종료합니다.")
        sys.exit(0)

    # 전체 수집 풀 스냅샷 — "수집 전체" 탭이 가리키는 데이터
    # (Apify가 반환하고 제외채널 필터를 통과한 모든 영상의 superset.
    #  이후 MAX_VIEWS 초과 분리 / Hard 컷이 이 풀의 부분집합으로 나타남)
    all_collected_pool = list(kept)

    # ── STEP 2.5. MAX_VIEWS 필터 (대표님 요청: 뻔한 영상 제외) ──
    print(f"\n[STEP 2.5] 조회수 상한 필터 (MAX_VIEWS={CFG.get('MAX_VIEWS', 0):,})")
    kept, max_views_excluded = apply_max_views_filter(kept)
    print(f"  상한 초과 컷: {len(max_views_excluded)}개")
    print(f"  통과: {len(kept)}개")

    if not kept:
        # 0개여도 종료하지 않고 리포트는 생성 — '왜 컷됐는지' 탭에서 확인 가능해야 함
        print("⚠️ MAX_VIEWS 필터 후 남은 영상이 없습니다 — "
              "리포트는 '조회수 초과 제외' 탭만 채워 생성합니다 (분석/최종 후보 0건).")

    # 조회수 상위부터 정렬 (Hard 필터 결과 리포트 노출 순서 결정용).
    # 2026-08-07 대표님 요청으로 MAX_TOTAL_RAW 상한 완전 제거 — 초창기 2채널×15=30 기준의
    # 안전 상한이었으나 8채널 확장 후 후보 다양성을 과도하게 제한하고 있었음.
    # Claude 분석 비용 상한은 MAX_ANALYSIS_CANDIDATES 로 별도 통제됨.
    if kept:
        kept.sort(key=lambda v: v["views"], reverse=True)

    # EXCLUDE_TOP_N_VIEWS: 검증용 상위 N개 명시적 제외 (2026-08-07 도입).
    # 정규 실행에서는 미설정 → no-op. workflow_dispatch 로 test 발송할 때만 사용.
    # 상위권을 명시적으로 잘라내 하위권 후보 풀에서 어떤 결과가 나오는지 확인.
    try:
        exclude_top_n = int(os.environ.get("EXCLUDE_TOP_N_VIEWS", "0") or "0")
    except ValueError:
        exclude_top_n = 0
    if exclude_top_n > 0 and kept:
        dropped_top = kept[:exclude_top_n]
        kept = kept[exclude_top_n:]
        print(f"[EXCLUDE_TOP_N_VIEWS={exclude_top_n}] 상위 {len(dropped_top)}개 명시적 제외 "
              f"→ 하위 {len(kept)}개 대상으로 진행 (검증용)")

    # ── STEP 3. 지표 계산 (전체 풀 일괄 — dict 공유라 kept/excluded에 모두 반영) ──
    print("\n[STEP 3] 벤치마크 지표 계산")
    for v in all_collected_pool:
        compute_metrics(v, now_utc)
    print(f"  {len(all_collected_pool)}개 영상 지표 계산 완료 "
          f"(통과 {len(kept)} + 조회수 초과 {len(max_views_excluded)})")

    # config 스냅샷 (filtered_raw + HTML 필터 기준 표시용)
    config_snapshot = {
        "MAX_VIEWS": CFG.get("MAX_VIEWS", 0),
        "MIN_VIEWS": CFG.get("MIN_VIEWS", 0),
        "MAX_SHORTS_PER_CHANNEL": CFG.get("MAX_SHORTS_PER_CHANNEL", 0),
        "MAX_DURATION_SEC": CFG.get("MAX_DURATION_SEC", 0),
        "MIN_DURATION_SEC": CFG.get("MIN_DURATION_SEC", 0),
        # 업로드 age 필터 (사용자-facing "업로드 직후 ~ N일" / "M일 초과 ~ N일" 표시용).
        # MIN_AGE_DAYS_EXCLUSIVE 는 standard=30, recent=None. 반드시 스냅샷 포함해야
        # render_report 의 필터 기준 문구가 profile 별로 정확히 표시됨.
        "MIN_AGE_DAYS_EXCLUSIVE": CFG.get("MIN_AGE_DAYS_EXCLUSIVE"),
        "UPLOADED_WITHIN_DAYS": CFG.get("UPLOADED_WITHIN_DAYS", 0),
        "MAX_ANALYSIS_CANDIDATES": CFG.get("MAX_ANALYSIS_CANDIDATES", 0),
        "FINAL_CANDIDATES": CFG.get("FINAL_CANDIDATES", 0),
    }

    # 파일명에 profile 포함 — 같은 날 recent/standard 동시 실행 시 충돌 방지
    _profile_slug = CFG.get("_PROFILE", "standard")
    raw_path = BENCHMARK_DIR / f"{today_label}_{_profile_slug}_filtered_raw.json"

    def _save_filtered_raw(stages_dict):
        """filtered_raw.json 저장 — 항상 benchmark/ 디렉토리에만 쓴다."""
        raw_path.write_text(
            json.dumps({
                "generated_at": now_kst.isoformat(),
                "reference_channels": {
                    "manual": manual_channels,
                    "auto_discovered_reference_candidates":
                        auto_discovered_reference_candidates,
                    "merged_used": ref_channels,
                },
                "config_snapshot": config_snapshot,
                "counts": {k: len(v) for k, v in stages_dict.items()},
                "stages": stages_dict,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ── STEP 4. filtered_raw 1차 저장 (Claude 실패 대비 안전망) ──
    # 현재 시점에 확정된 stage: collected(전체 풀), max_views_excluded
    _save_filtered_raw({
        "collected": all_collected_pool,
        "max_views_excluded": max_views_excluded,
        "hard_excluded": [],
        "analyzed": [],
        "sent_excluded": [],
        "direct_final": [],
        "benchmark_final": [],
        "final": [],
    })
    print(f"  💾 filtered_raw 1차 저장: {raw_path}")

    # ── STEP 5. Hard 필터 ──
    print("\n[STEP 5] Hard 필터 (조회수 하한·길이·업로드 기간)")
    passed, hard_excl_pairs = hard_filter(kept, now_utc)
    print(f"  Hard 통과 {len(passed)}개 / 컷 {len(hard_excl_pairs)}개")

    # 제외 사유를 영상 dict에 부착 (탭 표시용)
    hard_excluded_list = []
    for v, reason in hard_excl_pairs:
        v["exclusion_reason"] = reason
        hard_excluded_list.append(v)

    # Claude 분석 대상: 조회수 상위 N개
    passed.sort(key=lambda v: v["views"], reverse=True)
    to_analyze = passed[:CFG["MAX_ANALYSIS_CANDIDATES"]]

    # ── STEP 6. Claude 분석 (영상별 적합도 + 벤치마크 가치, 두 축 독립 평가) ──
    total_usage = {"input": 0, "cache_write": 0, "cache_read": 0, "out": 0}
    sent_excluded_list = []
    direct_final = []
    benchmark_final = []
    final_candidates = []  # backward compat = direct_final
    # Phase 4 이후 3섹션 구조. HTML 렌더의 legacy fallback은 별도로 유지.
    patterns = {
        "direct_common_patterns": [],
        "benchmark_common_patterns": [],
        "per_channel_insights": [],
    }
    if to_analyze:
        print(f"\n[STEP 6] Claude 분석 — {len(to_analyze)}개 영상 (fit + benchmark_value 두 축)")
        client = anthropic.Anthropic(api_key=anthropic_key)
        for i, v in enumerate(to_analyze, 1):
            analysis, usage = analyze_video(client, v)
            v["analysis"] = analysis
            _add_usage(total_usage, usage)
            print(f"  [{i}/{len(to_analyze)}] {v['title'][:30]}... "
                  f"→ fit {analysis.get('fit_score', 0)} / "
                  f"bv {analysis.get('benchmark_value_score', 0)}")

        # 기발송 영상 표시 (weekly가 이미 발송한 영상)
        exclude_sent = CFG.get("EXCLUDE_SENT_FROM_CANDIDATES", True)
        for v in to_analyze:
            v["already_sent"] = bool(exclude_sent and v["video_id"] in sent_video_ids)

        # 시각적 일관성을 위해 analyzed 탭은 fit_score 내림차순 정렬 (기존 동작 유지)
        to_analyze.sort(key=lambda v: v.get("analysis", {}).get("fit_score", 0),
                        reverse=True)

        # 기발송 분리
        fresh = [v for v in to_analyze if not v.get("already_sent")]
        sent_excluded_list = [v for v in to_analyze if v.get("already_sent")]
        for v in sent_excluded_list:
            v["exclusion_reason"] = "weekly 기발송 영상 (history dedup)"
        if sent_excluded_list:
            print(f"  기발송 영상 {len(sent_excluded_list)}개를 후보 리스트에서 제외")

        # ── 두 리스트 계산 (26.07.14 대표님 피드백 반영: 중복 제거 정책) ──
        # 1) direct_final: fit_score 내림차순 TOP N (기존 방식)
        # 2) benchmark_final: bv_score 내림차순 전체 후보에서
        #    direct_final에 이미 포함된 video_id를 제외한 뒤 TOP N
        # 하드 캡·강제 쿼터·라운드 로빈·채널 기반 점수 보정은 여전히 없음.
        # benchmark_value_score 자체는 수정/보정하지 않음.
        direct_sorted = sorted(
            fresh,
            key=lambda v: (
                -(v.get("analysis") or {}).get("fit_score", 0),
                -v.get("views", 0),  # tiebreak: view_count desc
            ),
        )
        direct_final = direct_sorted[:CFG["FINAL_CANDIDATES"]]

        # direct에 이미 포함된 video_id는 benchmark 후보에서 제외 후 차순위 승격
        direct_ids = {v.get("video_id") for v in direct_final if v.get("video_id")}
        benchmark_sorted_dedup = [
            v for v in sorted(
                fresh,
                key=lambda x: (
                    -(x.get("analysis") or {}).get("benchmark_value_score", 0),
                    -x.get("views", 0),
                ),
            )
            if v.get("video_id") not in direct_ids
        ]
        benchmark_final = benchmark_sorted_dedup[:CFG["FINAL_CANDIDATES"]]

        print(f"  직접 후보 TOP {len(direct_final)}, "
              f"벤치마크 가치 TOP {len(benchmark_final)} "
              f"(direct와 중복 video_id는 benchmark에서 제외)")

        # backward compat: 기존 코드가 참조하는 final_candidates도 유지 (= direct_final)
        final_candidates = direct_final

        # Section 2 패턴 분석 입력 — direct_final + benchmark_final 합집합,
        # video_id 기준 unique 처리 (중복 표본 과대 계산 방지).
        # 각 축의 상위 후보를 모두 반영하되 동일 영상이 두 번 카운트되지 않게.
        seen_pattern = set()
        pattern_input = []
        for src in (direct_final, benchmark_final):
            for v in src:
                vid = v.get("video_id")
                if vid and vid not in seen_pattern:
                    seen_pattern.add(vid)
                    pattern_input.append(v)

        # ── STEP 7. Claude 분석 (공통 패턴 + 기획 포인트) ──
        print(f"\n[STEP 7] Claude 분석 — 공통 패턴 & 기획 포인트 도출")
        patterns, p_usage = analyze_patterns(client, pattern_input)
        _add_usage(total_usage, p_usage)
        print(f"  직접 후보 공통 패턴 {len(patterns.get('direct_common_patterns', []))}개 / "
              f"벤치마크형 공통 패턴 {len(patterns.get('benchmark_common_patterns', []))}개 / "
              f"채널별 인사이트 {len(patterns.get('per_channel_insights', []))}개")

        cost = estimate_cost(total_usage)
        print(f"  💰 예상 비용: ${cost:.4f} (≈ {cost * 1400:.0f}원)")
    else:
        print("\n⚠️ Hard 필터를 통과한 영상이 없어 분석을 건너뜁니다.")

    # ── STEP 8. HTML 리포트 + filtered_raw 최종 저장 ──
    print("\n[STEP 8] HTML 리포트 생성")
    stage_data = {
        "collected": all_collected_pool,
        "max_views_excluded": max_views_excluded,
        "hard_excluded": hard_excluded_list,
        "analyzed": to_analyze,
        "sent_excluded": sent_excluded_list,
        "direct_final": direct_final,        # 신규: fit_score 축 TOP N
        "benchmark_final": benchmark_final,  # 신규: benchmark_value_score 축 TOP N
        "final": final_candidates,           # backward compat = direct_final
    }
    # filtered_raw 최종 저장 (모든 stage 데이터 포함)
    _save_filtered_raw(stage_data)
    print(f"  💾 filtered_raw 최종 저장: {raw_path}")

    html = render_report(stage_data, patterns, today_label, ref_channels,
                         config_snapshot)
    # 파일명에 profile 포함 — 같은 날 recent/standard 동시 실행 시 충돌 방지
    report_path = BENCHMARK_DIR / f"{today_label}_{_profile_slug}_report.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"  📄 리포트 저장: {report_path}")

    # ============================================================
    # REPORT_FRAGMENT_PATH: orchestrator가 통합 shell 조립용으로 요청한 경로
    # → 발송/dedup write와는 무관. 파일 저장만.
    # ============================================================
    report_fragment_path = (os.environ.get("REPORT_FRAGMENT_PATH") or "").strip()
    if report_fragment_path:
        frag_p = Path(report_fragment_path)
        frag_p.parent.mkdir(parents=True, exist_ok=True)
        frag_p.write_text(html, encoding="utf-8")
        print(f"  📄 fragment 저장: {frag_p}")

    print("\n" + "=" * 60)
    print(f"벤치마크 완료 — 최종 참고 후보 {len(final_candidates)}개")
    print(f"  filtered_raw : {raw_path}")
    print(f"  report       : {report_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
