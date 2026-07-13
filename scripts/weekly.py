"""
털어드림 K-pop Shorts 주간 후보 수집·발송·아카이브.

GitHub Actions에서 매주 금요일 09:00 KST 실행.
환경변수:
  YOUTUBE_API_KEY    - YouTube Data API v3 키
  GMAIL_ADDRESS      - 발송용 Gmail 주소
  GMAIL_APP_PASSWORD - Gmail 앱 비밀번호 (16자리)
  RECIPIENT_EMAIL    - 받을 메일 주소
  ANTHROPIC_API_KEY  - Claude API 키 (후보 분석용)

실패 시 RECIPIENT_EMAIL로 에러 메일 발송 후 sys.exit(1).
"""
import os
import sys
import json
import time
import re
import smtplib
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import anthropic

# ============================================================================
# 설정
# ============================================================================
CONFIG = {
    "MIN_VIEWS": 500_000,           # 100만 이상 → 50만 이상 (26.07.13 변경)
    "MAX_VIEWS": 5_000_000,           # 500만 미만
    "MAX_DURATION_SEC": 180,
    # 검색 기간: "업로드 직후 ~ 1년 이내" 영상 (26.07.13 변경).
    # PRIMARY=FALLBACK=0이라 fallback 로직은 실질 no-op (코드는 유지 — 향후 정책 변경 시 값만 조정)
    "LOOKBACK_DAYS_OLDEST": 365,             # 가장 오래된 한계 (1년 전)
    "LOOKBACK_DAYS_NEWEST_PRIMARY": 0,       # 가장 최근 한계 (오늘까지 = 업로드 직후 포함)
    "LOOKBACK_DAYS_NEWEST_FALLBACK": 0,      # primary와 동일 — fallback 실질 무효
    "MIN_HARD_PASS": 25,                     # hard pass 미만이면 fallback 발동
    "MAX_ANALYSIS_CANDIDATES": 30,           # Claude 분석 비용 상한 (조회수 상위 N개만 분석)
    "TARGET_CANDIDATES": 20,                 # 최종 메일에 노출할 개수
    "REGION": "KR",
    "LANGUAGE": "ko",
    "CHANNEL_BLOCKLIST": {
        "SOONIGROUP [수니그룹]",
        # 자사/협력 채널 — 우리가 참고해야 하는 채널이라 후보에서 제외
        "연예부 김버니",
        "묘한덕질",
        "털어드림",
        "밈박스",
        "짤덕방",
    },
    # 채널 ID 기반 블록리스트 (이름 변경에 영향 안 받음 — 더 안전)
    "CHANNEL_ID_BLOCKLIST": {
        "UC7m5t1dfCRmr_YjInZYurXQ",  # 연예부 김버니
        "UCww8_tNoNouU_qk1JxZnNLQ",  # 묘한덕질
        "UCfrO3ZMC-rOThB-NSxfGjTQ",  # 털어드림
        "UCJ-WDvNyJYnt-9lIIX7uKGA",  # 밈박스
        "UCcerVbAluh-1ifuEH6ZMqsw",  # 짤덕방
    },
    "TITLE_KEYWORD_BLOCKLIST": [
        "직캠", "풀캠", "fancam",
        "챌린지 비하인드", "댄스 챌린지",
        "열애설", "열애",
        "뮤비 메이킹",
    ],
    "SEARCH_QUERIES": [
        "아이돌 비하인드 shorts",
        "아이돌 숨겨진 이유 shorts",
        "아이돌 팬들이 몰랐던 shorts",
        "아이돌 실력 논란 이유 shorts",
        "아이돌 무대 비하인드 shorts",
        "아이돌 라이브 실력 shorts",
        "아이돌 연습생 시절 shorts",
        "아이돌 데뷔 비하인드 shorts",
        "아이돌 관계성 shorts",
        "아이돌 인성 논란 이유 shorts",
        "아이돌 행동 이유 shorts",
        "아이돌 무대 실수 이유 shorts",
        "아이돌 팬서비스 이유 shorts",
        "아이돌 회사 시스템 shorts",
        "아이돌 코디 비하인드 shorts",
        "아이돌 안무 비하인드 shorts",
        "아이돌 녹음 비하인드 shorts",
        "걸그룹 비하인드 shorts",
        "보이그룹 비하인드 shorts",
        "케이팝 비하인드 shorts",
        "케이팝 숨겨진 이유 shorts",
        "케이팝 팬들이 몰랐던 shorts",
    ],
}

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "history"
HISTORY_DIR.mkdir(exist_ok=True)
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOCAL_OUTPUT_DIR = ROOT / "local_output"  # DRY_RUN 미리보기 저장 (gitignore됨)


class Tee:
    """stdout/stderr를 콘솔과 파일에 동시에 쓰는 헬퍼."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def setup_file_logging():
    """logs/YYYY-MM-DD_HHMMSS.log 파일에 콘솔 출력을 동시 저장."""
    ts = datetime.now(KST).strftime("%Y-%m-%d_%H%M%S")
    log_path = LOGS_DIR / f"{ts}.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)
    return log_path


# ============================================================================
# 환경변수 로딩 + 유효성 검사
# ============================================================================
def load_env():
    """필수 환경변수 로딩. 누락/형식 이상 시 즉시 명확한 에러."""
    required = {
        "YOUTUBE_API_KEY": str,
        "GMAIL_ADDRESS": str,
        "GMAIL_APP_PASSWORD": str,
        "RECIPIENT_EMAIL": str,
        "ANTHROPIC_API_KEY": str,
    }
    env = {}
    missing = []
    for key in required:
        val = os.environ.get(key, "").strip()  # 앞뒤 공백·줄바꿈 제거
        if not val:
            missing.append(key)
        else:
            env[key] = val

    if missing:
        msg = (
            f"❌ 필수 환경변수 누락: {', '.join(missing)}\n"
            f"GitHub 레포의 Settings → Secrets and variables → Actions에서 등록 확인 필요.\n"
            f"이름은 위와 정확히 일치해야 함 (대소문자·언더스코어 구분)."
        )
        print(msg, file=sys.stderr)
        sys.exit(1)

    # Gmail 앱 비밀번호 형식 빠른 체크 (16자리, 공백 가능)
    pw = env["GMAIL_APP_PASSWORD"].replace(" ", "")
    if len(pw) != 16 or not pw.isalnum():
        print(
            "⚠️ GMAIL_APP_PASSWORD가 16자리 영숫자 형식이 아닙니다. "
            "일반 비밀번호 대신 'Google 계정 → 2단계 인증 → 앱 비밀번호'에서 발급한 16자리 키를 사용하세요.",
            file=sys.stderr,
        )
    env["GMAIL_APP_PASSWORD"] = pw

    # 이메일 형식 빠른 체크
    for k in ("GMAIL_ADDRESS", "RECIPIENT_EMAIL"):
        if "@" not in env[k]:
            print(f"⚠️ {k}가 이메일 형식이 아닙니다: {env[k]}", file=sys.stderr)

    return env


# ============================================================================
# YouTube API 호출
# ============================================================================
BASE = "https://www.googleapis.com/youtube/v3"


def http_get(url, timeout=20):
    req = Request(url, headers={"User-Agent": "teoldeurim-weekly/1.0"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        # 에러 응답 바디를 읽어서 구체적인 reason 노출 (quotaExceeded 등)
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        reason = ""
        try:
            j = json.loads(body)
            errs = j.get("error", {}).get("errors", [])
            if errs:
                reason = errs[0].get("reason", "")
            msg = j.get("error", {}).get("message", "")
        except Exception:
            msg = body[:300]
        raise HTTPError(
            e.url,
            e.code,
            f"{e.reason} | reason={reason} | message={msg}",
            e.headers,
            None,
        ) from None


def search_videos(api_key, query, published_after, published_before):
    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": 50,                 # 50이 페이지당 최대 (쿼터 비용은 25/50 동일)
        "order": "viewCount",
        "regionCode": CONFIG["REGION"],
        "relevanceLanguage": CONFIG["LANGUAGE"],
        "publishedAfter": published_after,
        "publishedBefore": published_before,
        "videoDuration": "short",
        "key": api_key,
    }
    url = f"{BASE}/search?{urlencode(params)}"
    data = http_get(url)
    return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]


def load_seen_video_meta(skip_filename=None):
    """history/*.json에서 이미 발송된 영상의 메타데이터를 {video_id: dict}로 반환.

    매주 신선한 후보 보장용 + dedup 탭에 영상 정보를 표시하기 위함.
    각 dict에는 원본 메타데이터 + 'sent_date'(발송일)가 포함됨.
    skip_filename에 해당하는 파일은 제외 (resume 모드에서 오늘자 history 자기 자신 비교 방지).
    """
    seen = {}
    if not HISTORY_DIR.exists():
        return seen
    for path in sorted(HISTORY_DIR.glob("*.json")):
        if skip_filename and path.name == skip_filename:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sent_date = data.get("date_kst") or path.stem
            for c in data.get("candidates", []):
                vid = c.get("video_id")
                if vid:
                    m = dict(c)
                    m["sent_date"] = sent_date
                    seen[vid] = m  # 가장 최근 발송 기록으로 덮어씀
        except Exception as e:
            print(f"  ⚠️ history 로드 실패 [{path.name}]: {e}")
    return seen


def load_seen_video_ids(skip_filename=None):
    """과거 발송 video_id 집합 (load_seen_video_meta의 키만)."""
    return set(load_seen_video_meta(skip_filename).keys())


def fetch_video_details(api_key, ids):
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(chunk),
            "key": api_key,
        }
        url = f"{BASE}/videos?{urlencode(params)}"
        data = http_get(url)
        out.extend(data.get("items", []))
        time.sleep(0.15)
    return out


def iso_dur_to_sec(iso):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def has_korean(text):
    return bool(re.search(r"[\uAC00-\uD7AF]", text or ""))


def parse_item(v):
    sn = v.get("snippet", {})
    st = v.get("statistics", {})
    cd = v.get("contentDetails", {})
    return {
        "video_id": v["id"],
        "url": f"https://www.youtube.com/shorts/{v['id']}",
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "channel_id": sn.get("channelId", ""),
        "published_at": sn.get("publishedAt", ""),
        "view_count": int(st.get("viewCount", 0) or 0),
        "duration_seconds": iso_dur_to_sec(cd.get("duration", "")),
        "default_lang": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "",
    }


def blocked_by_keyword(title):
    low = (title or "").lower()
    return any(kw.lower() in low for kw in CONFIG["TITLE_KEYWORD_BLOCKLIST"])


# ============================================================================
# 수집 + 필터링
# ============================================================================
def hard_filter_reason(row):
    """row가 하드 필터에 걸리면 (사유 문자열, drop키) 반환. 통과면 (None, None).

    필터 기준·로직은 기존과 동일 — 단지 탈락 사유를 문자열로 남기는 것뿐.
    """
    v = row["view_count"]
    if v < CONFIG["MIN_VIEWS"]:
        return f"조회수 {CONFIG['MIN_VIEWS']//10000}만 미만 ({v:,}회)", "min_views"
    if v >= CONFIG["MAX_VIEWS"]:
        return f"조회수 {CONFIG['MAX_VIEWS']//10000}만 이상 ({v:,}회)", "max_views"
    d = row["duration_seconds"]
    if not (0 < d <= CONFIG["MAX_DURATION_SEC"]):
        return f"Shorts 길이 초과 ({d}초 / 제한 {CONFIG['MAX_DURATION_SEC']}초)", "duration"
    if not has_korean(row["title"]):
        return "한국어 제목 아님", "korean"
    if row["channel"] in CONFIG["CHANNEL_BLOCKLIST"] or row["channel_id"] in CONFIG.get("CHANNEL_ID_BLOCKLIST", set()):
        return f"채널 차단 ({row['channel']})", "channel"
    if blocked_by_keyword(row["title"]):
        return "제목 키워드 차단 (직캠/풀캠 등)", "keyword"
    return None, None


def collect(api_key, days_oldest, days_newest, seen_meta=None):
    """특정 기간 풀에서 영상 수집 + 하드 필터 + 중복 제외.

    days_oldest/days_newest: 검색 기간 한계 (예: 0~365 → 업로드 직후 ~ 1년 이내)
    seen_meta: 과거 발송 영상 {video_id: 메타데이터} 딕셔너리.

    반환: candidates(하드 통과) + hard_excluded(하드 탈락, 사유 포함) +
          dedup_list(중복 제외, 과거 메타데이터 포함) — 리포트 렌더링용 전체 리스트.
    """
    if seen_meta is None:
        seen_meta = {}
    seen_ids = set(seen_meta.keys())

    now = datetime.now(timezone.utc)
    pa = (now - timedelta(days=days_oldest)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pb = (now - timedelta(days=days_newest)).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"  검색 기간: {pa[:10]} ~ {pb[:10]} ({days_oldest}일 전 ~ {days_newest}일 전)")
    all_ids = set()
    for q in CONFIG["SEARCH_QUERIES"]:
        try:
            ids = search_videos(api_key, q, pa, pb)
            all_ids.update(ids)
            time.sleep(0.15)
        except Exception as e:
            print(f"  검색 실패 [{q}]: {e}")
    print(f"  고유 ID: {len(all_ids)}개")

    # 중복 제외 — history에서 이미 발송된 ID 컷 (Claude 분석 비용 절감)
    fresh_ids = all_ids - seen_ids
    dedup_ids = all_ids & seen_ids
    if seen_ids:
        print(f"  중복 제외: {len(dedup_ids)}개 (이미 발송된 영상) → 신규 {len(fresh_ids)}개")

    # dedup 리스트 — 과거 history 메타데이터에서 복원 (리포트 표시용)
    dedup_list = []
    for vid in dedup_ids:
        m = dict(seen_meta.get(vid, {}))
        m.setdefault("video_id", vid)
        m.setdefault("url", f"https://www.youtube.com/shorts/{vid}")
        m.setdefault("title", "(과거 발송 영상)")
        m.setdefault("channel", "")
        m.setdefault("view_count", 0)
        m.setdefault("duration_seconds", 0)
        m.setdefault("published_at", "")
        m["stage"] = "dedup_excluded"
        m["status_label"] = "중복 제외"
        m["exclusion_reason"] = f"과거 발송 이력 중복 ({m.get('sent_date', '?')} 발송)"
        dedup_list.append(m)
    dedup_list.sort(key=lambda x: -x.get("view_count", 0))

    details = fetch_video_details(api_key, list(fresh_ids))
    print(f"  메타데이터: {len(details)}개")

    candidates = []
    hard_excluded = []
    drop = {"min_views": 0, "max_views": 0, "duration": 0, "korean": 0, "channel": 0, "keyword": 0}
    for v in details:
        try:
            row = parse_item(v)
        except Exception:
            continue
        reason, dropkey = hard_filter_reason(row)
        if reason:
            drop[dropkey] += 1
            row["stage"] = "hard_excluded"
            row["status_label"] = "자동 필터 컷"
            row["exclusion_reason"] = reason
            hard_excluded.append(row)
        else:
            row["stage"] = "hard_passed"
            candidates.append(row)
    print(f"  필터 탈락: 조회수↓{drop['min_views']} 조회수↑{drop['max_views']} 길이{drop['duration']} 비한국어{drop['korean']} 채널{drop['channel']} 키워드{drop['keyword']}")

    candidates.sort(key=lambda x: -x["view_count"])
    hard_excluded.sort(key=lambda x: -x["view_count"])
    return {
        "candidates": candidates,
        "hard_excluded": hard_excluded,
        "dedup_list": dedup_list,
        "total_collected": len(all_ids),
        "dedup_excluded": len(dedup_ids),
        "total_detailed": len(details),
    }


# ============================================================================
# Claude API 후보 분석
# ============================================================================
ANALYSIS_SYSTEM_PROMPT = """당신은 '털어드림' K-pop Shorts 채널의 소싱 큐레이터입니다.

# 채널 정체성

'털어드림'은 K-pop 연예인 중심의 시사/논란 분석형 Shorts 채널입니다. 단순 가십이나
이슈 보도가 아니라 "이슈가 만들어낸 파장·관계·구조"를 분석적으로 다룹니다.

핵심 공식 — **시의성 지연**:
  1차 (이슈 자체)  →  2차 (반응 정리·해석형)  →  3차 (여론 분석·산업 영향)
이슈 직후 단순 보도가 아닌, 2차/3차 해석으로 재가공해 시간이 지나도 유효한
구조적 분석을 추구합니다. 휘발성 콘텐츠 ❌, 반복 가능한 구조적 콘텐츠 ✅.

# 8가지 핵심 소재 유형 (이 중 하나에 명확히 속해야 통과)

1. **K-pop 산업 분석**: 계약·경제·정책·소속사 시스템
   예: "소속사가 가난한 연습생 안 뽑는 이유", "중국 멤버들이 중국 안가는 이유"

2. **아이돌 뒷이야기**: 녹음실·촬영장·연습실 비하인드
   예: "녹음실에서 받는 특이한 요청", "후보정 개나 준 레코딩 비하인드"

3. **아이돌 일상 상황극**: 실물 vs 무대, 기대 vs 현실 갭
   예: "여돌 마주쳤을 때 현실 반응", "방송 중 행동을 조심해야 하는 이유"

4. **뮤직비디오 비하인드**: 위험 촬영, NG 장면, 메이킹 디테일
   예: "NG 내면 큰일나는 뮤비 장면", "뮤비 촬영을 위해 이걸 먹은 아사"

5. **아이돌 고충 분석**: 수면·다이어트·스케줄·건강·심리
   예: "72시간 못 자면 생기는 일", "장원영이 죽기 살기로 다이어트하는 이유"

6. **세대 간 관계성**: 선배 → 후배 영향, 그룹 간 계보, 멤버 간 관계
   예: "이채영 보고 연습한 백지헌", "성격 차이 보인다는 카리나vs윈터"

7. **실력/논란 해설**: 안무·라이브·인성 논란의 구조적 이유
   예: "춤 때문에 또 논란 된 베이비몬스터 아현", "라이브 실력 들통난 무대"

8. **연습생/데뷔 비하인드**: 회사 결정·트레이닝 시스템·데뷔 과정
   예: "SM에서 나가야겠다고 생각한 연습생 시절 이안", "신이 밀어줬다는 안유진 데뷔"

# 5가지 훅 패턴 (성공 영상의 첫 3초 구조)

1. **비교형**: 과거 vs 현재 / 기대 vs 현실 / 선배 vs 후배 나란히 제시
2. **의외성형**: 충격적 사실, 위험한 상황, 예상 밖 폭로
3. **극한상황형**: 수치·극단 강조 (72시간, 5년의 연습생, 죽기 살기)
4. **설명형**: 기술·경제·구조적 배경을 분석적으로 해부
5. **유머형**: 자조적 유머, 놀라운 장면, 위트 있는 표현

# 6가지 감정 자극 포인트 (좋은 후보는 이 중 1~2개를 명확히 자극)

- 놀라움 (예상 밖 사실 공개)
- 충격 (가혹한 현실 제시)
- 공감 (일상적 상황 공감)
- 웃음 (자조적·상황적 유머)
- 이해 (복잡한 현상 설명)
- 호기심 (의문형 제목 + 답변 약속)

# 통과 후보의 정량 패턴 (실제 상위 30개 영상 분석)

- 50%가 **1군 아이돌 이름 직접 포함** (제목에 구체적 인물성)
- 33%가 **부정적/극단적 표현** ("안 된다", "사라진", "충격적인", "죽기 살기", "또 논란")
- 100% **한국어 제목**
- **의문형 제목** ("왜?", "이유", "현실", "~일까?", "~는 이유")
- **시의성 낮음 + 반복 가능성 높음** (시간 지나도 유효)
- 길이 29~53초 (sweet spot)

# 탈락 후보의 정량 패턴 (하위 30개 영상 분석)

- **37%가 영어 제목** → 한국 타깃 이탈
- **부정적/강한 훅 부재** (밋밋한 평서문 톤)
- **특정 인물 언급 약함** (17% 수준, 상위 대비 1/3)
- **최근 1~2개월 시의성 강한 이슈 의존** (몇 주 후 휘발)

# 1군 아이돌 정의 (구체 그룹 리스트)

대형/중대형 기획사 소속 + 대중적 인지도 높은 현역 그룹의 멤버 또는 그룹 자체:

- **HYBE**: BTS, NewJeans/NJZ, TXT, ENHYPEN, ILLIT, LE SSERAFIM, SEVENTEEN, &TEAM
- **SM**: aespa, RIIZE, NCT(127/Dream/WayV), Red Velvet, Hearts2Hearts(하츠투하츠)
- **YG**: BLACKPINK, BABYMONSTER, TREASURE
- **JYP**: TWICE, ITZY, NMIXX, Stray Kids, ENHYPEN
- **기타 대중 인지도 높은 그룹**: IVE, (G)I-DLE, MAMAMOO, ATEEZ, TWS, KISS OF LIFE,
  BOYNEXTDOOR, fromis_9, ZEROBASEONE
- **솔로 활동으로 트렌드 1군 진입한 인물**:
  - IZ*ONE 출신 솔로: 이채영, 장원영, 안유진
  - **우주소녀 출신 솔로: 다영** (현 솔로 활동 중, 트렌드 폭발)

이 외 무명/소형 기획사·솔로·트로트·해외 K-pop은 1군 아닌 것으로 판단.
(우주소녀 그룹 자체는 1군 외 — 다영 솔로 활동만 1군 인정)

**동명이인 주의**: 한국 아이돌 이름은 동명이인이 흔합니다(다영/지수/유나/하니 등).
메타데이터만으로 인물 식별이 100% 확실하지 않은 경우, 1군 그룹 멤버일 가능성을 우선
고려해 통과 판정하고 topic_type에 "(추정)"을 표시하세요. 동명이인 가능성 때문에
실제 1군 인물을 컷하는 것보다, 추정으로 통과시키는 쪽이 안전합니다.

# 분석 임무

YouTube에서 발견된 후보 Shorts의 메타데이터(제목·채널·조회수·길이·업로드일)를 보고
다음 8개 필드를 작성하세요. 모든 텍스트 필드는 한국어, 짧고 분석적으로.

## 출력 필드 정의

1. **topic_type** (소재 유형): 위 8개 카테고리 중 어디에 속하는지 + 등장 인물·그룹 괄호로 부기.
   예: "세대 간 관계성/영향 (이채영 → 백지헌)", "실력 논란 해설 (베이비몬스터 아현)"

2. **title_pattern** (제목 구조): 제목을 추상화한 템플릿. 변수는 [대괄호]로.
   예: "'[선배] 보고 연습했다는 [후배]의 [특징]'", "'[원인] 때문에 또 논란 된 [인물]'"

3. **low_timeliness_reason** (시의성이 약한 이유): 왜 휘발성 낮고 재활용 가능한지 한 문장.
   예: "선배 아이돌 따라한 후배 구조는 매 세대마다 반복되는 장기 콘텐츠"

4. **channel_fit** (털어드림 적합성): 8개 카테고리 중 어디 정합 + 분석 각도.
   예: "명세 '관계성 shorts' 정합. 1군 인물 2명 직접 비교 가능, 2차 해석으로 확장 용이"

5. **first_3sec_hook** (첫 3초 훅 추정): 5가지 훅 패턴 중 어디 + 도입부 시각·청각 구체 추정.
   예: "비교형. 이채영 장면 / 백지헌 장면 나란히 편집 + '이거 보고 따라했다는' 자막"

6. **variation_topic** (털어드림식 변형 주제): 이 후보의 1차 이슈를 채널 공식대로
   2차/3차 해석으로 어떻게 재가공할지 한 줄. 분석적이고 구조적인 각도.
   예: "백지헌이 이채영을 '레퍼런스'로 공개 언급한 구조 — 르세라핌과 아이즈원의 암묵적 계보"

7. **should_include** (Soft Filter, 채택 여부 boolean):
   다음에 하나라도 해당하면 false:
   - **영어 제목** (한국 타깃 이탈, 하위 영상의 37%)
   - 직캠/풀캠/뮤비 영상 자체/무대 본방 (해설·비하인드 아님)
   - 1~2개월 내 휘발 가십 (단순 열애설/근황 보도)
   - 1군 아이돌이 아닌 무명·유튜버·소형 기획사 중심 (위 1군 리스트 외)
   - 단순 짤·밈·웃긴 모음 (분석 각도 0)
   - 자극·낚시·어그로 톤 (채널의 분석/해설형 결과 어긋남)
   - 8가지 소재 유형 어디에도 명확히 속하지 않는 모호한 콘텐츠
   - 부정적/구체적 훅이 전혀 없는 평서문 (하위 영상의 공통 패턴)
   기준에 부합하면 true.

8. **exclusion_reason**: should_include=false일 때 위 어느 기준에 걸렸는지
   짧은 한국어 사유 한 줄. true면 빈 문자열.

# Worked Examples — 통과 사례 (실제 채널 큐레이션 통과)

## Example 1
입력: 제목 "이채영 보고 연습했다는 백지헌의 짧은치마", 채널 "나 잘한다해짜나"
출력:
- topic_type: "세대 간 관계성/영향 (이채영 → 백지헌)"
- title_pattern: "'[선배] 보고 연습했다는 [후배]의 [특징]'"
- low_timeliness_reason: "선배 아이돌 따라한 후배 구조는 매 세대마다 반복되는 장기 콘텐츠"
- channel_fit: "명세 '관계성 shorts' 정합. 1군 인물 2명 직접 비교, 2차 해석 확장 용이"
- first_3sec_hook: "비교형. 이채영 무대 / 백지헌 무대 나란히 + '이거 보고 따라했다는' 자막"
- variation_topic: "백지헌이 이채영 무대를 따라 연습한 디테일(워킹·치마 핸들링)을 프레임 단위로 비교 해설"
- should_include: true
- exclusion_reason: ""

## Example 2
입력: 제목 "'춤' 때문에 또 논란 된 베이비몬스터 아현", 채널 "덕칼럼"
출력:
- topic_type: "실력 논란 해설 (베이비몬스터 아현)"
- title_pattern: "'[실력요소] 때문에 또 논란 된 [그룹] [멤버]'"
- low_timeliness_reason: "실력 논란은 한 번 붙으면 활동기마다 재소환되는 장기 콘텐츠"
- channel_fit: "명세 '실력 논란 이유 shorts' 정합. 구체 인물 + 반복 프레임('또')으로 시청자 호기심 유도"
- first_3sec_hook: "의외성형. 안무 어긋난 장면 → 동일 패턴 3회 반복 자막"
- variation_topic: "아현 춤 논란이 반복되는 구조적 이유 — YG 안무 난이도·포지션 배치·데뷔 전 연습량 부족 교차 분석"
- should_include: true
- exclusion_reason: ""

## Example 3
입력: 제목 "SM에서 나가야겠다고 생각한 연습생 시절 이안", 채널 "하츄뿅"
출력:
- topic_type: "연습생 시절 비하인드 (이안 / 전 SM)"
- title_pattern: "'[회사]에서 나가야겠다고 생각한 연습생 시절 [아이돌]'"
- low_timeliness_reason: "연습생 시절 회사 이탈 결정 구조는 데뷔 후에도 회상형으로 계속 소비되는 영구 소재"
- channel_fit: "명세 '연습생 시절 shorts' 정합. SM-현 그룹 시스템 차이를 분석 각도로 풀어낼 수 있음"
- first_3sec_hook: "설명형. SM 로고 + 이안 연습생 시절 사진 + '여기 있던 사람이 왜...' 자막"
- variation_topic: "이안이 SM을 떠나 현재 그룹에 자리잡기까지의 선택 구조를 회사 시스템 관점에서 해설"
- should_include: true
- exclusion_reason: ""

# Worked Examples — 탈락 사례 (Soft Filter 컷)

## Example 4
입력: 제목 "야구 선수 팬이 진짜 무서운 이유ㅋㅋㅋ #shorts #아는형님", 채널 "아는형님 공식"
출력 (요점):
- should_include: false
- exclusion_reason: "K-pop 아이돌 소재가 아닌 일반 예능 짤 모음. 8가지 소재 유형 중 어디에도 속하지 않음"

## Example 5
입력: 제목 "결국 삭제됐다는 부분?", 채널 "이슈모닝"
출력 (요점):
- should_include: false
- exclusion_reason: "대상 아이돌 특정 불가, 자극·낚시성 훅만 있고 분석 각도 없음"

## Example 6
입력: 제목 "[아이브] 안무 연습중에 혼자 즐기는 이서 #shorts", 채널 "IVE STARSHIP"
출력 (요점):
- should_include: false
- exclusion_reason: "해설/비하인드 분석이 아닌 단순 연습실 클립 — 1군 그룹이지만 채널 톤(분석형) 부정합"

# 출력 규칙

반드시 위 8개 필드를 가진 단일 JSON 객체로만 답하세요. 메타 코멘트나 "json" 같은 라벨 금지.
JSON 외 어떤 prose나 설명도 추가하지 마세요.

# 최종 자가검증 체크리스트 (출력 전 반드시 확인)

답변을 출력하기 직전, 아래 항목을 한 번 더 점검하세요. 하나라도 어기면 **출력 금지·재작성**:

- [ ] **8개 필드 전부 존재**: topic_type, title_pattern, low_timeliness_reason, channel_fit,
      first_3sec_hook, variation_topic, should_include, exclusion_reason — 누락 0개
- [ ] **필수 필드 비어있지 않음**: should_include=true인 경우 1~6번 필드 모두 의미 있는 값.
      "(분석 불가)", "정보 부족", "미정" 같은 회피성 답변 금지 — 메타데이터로 판단 가능한 만큼 추론
- [ ] **모든 텍스트 필드 한국어**: 영어/외국어 fallback 금지 (그룹명·인물명·노래제목 등 고유명사 제외)
- [ ] **should_include는 boolean**: true/false (문자열 "true" 금지)
- [ ] **exclusion_reason 일관성**:
      - should_include=true → 빈 문자열 ""
      - should_include=false → 비어있지 않은 짧은 한국어 사유
- [ ] **topic_type은 8개 카테고리 중 하나에 매핑**: 카테고리 외 새 분류 만들지 말 것
- [ ] **JSON 문법 유효**: 따옴표/쉼표/괄호 정확, trailing comma 금지
- [ ] **JSON 외 텍스트 0**: 머리말·꼬리말·설명·생각 출력 금지

위 8개 모두 통과 확인 후에만 JSON을 출력하세요. 누락·형식 오류는 작업 완료로 간주하지 마세요.
"""


def analyze_candidate(client, candidate):
    """단일 후보 영상에 대해 Claude로 6개 분석 필드 생성.

    프롬프트 캐싱(시스템 프롬프트) + 구조화 출력(json_schema) + adaptive thinking 사용.
    실패 시 빈 필드로 채워 반환 (전체 파이프라인이 멈추지 않도록).
    """
    user_msg = (
        f"제목: {candidate['title']}\n"
        f"채널: @{candidate['channel']}\n"
        f"조회수: {candidate['view_count']:,}\n"
        f"길이: {candidate['duration_seconds']}초\n"
        f"업로드: {candidate['published_at'][:10]}\n"
        f"URL: {candidate['url']}"
    )

    schema = {
        "type": "object",
        "properties": {
            "topic_type": {"type": "string"},
            "title_pattern": {"type": "string"},
            "low_timeliness_reason": {"type": "string"},
            "channel_fit": {"type": "string"},
            "first_3sec_hook": {"type": "string"},
            "variation_topic": {"type": "string"},
            "should_include": {"type": "boolean"},
            "exclusion_reason": {"type": "string"},
        },
        "required": [
            "topic_type", "title_pattern", "low_timeliness_reason",
            "channel_fit", "first_3sec_hook", "variation_topic",
            "should_include", "exclusion_reason",
        ],
        "additionalProperties": False,
    }

    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            thinking={"type": "adaptive"},
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": "medium",
            },
            system=[{
                "type": "text",
                "text": ANALYSIS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        # 토큰 사용량 — regular input + cache write/read + output 모두 추적
        return data, {
            "input": response.usage.input_tokens or 0,
            "cache_write": response.usage.cache_creation_input_tokens or 0,
            "cache_read": response.usage.cache_read_input_tokens or 0,
            "out": response.usage.output_tokens or 0,
        }
    except Exception as e:
        print(f"  ⚠️ 분석 실패 [{candidate['title'][:30]}...]: {e}")
        return {
            "topic_type": "(분석 실패)",
            "title_pattern": "",
            "low_timeliness_reason": "",
            "channel_fit": "",
            "first_3sec_hook": "",
            "variation_topic": "",
            "should_include": True,  # 분석 실패 시 일단 통과시킴 (사람이 직접 검수)
            "exclusion_reason": "",
        }, {"input": 0, "cache_write": 0, "cache_read": 0, "out": 0}


REQUIRED_ANALYSIS_FIELDS = {
    "topic_type", "title_pattern", "low_timeliness_reason", "channel_fit",
    "first_3sec_hook", "variation_topic", "should_include", "exclusion_reason",
}

# Claude Opus 4.7 단가 (USD per 1M tokens)
PRICING_OPUS_4_7 = {
    "input": 5.0,         # 일반 입력
    "cache_write": 6.25,  # 5분 ephemeral 캐시 쓰기 (1.25× input)
    "cache_read": 0.50,   # 캐시 히트 (0.1× input)
    "output": 25.0,       # 출력 (thinking 포함)
}


def estimate_cost(usage):
    """토큰 usage dict로 USD 비용 추정.

    usage = {"input": N, "cache_write": N, "cache_read": N, "out": N}
    """
    p = PRICING_OPUS_4_7
    return (
        usage["input"]       * p["input"]       / 1_000_000 +
        usage["cache_write"] * p["cache_write"] / 1_000_000 +
        usage["cache_read"]  * p["cache_read"]  / 1_000_000 +
        usage["out"]         * p["output"]      / 1_000_000
    )


def validate_analysis(analysis):
    """단일 후보의 analysis dict 검증. 문제 있으면 (False, 사유) 반환."""
    if not isinstance(analysis, dict):
        return False, "analysis가 dict 아님"
    missing = REQUIRED_ANALYSIS_FIELDS - set(analysis.keys())
    if missing:
        return False, f"필드 누락: {sorted(missing)}"
    if not isinstance(analysis.get("should_include"), bool):
        return False, f"should_include가 boolean 아님: {type(analysis.get('should_include')).__name__}"
    # should_include vs exclusion_reason 일관성
    if analysis["should_include"] and analysis.get("exclusion_reason", "").strip():
        return False, "should_include=true인데 exclusion_reason이 비어있지 않음"
    if not analysis["should_include"] and not analysis.get("exclusion_reason", "").strip():
        return False, "should_include=false인데 exclusion_reason 비어있음"
    # should_include=true이면 분석 필드 비어있으면 안 됨
    if analysis["should_include"]:
        for f in ("topic_type", "title_pattern", "channel_fit", "variation_topic"):
            if not analysis.get(f, "").strip():
                return False, f"필드 비어있음: {f}"
    return True, ""


def analyze_all(api_key, candidates):
    """후보 리스트 전체를 분석. 각 후보 dict에 'analysis' 키 추가.

    완료 후 검증:
    1. 입력 개수 == 출력 개수 (analysis 키 가진 후보 수) 일치
    2. 각 analysis dict의 필드 누락·형식 오류 검증
    3. 검증 실패 시 경고 로그 + 카운트 (전체 파이프라인은 계속 진행)
    """
    if not candidates:
        return candidates
    input_count = len(candidates)
    print(f"\n[분석] Claude API로 {input_count}개 후보 분석 중...")
    client = anthropic.Anthropic(api_key=api_key)
    total = {"input": 0, "cache_write": 0, "cache_read": 0, "out": 0}
    invalid = []
    for i, c in enumerate(candidates, 1):
        analysis, usage = analyze_candidate(client, c)
        c["analysis"] = analysis
        for k in total:
            total[k] += usage[k]
        ok, reason = validate_analysis(analysis)
        if not ok:
            invalid.append((i, c["title"][:40], reason))
            print(f"  [{i}/{input_count}] {c['title'][:40]}... ⚠️ {reason}")
        else:
            print(f"  [{i}/{input_count}] {c['title'][:40]}... ✅")

    cost = estimate_cost(total)
    total_tokens = total["input"] + total["cache_write"] + total["cache_read"] + total["out"]
    print(
        f"  토큰 합계: 입력 {total['input']:,} / "
        f"캐시쓰기 {total['cache_write']:,} / "
        f"캐시읽기 {total['cache_read']:,} / "
        f"출력 {total['out']:,} (총 {total_tokens:,})"
    )
    cache_efficiency = (
        f"{total['cache_read'] / max(total['cache_read'] + total['input'], 1) * 100:.1f}%"
    )
    print(f"  💰 예상 비용: ${cost:.4f} (≈ {cost * 1400:.0f}원, 캐시 히트율 {cache_efficiency})")

    # 배치 레벨 검증
    output_count = sum(1 for c in candidates if "analysis" in c)
    if input_count != output_count:
        print(f"  ❌ 검증 실패: 입력 {input_count}개 ≠ 출력 {output_count}개")
    else:
        print(f"  ✅ 개수 일치: 입력=출력={input_count}")
    if invalid:
        print(f"  ⚠️ 형식 오류 후보 {len(invalid)}개:")
        for idx, title, reason in invalid:
            print(f"     [{idx}] {title}... — {reason}")
    else:
        print(f"  ✅ 모든 후보 형식 검증 통과")

    return candidates


# ============================================================================
# 출력 포맷 (이메일 본문, 첨부 HTML, history JSON)
# ============================================================================
def fmt_views(n):
    if n >= 10_000_000:
        return f"{n/10_000_000:.1f}천만"
    if n >= 10_000:
        return f"{n//10_000}만"
    return f"{n:,}"


def esc_html(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


REPORT_CSS = """
:root {
  --bg: #ffffff;
  --bg-subtle: #f6f5f3;
  --border: #e8e6e0;
  --ink: #0a0a0a;
  --ink-2: #2a2a2a;
  --ink-dim: #6b6b6b;
  --ink-light: #9a9a9a;
  --accent: #8b1e3f;
  --accent-bg: #fef2f4;
  --highlight: #f5c518;
  --radius: 8px;
  --radius-sm: 4px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--bg); color: var(--ink); }
body {
  font-family: "Pretendard Variable", Pretendard, "Noto Sans KR", "Noto Sans CJK KR",
               -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  font-size: 15px;
  line-height: 1.65;
  letter-spacing: -0.01em;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "tnum" 1;
}
.page { max-width: 1080px; margin: 0 auto; padding: 80px 48px 96px; }

.hero { margin-bottom: 72px; }
.eyebrow {
  display: inline-flex; align-items: center; gap: 10px;
  font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--accent); margin-bottom: 28px; text-transform: uppercase;
}
.eyebrow::before { content: ""; width: 24px; height: 1.5px; background: var(--accent); display: inline-block; }
.hero h1 {
  font-weight: 900; font-size: 68px; line-height: 1.02;
  letter-spacing: -0.035em; color: var(--ink); margin-bottom: 24px; word-break: keep-all;
}
.hero h1 em { font-style: normal; color: var(--accent); }
.hero .lead { font-size: 16px; line-height: 1.7; color: var(--ink-dim); max-width: 760px; font-weight: 400; margin-bottom: 14px; }
.hero .lead:last-of-type { margin-bottom: 0; }
.hero .lead b { color: var(--ink); font-weight: 700; }
.hero .lead-row {
  display: grid; grid-template-columns: 110px 1fr; gap: 16px;
  max-width: 760px; padding: 8px 0;
  border-bottom: 1px solid var(--border);
}
.hero .lead-row:last-of-type { border-bottom: none; }
.hero .lead-row-k {
  font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--accent); padding-top: 4px;
}
.hero .lead-row-v {
  font-size: 15px; line-height: 1.65; color: var(--ink-2); word-break: keep-all;
}
.hero .lead-row-v b { color: var(--ink); font-weight: 700; }
.hero .lead-rows { margin-top: 18px; margin-bottom: 18px; }
.hero-meta {
  margin-top: 36px; display: flex; gap: 40px; flex-wrap: wrap;
  padding-top: 28px; border-top: 1px solid var(--border);
  font-size: 13px; color: var(--ink-light);
}
.hero-meta span b { color: var(--ink-2); font-weight: 700; margin-left: 6px; }

.stats {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 1px;
  background: var(--border); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; margin-bottom: 88px;
}
@media (max-width: 900px) {
  .stats { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 560px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
}
.stat { background: var(--bg); padding: 28px 24px; display: flex; flex-direction: column; gap: 4px; }
.stat.primary { background: var(--ink); color: white; }
.stat.primary .stat-k { color: rgba(255,255,255,0.55); }
.stat.primary .stat-n { color: var(--highlight); }
.stat-n { font-size: 40px; font-weight: 900; line-height: 1; letter-spacing: -0.03em; color: var(--ink); font-variant-numeric: tabular-nums; }
.stat-k { font-size: 11px; font-weight: 600; color: var(--ink-light); letter-spacing: 0.04em; text-transform: uppercase; margin-top: 8px; }

.section { margin-bottom: 88px; }
.section-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 24px; margin-bottom: 12px; flex-wrap: wrap;
}
.section-head .left { display: flex; align-items: baseline; gap: 16px; }
.section-num { font-size: 13px; font-weight: 700; color: var(--accent); letter-spacing: 0.05em; }
.section-head h2 { font-size: 34px; font-weight: 900; letter-spacing: -0.03em; color: var(--ink); }
.section-tag {
  font-size: 12px; font-weight: 600; color: var(--ink-light);
  padding: 4px 10px; background: var(--bg-subtle); border-radius: 99px;
}
.section-lead {
  font-size: 15px; color: var(--ink-dim); line-height: 1.7;
  max-width: 760px; margin-bottom: 40px; padding-top: 16px; border-top: 2px solid var(--ink);
}

.candidate {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 28px 32px; margin-bottom: 14px;
  break-inside: avoid; page-break-inside: avoid;
  transition: border-color .15s;
}
.candidate:hover { border-color: var(--ink); }
.cand-top {
  display: grid; grid-template-columns: 52px 1fr auto; gap: 20px;
  align-items: start; padding-bottom: 20px; margin-bottom: 20px;
  border-bottom: 1px dashed var(--border);
}
.cand-rank {
  font-size: 34px; font-weight: 900; color: var(--accent);
  line-height: 1; letter-spacing: -0.04em; font-variant-numeric: tabular-nums;
}
.cand-title-wrap { min-width: 0; }
.cand-title {
  font-size: 21px; font-weight: 800; line-height: 1.35;
  letter-spacing: -0.02em; color: var(--ink); margin-bottom: 10px; word-break: keep-all;
}
.cand-title a { color: inherit; text-decoration: none; }
.cand-meta-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 99px; font-weight: 600; }
.chip-channel { color: var(--accent); background: var(--accent-bg); }
.chip-date { color: var(--ink-dim); background: var(--bg-subtle); }
.chip-dur { color: var(--ink-2); background: var(--bg-subtle); font-variant-numeric: tabular-nums; }

.cand-views { text-align: right; white-space: nowrap; }
.cand-views-n {
  font-size: 34px; font-weight: 900; color: var(--ink);
  line-height: 1; letter-spacing: -0.03em; font-variant-numeric: tabular-nums;
}
.cand-views-u { font-size: 14px; font-weight: 500; color: var(--ink-dim); margin-left: 2px; }
.cand-views-raw { font-size: 11px; color: var(--ink-light); margin-top: 2px; font-variant-numeric: tabular-nums; }

.cand-body { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px 32px; }
.field { min-width: 0; }
.field-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--ink-light); margin-bottom: 5px;
}
.field-value { font-size: 14px; color: var(--ink-2); line-height: 1.55; font-weight: 500; }
.field-code {
  display: inline-block; font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12.5px; background: var(--bg-subtle); padding: 3px 8px;
  border-radius: var(--radius-sm); color: var(--ink); font-weight: 500;
}
.field-link {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px; color: var(--accent); text-decoration: none; word-break: break-all;
}
.field-link:hover { text-decoration: underline; }

.hero-field {
  grid-column: span 2;
  background: linear-gradient(135deg, var(--accent-bg) 0%, #fff 100%);
  border: 1px solid var(--accent); border-radius: var(--radius-sm);
  padding: 16px 20px; margin-top: 4px;
}
.hero-field .field-label { color: var(--accent); }
.hero-field .field-value {
  font-size: 16px; font-weight: 700; color: var(--ink); letter-spacing: -0.015em;
}

.footer {
  margin-top: 80px; padding-top: 24px; border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px;
  font-size: 11px; font-weight: 500; color: var(--ink-light); letter-spacing: 0.02em;
}

/* 필터 기준 박스 (Hard / Soft 명시) */
.criteria {
  display: grid; grid-template-columns: 1fr 1fr; gap: 1px;
  background: var(--border); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; margin-bottom: 56px;
}
.criteria-col { background: var(--bg); padding: 24px 28px; }
.criteria-head {
  display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px;
  padding-bottom: 10px; border-bottom: 2px solid var(--ink);
}
.criteria-tag {
  font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: var(--radius-sm);
  letter-spacing: 0.06em; text-transform: uppercase;
}
.criteria-tag.hard { color: #fff; background: var(--ink); }
.criteria-tag.soft { color: var(--accent); background: var(--accent-bg); border: 1px solid var(--accent); }
.criteria-head h3 {
  font-size: 17px; font-weight: 800; letter-spacing: -0.02em; color: var(--ink);
}
.criteria-head .meta { font-size: 11px; color: var(--ink-light); margin-left: auto; }
.criteria-list { list-style: none; padding: 0; margin: 0; }
.criteria-list li {
  font-size: 13px; color: var(--ink-2); line-height: 1.75; padding: 6px 0;
  padding-left: 14px; position: relative; word-break: keep-all;
}
.criteria-list li::before {
  content: "·"; color: var(--ink-light); font-weight: 900;
  position: absolute; left: 0; top: 6px;
}
.criteria-list li b { color: var(--ink); font-weight: 700; margin-right: 4px; }
.criteria-list li code {
  display: inline-block; white-space: nowrap; word-break: normal;
  font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px;
  background: var(--bg-subtle); padding: 1px 6px; border-radius: 3px;
  margin: 1px 2px;
}

@media (max-width: 720px) { .criteria { grid-template-columns: 1fr; } }

/* Soft filter 탈락 섹션 */
.excluded-row {
  display: grid; grid-template-columns: 44px 1fr; gap: 16px;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
  align-items: start;
}
.excluded-row:last-child { border-bottom: none; }
.ex-rank {
  font-size: 14px; font-weight: 800; color: var(--ink-light);
  font-variant-numeric: tabular-nums; padding-top: 2px;
}
.ex-main { min-width: 0; }
.ex-title {
  font-size: 14px; font-weight: 700; color: var(--ink-2);
  line-height: 1.4; margin-bottom: 6px; word-break: keep-all;
}
.ex-title a { color: inherit; text-decoration: none; }
.ex-title a:hover { color: var(--accent); }
.ex-meta { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
.chip-views { color: var(--ink-2); background: var(--bg-subtle); font-variant-numeric: tabular-nums; }
.ex-reason {
  display: flex; gap: 10px; align-items: baseline;
  background: #fafaf8; border-left: 3px solid #c44;
  padding: 8px 12px; border-radius: var(--radius-sm);
  font-size: 13px;
}
.ex-reason-label {
  font-size: 10px; font-weight: 700; color: #c44;
  letter-spacing: 0.06em; text-transform: uppercase; flex-shrink: 0;
}
.ex-reason-text { color: var(--ink-2); line-height: 1.5; }

.excluded-wrap {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden;
}

/* Waitlist (다음 주 후보) 섹션 — 파란 톤으로 차별화 */
.criteria-tag.waitlist {
  color: #fff; background: #2a5278; border: 1px solid #2a5278;
}
.waitlist-row {
  display: grid; grid-template-columns: 44px 1fr; gap: 16px;
  padding: 12px 18px; border-bottom: 1px solid var(--border);
  align-items: start;
}
.waitlist-row:last-child { border-bottom: none; }
.wl-rank {
  font-size: 13px; font-weight: 800; color: var(--ink-light);
  font-variant-numeric: tabular-nums; padding-top: 2px;
}
.wl-main { min-width: 0; }
.wl-title {
  font-size: 13px; font-weight: 700; color: var(--ink-2);
  line-height: 1.4; margin-bottom: 5px; word-break: keep-all;
}
.wl-title a { color: inherit; text-decoration: none; }
.wl-title a:hover { color: #2a5278; }
.wl-meta { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }
.wl-status {
  display: flex; gap: 10px; align-items: baseline;
  background: #f4f7fb; border-left: 3px solid #2a5278;
  padding: 6px 10px; border-radius: var(--radius-sm);
  font-size: 12px;
}
.wl-status-label {
  font-size: 9px; font-weight: 700; color: #2a5278;
  letter-spacing: 0.06em; text-transform: uppercase; flex-shrink: 0;
}
.wl-status-text { color: var(--ink-2); line-height: 1.5; }

.waitlist-wrap {
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden;
}

/* ===== 접이식 필터 기준 박스 (stats 위) ===== */
.criteria-toggle {
  margin-bottom: 20px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--bg); overflow: hidden;
}
.criteria-toggle > summary {
  cursor: pointer; list-style: none; padding: 14px 22px;
  font-size: 13px; font-weight: 700; color: var(--ink-2);
  display: flex; align-items: center; gap: 8px; user-select: none;
}
.criteria-toggle > summary::-webkit-details-marker { display: none; }
.criteria-toggle > summary::before {
  content: "▸"; color: var(--accent); font-size: 11px;
  transition: transform .15s;
}
.criteria-toggle[open] > summary::before { transform: rotate(90deg); }
.criteria-toggle > summary:hover { background: var(--bg-subtle); }
.criteria-toggle .criteria { margin: 0; border: none; border-top: 1px solid var(--border); border-radius: 0; }

/* ===== stats 카드 → 탭 (JS 있을 때만 인터랙티브) ===== */
.stats .stat {
  border: none; text-align: left; width: 100%;
  font-family: inherit; position: relative; cursor: default;
}
html.js .stats .stat { cursor: pointer; transition: background .12s; }
html.js .stats .stat:hover { background: var(--bg-subtle); }
html.js .stats .stat.primary:hover { background: #1f1f1f; }
html.js .stats .stat.active {
  outline: 2.5px solid var(--accent); outline-offset: -2.5px; z-index: 2;
}
html.js .stats .stat.active::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 3px;
  background: var(--accent);
}
html.js .stats .stat.primary.active { outline-color: var(--highlight); }
html.js .stats .stat.primary.active::after { background: var(--highlight); }

/* ===== 검색 바 (JS 있을 때만 표시) ===== */
.search-bar {
  display: none; align-items: center; gap: 12px;
  margin-bottom: 18px; flex-wrap: wrap;
}
html.js .search-bar { display: flex; }

/* JS 유무에 따른 표시 토글 */
.js-only { display: none; }
html.js .js-only { display: inline; }
html.js .nojs-only { display: none; }
/* JS 없으면 최종 발송 패널만 노출 (다른 패널은 is-hidden 그대로) */
.search-input {
  flex: 1; min-width: 240px; font-family: inherit; font-size: 14px;
  padding: 11px 16px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--ink); background: var(--bg);
  outline: none;
}
.search-input:focus { border-color: var(--accent); }
.search-input::placeholder { color: var(--ink-light); }
.search-count {
  font-size: 13px; color: var(--ink-dim); font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.search-count b { color: var(--accent); font-weight: 800; }

/* ===== 단계별 패널 + 리스트 ===== */
.stage-panel { }
.stage-panel-head {
  display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px;
}
.stage-panel-head h2 {
  font-size: 26px; font-weight: 900; letter-spacing: -0.03em; color: var(--ink);
}
.stage-panel-head .cnt {
  font-size: 13px; font-weight: 600; color: var(--ink-light);
}
.stage-panel-desc {
  font-size: 13px; color: var(--ink-dim); line-height: 1.6;
  margin-bottom: 20px; padding-top: 12px; border-top: 2px solid var(--ink);
}
.stage-list {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden;
}

/* 컴팩트 리스트 아이템 */
.si {
  display: grid; grid-template-columns: 44px 1fr auto; gap: 14px;
  padding: 13px 18px; border-bottom: 1px solid var(--border);
  align-items: start;
}
.si:last-child { border-bottom: none; }
.si-rank {
  font-size: 13px; font-weight: 800; color: var(--ink-light);
  font-variant-numeric: tabular-nums; padding-top: 2px;
}
.si-body { min-width: 0; }
.si-title {
  font-size: 14px; font-weight: 700; color: var(--ink); line-height: 1.4;
  margin-bottom: 5px; word-break: keep-all;
}
.si-title a { color: inherit; text-decoration: none; }
.si-title a:hover { color: var(--accent); }
.si-meta { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.si-reason {
  margin-top: 7px; font-size: 12px; line-height: 1.5; color: var(--ink-2);
  background: var(--bg-subtle); border-left: 3px solid var(--ink-light);
  padding: 6px 10px; border-radius: var(--radius-sm);
}
.si-reason.cut { background: #fafaf8; border-left-color: #c44; }
.si-reason.dedup { background: #f4f7fb; border-left-color: #2a5278; }
.si-reason b {
  font-size: 9px; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; margin-right: 6px;
}
.si-reason.cut b { color: #c44; }
.si-reason.dedup b { color: #2a5278; }
.si-views { text-align: right; white-space: nowrap; }
.si-views-n {
  font-size: 18px; font-weight: 900; color: var(--ink);
  font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
}
.si-views-u { font-size: 11px; color: var(--ink-dim); font-weight: 500; }

/* 상태 badge */
.badge {
  font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 99px;
  white-space: nowrap;
}
.badge-final { color: #fff; background: var(--ink); }
.badge-soft { color: #fff; background: #c44; }
.badge-hard { color: #fff; background: var(--ink-light); }
.badge-dedup { color: #fff; background: #2a5278; }
.badge-wait { color: var(--accent); background: var(--accent-bg); border: 1px solid var(--accent); }
.badge-na { color: var(--ink-dim); background: var(--bg-subtle); }

/* empty state */
.empty-state {
  padding: 56px 24px; text-align: center; color: var(--ink-light);
  font-size: 14px;
}
.empty-state .big { font-size: 32px; margin-bottom: 10px; }

/* 더보기 버튼 */
.more-btn {
  display: block; width: 100%; padding: 14px; cursor: pointer;
  background: var(--bg-subtle); border: none; border-top: 1px solid var(--border);
  font-family: inherit; font-size: 13px; font-weight: 700; color: var(--accent);
}
.more-btn:hover { background: #ecebe5; }

.is-hidden { display: none !important; }

@media print {
  body { background: white; }
  .page { max-width: 100%; padding: 24px 28px; }
  .candidate, .stats { page-break-inside: avoid; break-inside: avoid; }
  .section-head { page-break-after: avoid; }
}
"""


def _render_candidate_card(i, v, data_search=""):
    """단일 후보 카드 — 6개 분석 필드 포함. data_search: 검색용 텍스트."""
    a = v.get("analysis", {})
    return f"""
    <div class="candidate" data-search="{data_search}">
      <div class="cand-top">
        <div class="cand-rank">{i:02d}</div>
        <div class="cand-title-wrap">
          <h3 class="cand-title">
            <a href="{v['url']}">{esc_html(v['title'])}</a>
          </h3>
          <div class="cand-meta-row">
            <span class="chip chip-channel">@{esc_html(v['channel'])}</span>
            <span class="chip chip-date">{v['published_at'][:10]}</span>
            <span class="chip chip-dur">{v['duration_seconds']}초</span>
          </div>
        </div>
        <div class="cand-views">
          <div><span class="cand-views-n">{fmt_views(v['view_count'])}</span><span class="cand-views-u">회</span></div>
          <div class="cand-views-raw">({v['view_count']:,})</div>
        </div>
      </div>
      <div class="cand-body">
        <div class="field">
          <div class="field-label">소재 유형</div>
          <div class="field-value">{esc_html(a.get('topic_type', ''))}</div>
        </div>
        <div class="field">
          <div class="field-label">제목 구조</div>
          <div class="field-value"><span class="field-code">{esc_html(a.get('title_pattern', ''))}</span></div>
        </div>
        <div class="field">
          <div class="field-label">시의성이 약한 이유</div>
          <div class="field-value">{esc_html(a.get('low_timeliness_reason', ''))}</div>
        </div>
        <div class="field">
          <div class="field-label">털어드림 적합성</div>
          <div class="field-value">{esc_html(a.get('channel_fit', ''))}</div>
        </div>
        <div class="field">
          <div class="field-label">첫 3초 훅 추정</div>
          <div class="field-value">{esc_html(a.get('first_3sec_hook', ''))}</div>
        </div>
        <div class="field">
          <div class="field-label">Link</div>
          <div class="field-value"><a class="field-link" href="{v['url']}">{v['url']}</a></div>
        </div>
        <div class="hero-field">
          <div class="field-label">▸ 털어드림식 변형 주제</div>
          <div class="field-value">{esc_html(a.get('variation_topic', ''))}</div>
        </div>
      </div>
    </div>"""


def _render_excluded_card(i, v):
    """Soft filter 탈락 후보 카드 — 더 작고, 사유를 강조."""
    a = v.get("analysis", {})
    reason = a.get("exclusion_reason") or "(사유 누락)"
    return f"""
    <div class="excluded-row">
      <div class="ex-rank">{i:02d}</div>
      <div class="ex-main">
        <div class="ex-title">
          <a href="{v['url']}">{esc_html(v['title'])}</a>
        </div>
        <div class="ex-meta">
          <span class="chip chip-channel">@{esc_html(v['channel'])}</span>
          <span class="chip chip-date">{v['published_at'][:10]}</span>
          <span class="chip chip-dur">{v['duration_seconds']}초</span>
          <span class="chip chip-views">{fmt_views(v['view_count'])}회</span>
        </div>
        <div class="ex-reason">
          <span class="ex-reason-label">제외 사유</span>
          <span class="ex-reason-text">{esc_html(reason)}</span>
        </div>
      </div>
    </div>"""


def _render_waitlist_card(i, v):
    """다음 주 진입 가능성 있는 보류 후보 카드."""
    wl_type = v.get("waitlist_type", "")
    if wl_type == "soft_pass_below_top":
        status = "AI 통과했지만 조회수 순위 밀려 보류 → 다음 주 dedup 후 진입 가능"
    elif wl_type == "not_analyzed":
        status = "분석 미실시 (조회수 순위로 분석 풀 못 들어감) → 다음 주 분석 풀 진입 가능"
    else:
        status = "다음 주 진입 가능"
    return f"""
    <div class="waitlist-row">
      <div class="wl-rank">{i:02d}</div>
      <div class="wl-main">
        <div class="wl-title">
          <a href="{v['url']}">{esc_html(v['title'])}</a>
        </div>
        <div class="wl-meta">
          <span class="chip chip-channel">@{esc_html(v['channel'])}</span>
          <span class="chip chip-views">{fmt_views(v['view_count'])}회</span>
          <span class="chip chip-date">{v['published_at'][:10]}</span>
          <span class="chip chip-dur">{v['duration_seconds']}초</span>
        </div>
        <div class="wl-status">
          <span class="wl-status-label">대기 사유</span>
          <span class="wl-status-text">{esc_html(status)}</span>
        </div>
      </div>
    </div>"""


def _si_search_text(item):
    """리스트 아이템의 검색용 텍스트 (제목·채널·사유·분석 텍스트 전부 합침, 소문자)."""
    parts = [item.get("title", ""), item.get("channel", ""), item.get("exclusion_reason", "")]
    a = item.get("analysis") or {}
    for k in ("topic_type", "title_pattern", "low_timeliness_reason",
              "channel_fit", "first_3sec_hook", "variation_topic", "exclusion_reason"):
        parts.append(str(a.get(k, "")))
    joined = " ".join(p for p in parts if p)
    return esc_html(joined.lower())


_BADGE_CLASS = {
    "최종 발송": "badge-final", "AI 컷": "badge-soft", "자동 필터 컷": "badge-hard",
    "중복 제외": "badge-dedup", "대기": "badge-wait", "미분석": "badge-na",
}


def _badge_html(label):
    if not label:
        return ""
    return f'<span class="badge {_BADGE_CLASS.get(label, "badge-na")}">{esc_html(label)}</span>'


def _render_si_row(i, item):
    """컴팩트 리스트 행 — 제목·채널·조회수·상태 badge·사유."""
    pub = (item.get("published_at", "") or "")[:10]
    status = item.get("status_label", "")
    reason = item.get("exclusion_reason", "")
    stage = item.get("stage", "")
    reason_html = ""
    if reason:
        rcls = "dedup" if stage == "dedup_excluded" else "cut"
        reason_html = f'<div class="si-reason {rcls}"><b>사유</b>{esc_html(reason)}</div>'
    return f"""
    <div class="si" data-search="{_si_search_text(item)}">
      <div class="si-rank">{i:02d}</div>
      <div class="si-body">
        <div class="si-title"><a href="{item.get('url', '#')}">{esc_html(item.get('title', ''))}</a></div>
        <div class="si-meta">
          <span class="chip chip-channel">@{esc_html(item.get('channel', ''))}</span>
          <span class="chip chip-date">{pub}</span>
          <span class="chip chip-dur">{item.get('duration_seconds', 0)}초</span>
          {_badge_html(status)}
        </div>
        {reason_html}
      </div>
      <div class="si-views">
        <div><span class="si-views-n">{fmt_views(item.get('view_count', 0))}</span><span class="si-views-u">회</span></div>
      </div>
    </div>"""


# 탭 메타데이터: (stage_key, 라벨, stat 숫자 키, 패널 제목, 패널 설명)
_STAGE_TABS = [
    ("collected", "수집한 영상", "total_collected", "수집한 영상 전체",
     "22개 검색어로 수집한 raw 후보 전체. 각 항목에 최종 단계 badge가 표시됩니다."),
    ("dedup_excluded", "중복 제외", "dedup_excluded", "중복 제외 (과거 발송 이력)",
     "과거에 이미 메일로 발송한 적이 있어 자동 제외된 영상. 매주 신선한 후보 보장을 위한 dedup."),
    ("hard_excluded", "자동 필터 컷", "hard_filter_excluded", "자동 필터 컷 (Hard Filter)",
     "조회수·길이·언어·채널·키워드 등 코드 자동 필터에서 제외된 영상. 각 항목에 제외 사유 표시."),
    ("hard_passed", "Hard 통과", "total_after_filter", "Hard Filter 통과",
     "자동 필터를 통과해 Claude 분석 대상으로 넘어간 후보. badge로 최종 발송/AI 컷/대기 여부 표시."),
    ("soft_excluded", "AI 컷", "soft_filter_excluded", "AI 큐레이션 컷 (Soft Filter)",
     "Hard는 통과했지만 Claude가 채널 톤과 어긋난다고 판정한 후보. 제외 사유 표시."),
    ("final", "최종 발송", "final_count", "최종 발송 후보",
     "큐레이션 최종 통과 — 메일로 발송된 후보. 각 영상별 6개 분석 필드 + 변형 주제 첨부."),
]


def render_standalone_report(top, meta, today_label, soft_excluded=None, waitlist=None, report_lists=None):
    """첨부 HTML 리포트 — 인터랙티브 (클릭 탭 + 단계별 리스트 + 검색).

    standalone HTML 전용. JS 기반 인터랙션 — 이메일 본문(render_email_html)은 별도 static.
    """
    soft_excluded = soft_excluded or []
    waitlist = waitlist or []
    report_lists = report_lists or {}
    fetched = datetime.now(KST).strftime("%Y-%m-%d")

    # ── 6개 단계 리스트 구성 ──
    dedup = report_lists.get("dedup_excluded", [])
    hard_excl = report_lists.get("hard_excluded", [])
    hard_pass = report_lists.get("hard_passed", top)  # 구버전 fallback
    stage_data = {
        "collected": dedup + hard_excl + hard_pass,
        "dedup_excluded": dedup,
        "hard_excluded": hard_excl,
        "hard_passed": hard_pass,
        "soft_excluded": soft_excluded,
        "final": top,
    }

    # ── stats 탭 카드 ──
    stat_cards = []
    for key, label, statkey, _t, _d in _STAGE_TABS:
        n = meta.get(statkey, len(stage_data.get(key, [])))
        primary = " primary" if key == "final" else ""
        active = " active" if key == "final" else ""
        stat_cards.append(
            f'<button class="stat{primary}{active}" data-stage="{key}" type="button">'
            f'<div class="stat-n">{n}</div><div class="stat-k">{label}</div></button>'
        )

    # ── 6개 패널 (more-btn은 리스트 아래) ──
    panels = []
    for key, label, statkey, ptitle, pdesc in _STAGE_TABS:
        items = stage_data.get(key, [])
        if not items:
            body = ('<div class="stage-list"><div class="empty-state">'
                    '<div class="big">—</div>해당 단계의 영상이 없습니다.</div></div>')
        elif key == "final":
            body = "".join(_render_candidate_card(i, v, _si_search_text(v))
                           for i, v in enumerate(items, 1))
        else:
            rows = "".join(_render_si_row(i, v) for i, v in enumerate(items, 1))
            body = f'<div class="stage-list">{rows}</div>'
        hidden = "" if key == "final" else " is-hidden"
        panels.append(
            f'<div class="stage-panel{hidden}" data-stage="{key}">'
            f'<div class="stage-panel-head"><h2>{esc_html(ptitle)}</h2>'
            f'<span class="cnt">총 {len(items)}개</span></div>'
            f'<p class="stage-panel-desc">{esc_html(pdesc)}</p>'
            f'{body}'
            f'<button class="more-btn is-hidden" type="button">더보기</button>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<base target="_blank">
<title>털어드림 · K-pop Shorts 소싱 리포트 {today_label}</title>
<style>{REPORT_CSS}</style>
</head><body><div class="page">
  <section class="hero">
    <div class="eyebrow">Channel Sourcing Report · {today_label}</div>
    <h1>털어드림 채널<br><em>K-pop Shorts</em> 후보 분석</h1>
    <p class="lead">
      YouTube Data API v3 기반 메타데이터 전수 검증. <b>22개 검색어</b>로 수집한 후보를 분석합니다.
      <span class="js-only"><b>상단 카드를 클릭</b>하면 각 단계별 영상 리스트를 볼 수 있습니다.</span>
      <span class="nojs-only">전체 단계별 리스트·검색 기능은 PC 브라우저(또는 파일 다운로드 후 모바일 브라우저)에서 확인할 수 있습니다.</span>
    </p>
    <p class="lead">
      → 이번 주 신규 <b style="color: var(--ink);">{len(top)}개</b> 확정.
    </p>
    <div class="hero-meta">
      <span>Source<b>YouTube Data API</b></span>
      <span>Fetched<b>{fetched}</b></span>
      <span>Queries<b>{len(CONFIG['SEARCH_QUERIES'])}</b></span>
      <span>Range<b>{esc_html(str(meta.get('lookback_days', '')))}</b></span>
    </div>
  </section>

  <details class="criteria-toggle">
    <summary>필터 기준 보기 (Hard / Soft)</summary>
    <div class="criteria">
      <div class="criteria-col">
        <div class="criteria-head">
          <span class="criteria-tag hard">Hard</span>
          <h3>자동 메타데이터 컷</h3>
          <span class="meta">코드 규칙</span>
        </div>
        <ul class="criteria-list">
          <li><b>조회수</b> {CONFIG['MIN_VIEWS']//10000:,}만 이상 ~ {CONFIG['MAX_VIEWS']//10000:,}만 미만</li>
          <li><b>길이</b> Shorts 형식 ({CONFIG['MAX_DURATION_SEC']}초 이하)</li>
          <li><b>언어</b> 한국어 제목 (한글 포함)</li>
          <li><b>업로드 기간</b> {CONFIG['LOOKBACK_DAYS_OLDEST']//30}개월 이내 (업로드 직후 ~ 1년 전)</li>
          <li><b>중복 제외</b> 과거 발송 영상 자동 dedup (history 기반)</li>
          <li><b>채널 블록</b> {', '.join(sorted(CONFIG['CHANNEL_BLOCKLIST'])) or '(없음)'}</li>
          <li><b>키워드 블록</b> {', '.join(f'<code>{esc_html(k)}</code>' for k in CONFIG['TITLE_KEYWORD_BLOCKLIST'])}</li>
        </ul>
      </div>
      <div class="criteria-col">
        <div class="criteria-head">
          <span class="criteria-tag soft">Soft</span>
          <h3>Claude 큐레이션 판정</h3>
          <span class="meta">AI 분석</span>
        </div>
        <ul class="criteria-list">
          <li><b>직캠/뮤비/무대 영상 그 자체</b> — 해설·비하인드가 아닌 본방 영상</li>
          <li><b>시의성 강한 소재</b> — 가십·근황·열애설 등 일주일 내 휘발</li>
          <li><b>무명/유튜버 중심</b> — 1군 아이돌이 주체가 아닌 콘텐츠</li>
          <li><b>단순 짤·밈·웃긴 모음</b> — 분석 각도 없는 영상</li>
          <li><b>자극·낚시 톤</b> — 분석/해설형이라는 채널 결과 어긋나는 콘텐츠</li>
          <li style="color:var(--ink-light);font-size:11px;border-top:1px solid var(--border);margin-top:8px;padding-top:10px;"><b style="color:var(--ink-light);">출력</b> 후보별 6개 분석 필드 + 채택/탈락 + 사유</li>
        </ul>
      </div>
    </div>
  </details>

  <div class="stats" id="statTabs">
    {''.join(stat_cards)}
  </div>

  <div class="search-bar">
    <input type="text" id="searchInput" class="search-input"
           placeholder="현재 선택한 리스트에서 제목·채널·키워드 검색" autocomplete="off">
    <span class="search-count" id="searchCount"></span>
  </div>

  <div id="panelWrap">
    {''.join(panels)}
  </div>

  <div class="footer">
    <span>털어드림 · 자동 발송</span>
    <span>YouTube Data API v3 · Claude Opus 4.7 · {today_label} KST</span>
  </div>
</div>
<script>
(function() {{
  // JS 작동 환경 표시 → CSS가 탭/검색 UI를 노출 (JS 없으면 최종 발송만 보임)
  document.documentElement.classList.add('js');
  var INITIAL = 30, STEP = 50;
  var tabs = document.querySelectorAll('#statTabs .stat');
  var panels = document.querySelectorAll('.stage-panel');
  var searchInput = document.getElementById('searchInput');
  var searchCount = document.getElementById('searchCount');
  var shownLimit = {{}};  // stage -> 현재 표시 개수 한도

  function activePanel() {{
    for (var i = 0; i < panels.length; i++) {{
      if (!panels[i].classList.contains('is-hidden')) return panels[i];
    }}
    return panels[0];
  }}

  function items(panel) {{
    // 컴팩트 리스트는 .si, 최종 발송 패널은 .candidate
    var nodes = panel.querySelectorAll('.si, .candidate');
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
      if (!q || hay.indexOf(q) !== -1) matched.push(all[i]);
    }}
    var limit = q ? matched.length : (shownLimit[stage] || INITIAL);
    var shownN = 0;
    for (var j = 0; j < all.length; j++) all[j].classList.add('is-hidden');
    for (var k = 0; k < matched.length && k < limit; k++) {{
      matched[k].classList.remove('is-hidden');
      shownN++;
    }}
    // 더보기 버튼
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
        : ('<b>0개</b> — \\u2018' + q + '\\u2019 검색 결과 없음');
    }} else {{
      searchCount.innerHTML = '전체 <b>' + all.length + '개</b>';
    }}
    // empty-state (검색 결과 0)
    var emptyMsg = panel.querySelector('.search-empty');
    if (q && matched.length === 0) {{
      if (!emptyMsg) {{
        emptyMsg = document.createElement('div');
        emptyMsg.className = 'search-empty empty-state';
        emptyMsg.innerHTML = '<div class="big">\\uD83D\\uDD0D</div>현재 단계에서 \\u2018'
          + q.replace(/</g,'&lt;') + '\\u2019 검색 결과가 없습니다.';
        var list = panel.querySelector('.stage-list') || panel;
        list.appendChild(emptyMsg);
      }}
    }} else if (emptyMsg) {{
      emptyMsg.parentNode.removeChild(emptyMsg);
    }}
  }}

  function switchTab(stage) {{
    for (var i = 0; i < tabs.length; i++) {{
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-stage') === stage);
    }}
    for (var j = 0; j < panels.length; j++) {{
      panels[j].classList.toggle('is-hidden', panels[j].getAttribute('data-stage') !== stage);
    }}
    searchInput.value = '';  // 탭 전환 시 검색 초기화
    render();
  }}

  for (var t = 0; t < tabs.length; t++) {{
    (function(tab) {{
      tab.addEventListener('click', function() {{
        switchTab(tab.getAttribute('data-stage'));
      }});
    }})(tabs[t]);
  }}

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


def _render_extra_attachment_intro_tr():
    """첨부 2종(주간 + benchmark)일 때 메일 본문 최상단에 노출되는 안내 <tr>.

    Gmail/Outlook 호환을 위해 모든 스타일을 inline으로 유지.
    """
    return """
    <!-- 첨부 2종 안내 (benchmark report 동봉 시) -->
    <tr><td style="padding:26px 28px 18px;border-bottom:1px solid #e8e6e0;
        font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
        color:#0a0a0a;font-size:13px;line-height:1.75;">
      <p style="margin:0 0 12px;">안녕하세요, 박서은입니다.</p>
      <p style="margin:0 0 16px;">
        털어드림 격주 후보 리포트와 타 채널 벤치마크 리포트 첨부하여 전달드립니다.
      </p>
      <div style="font-size:12px;line-height:1.75;color:#444;margin:0 0 16px;
          padding:12px 14px;background:#faf9f6;border:1px solid #e8e6e0;border-radius:4px;">
        <div style="margin-bottom:8px;">
          <b style="color:#8b1e3f;">1. 격주 후보 리포트</b><br>
          <span style="margin-left:14px;color:#444;">· 이번 회차 실제 후보 확인용</span>
        </div>
        <div>
          <b style="color:#8b1e3f;">2. 타 채널 벤치마크 리포트</b><br>
          <span style="margin-left:14px;color:#444;">· 참고 채널의 소재, 훅, 기획 포인트 확인용</span>
        </div>
      </div>
      <p style="margin:0 0 8px;">
        내용 확인 후 문제 있거나 추가/수정이 필요한 부분이 있으면
        편하게 말씀 부탁드리겠습니다.
      </p>
      <p style="margin:0;color:#666;">감사합니다.</p>
    </td></tr>
    """


def render_email_html(top, meta, today_label, soft_excluded=None, waitlist=None,
                      has_extra_attachment=False):
    """이메일 본문 — Gmail/Outlook 호환 (table 레이아웃 + inline 스타일).

    Gmail은 CSS Grid/Flex/변수를 무시하므로 모든 스타일을 inline으로,
    레이아웃은 <table>로 짜야 깨지지 않음. 화려한 디자인은 첨부 HTML에 있음.
    """
    soft_excluded = soft_excluded or []
    waitlist = waitlist or []
    # ---- 후보 row들 (table 기반) ----
    rows = []
    for i, v in enumerate(top, 1):
        a = v.get("analysis", {})
        rows.append(f"""
        <tr>
          <td style="padding:14px 12px;border-bottom:1px solid #eee;vertical-align:top;width:40px;color:#8b1e3f;font-size:18px;font-weight:900;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">{i:02d}</td>
          <td style="padding:14px 12px;border-bottom:1px solid #eee;vertical-align:top;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="font-size:14px;font-weight:700;color:#0a0a0a;line-height:1.4;margin-bottom:4px;">
              <a href="{v['url']}" style="color:#0a0a0a;text-decoration:none;">{esc_html(v['title'])}</a>
            </div>
            <div style="font-size:11px;color:#666;margin-bottom:8px;">
              <span style="color:#8b1e3f;font-weight:600;">@{esc_html(v['channel'])}</span>
              &nbsp;·&nbsp; {v['published_at'][:10]} &nbsp;·&nbsp; {v['duration_seconds']}초
            </div>
            <div style="font-size:11px;color:#444;line-height:1.5;background:#fef2f4;border-left:3px solid #8b1e3f;padding:8px 10px;margin-top:6px;">
              <b style="color:#8b1e3f;">▸ 변형 주제:</b> {esc_html(a.get('variation_topic', ''))}
            </div>
          </td>
          <td style="padding:14px 12px;border-bottom:1px solid #eee;vertical-align:top;text-align:right;white-space:nowrap;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="font-size:18px;font-weight:900;color:#0a0a0a;">{fmt_views(v['view_count'])}<span style="font-size:11px;color:#888;font-weight:500;">회</span></div>
            <div style="font-size:10px;color:#aaa;margin-top:2px;">({v['view_count']:,})</div>
            <a href="{v['url']}" style="display:inline-block;margin-top:8px;padding:5px 10px;background:#8b1e3f;color:#fff;text-decoration:none;border-radius:4px;font-size:11px;font-weight:600;">열기 ↗</a>
          </td>
        </tr>""")

    # ---- 5칸 stats를 table로 ----
    stats_cells = [
        (str(meta['total_collected']), "수집", False),
        (str(meta.get('dedup_excluded', 0)), "중복 제외", False),
        (str(meta.get('hard_filter_excluded', 0)), "자동 컷", False),
        (str(meta['total_after_filter']), "Hard 통과", False),
        (str(meta.get('soft_filter_excluded', 0)), "AI 컷", False),
        (str(len(top)), "최종 발송", True),  # primary (검정 배경 + 노란 숫자)
    ]
    stats_tds = []
    for n, k, primary in stats_cells:
        bg = "#0a0a0a" if primary else "#ffffff"
        n_color = "#f5c518" if primary else "#0a0a0a"
        k_color = "rgba(255,255,255,0.55)" if primary else "#888"
        stats_tds.append(f"""
        <td width="16.66%" style="background:{bg};padding:16px 12px;text-align:left;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;border-right:1px solid #e8e6e0;">
          <div style="font-size:24px;font-weight:900;color:{n_color};letter-spacing:-0.02em;line-height:1;">{n}</div>
          <div style="font-size:10px;font-weight:600;color:{k_color};letter-spacing:0.04em;text-transform:uppercase;margin-top:6px;">{k}</div>
        </td>""")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f7f6f3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f7f6f3;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#fff;border:1px solid #e8e6e0;border-radius:8px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
    {_render_extra_attachment_intro_tr() if has_extra_attachment else ''}
    <!-- 헤더 -->
    <tr><td style="padding:32px 28px 24px;border-bottom:1px solid #e8e6e0;">
      <div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:#8b1e3f;text-transform:uppercase;margin-bottom:12px;">
        Biweekly Sourcing Report · {today_label}
      </div>
      <h1 style="margin:0 0 14px;font-size:32px;font-weight:900;letter-spacing:-0.03em;color:#0a0a0a;line-height:1.1;">
        털어드림 <span style="color:#8b1e3f;">격주 후보</span>
      </h1>
      <p style="margin:0;font-size:13px;color:#666;line-height:1.7;">
        최근 <b style="color:#0a0a0a;">1년</b> 풀 · 과거 발송 자동 중복 제외 ·
        조회수 <b style="color:#0a0a0a;">{CONFIG['MIN_VIEWS']//10000:,}만~{CONFIG['MAX_VIEWS']//10000:,}만</b> ·
        한국어 · Shorts · Claude 큐레이션 통과 <b style="color:#0a0a0a;">{len(top)}개</b>.
        <br>상세 분석(6개 필드)은 첨부 HTML 참조.
      </p>
    </td></tr>

    <!-- 5칸 stats (table 기반) -->
    <tr><td style="padding:0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
        <tr>{''.join(stats_tds)}</tr>
      </table>
    </td></tr>

    <!-- 필터 기준 (Hard / Soft) -->
    <tr><td style="padding:20px 28px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;border:1px solid #e8e6e0;border-radius:6px;overflow:hidden;">
        <tr>
          <td width="50%" style="padding:16px 18px;background:#fff;vertical-align:top;border-right:1px solid #e8e6e0;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="margin-bottom:10px;">
              <span style="display:inline-block;background:#0a0a0a;color:#fff;font-size:9px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;padding:2px 7px;border-radius:3px;">HARD</span>
              <b style="font-size:13px;color:#0a0a0a;margin-left:8px;">자동 메타데이터 컷</b>
            </div>
            <div style="font-size:11px;color:#444;line-height:1.7;">
              조회수 <b>{CONFIG['MIN_VIEWS']//10000:,}만~{CONFIG['MAX_VIEWS']//10000:,}만</b> ·
              Shorts(<b>{CONFIG['MAX_DURATION_SEC']}초</b>) ·
              한국어 ·
              <b>{CONFIG['LOOKBACK_DAYS_OLDEST']//30}개월 이내</b> 영상 (업로드 직후 포함) ·
              과거 발송 자동 중복 제외 ·
              직캠/풀캠/열애설/뮤비 메이킹 등 키워드 차단
            </div>
          </td>
          <td width="50%" style="padding:16px 18px;background:#fff;vertical-align:top;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="margin-bottom:10px;">
              <span style="display:inline-block;background:#fef2f4;color:#8b1e3f;font-size:9px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;padding:2px 7px;border-radius:3px;border:1px solid #8b1e3f;">SOFT</span>
              <b style="font-size:13px;color:#0a0a0a;margin-left:8px;">Claude 큐레이션 판정</b>
            </div>
            <div style="font-size:11px;color:#444;line-height:1.7;">
              직캠·뮤비·무대 자체 컷 ·
              시의성 강한 가십/근황 컷 ·
              무명 채널 컷 ·
              단순 짤·밈 컷 ·
              자극·낚시 톤 컷 ·
              분석/해설형 비하인드 톤만 채택
            </div>
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- 후보 리스트 -->
    <tr><td style="padding:24px 28px 16px;">
      <div style="font-size:12px;font-weight:700;color:#8b1e3f;letter-spacing:0.05em;margin-bottom:6px;">01 / FINAL</div>
      <h2 style="margin:0 0 16px;font-size:20px;font-weight:900;color:#0a0a0a;border-bottom:2px solid #0a0a0a;padding-bottom:8px;">
        최종 후보 {len(top)}선
      </h2>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
        {''.join(rows) if rows else '<tr><td style="padding:24px;text-align:center;color:#888;font-size:13px;">이번 주는 후보가 없었습니다.</td></tr>'}
      </table>
    </td></tr>

    {_render_email_excluded_section(soft_excluded)}

    {_render_email_waitlist_section(waitlist)}

    <!-- 푸터 -->
    <tr><td style="padding:16px 28px 24px;border-top:1px solid #e8e6e0;background:#f6f5f3;">
      <div style="font-size:11px;color:#888;line-height:1.6;">
        자동 발송 · YouTube Data API v3 · Claude Opus 4.7<br>
        {today_label} KST · {'첨부 2종 — 격주 후보 분석 + 타 채널 벤치마크 리포트' if has_extra_attachment else '첨부 HTML로 전체 분석 보기'}
      </div>
    </td></tr>

  </table>
</td></tr>
</table>
</body></html>"""


# ============================================================================
# 메일 발송 (Gmail SMTP)
# ============================================================================
def _render_email_waitlist_section(waitlist):
    """이메일 본문용 보류 후보 섹션 — table 기반, inline 스타일."""
    if not waitlist:
        return ""
    rows = []
    for i, v in enumerate(waitlist, 1):
        wl_type = v.get("waitlist_type", "")
        if wl_type == "soft_pass_below_top":
            status = "AI 통과했지만 조회수 순위 밀려 보류 → 다음 주 dedup 후 진입 가능"
        elif wl_type == "not_analyzed":
            status = "분석 미실시 (조회수로 밀림) → 다음 주 분석 풀 진입 가능"
        else:
            status = "다음 주 진입 가능"
        rows.append(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;width:36px;color:#9a9a9a;font-size:13px;font-weight:800;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">{i:02d}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="font-size:13px;font-weight:700;color:#2a2a2a;line-height:1.4;margin-bottom:4px;">
              <a href="{v['url']}" style="color:#2a2a2a;text-decoration:none;">{esc_html(v['title'])}</a>
            </div>
            <div style="font-size:11px;color:#888;margin-bottom:6px;">
              <span style="color:#8b1e3f;font-weight:600;">@{esc_html(v['channel'])}</span>
              &nbsp;·&nbsp; {fmt_views(v['view_count'])}회 &nbsp;·&nbsp; {v['published_at'][:10]}
            </div>
            <div style="font-size:11px;color:#444;line-height:1.5;background:#f4f7fb;border-left:3px solid #2a5278;padding:6px 10px;">
              <b style="color:#2a5278;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;">대기 사유 &nbsp;</b>
              {esc_html(status)}
            </div>
          </td>
        </tr>""")
    return f"""
    <tr><td style="padding:8px 28px 16px;">
      <div style="font-size:12px;font-weight:700;color:#2a5278;letter-spacing:0.05em;margin-bottom:6px;">03 / WAITLIST</div>
      <h2 style="margin:0 0 12px;font-size:18px;font-weight:900;color:#2a2a2a;border-bottom:1px solid #ddd;padding-bottom:6px;">
        다음 주 후보 풀 {len(waitlist)}개
      </h2>
      <p style="margin:0 0 12px;font-size:11px;color:#888;line-height:1.6;">
        이번 주 발송엔 못 들었지만 풀에 남아있는 후보. 다음 주에 dedup 또는 분석 풀 진입으로 final 가능성 있음.
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
        {''.join(rows)}
      </table>
    </td></tr>"""


def _render_email_excluded_section(soft_excluded):
    """이메일 본문용 soft 탈락 섹션 — table 기반, inline 스타일."""
    if not soft_excluded:
        return ""
    rows = []
    for i, v in enumerate(soft_excluded, 1):
        a = v.get("analysis", {})
        reason = a.get("exclusion_reason") or "(사유 누락)"
        rows.append(f"""
        <tr>
          <td style="padding:12px 12px;border-bottom:1px solid #eee;vertical-align:top;width:36px;color:#9a9a9a;font-size:13px;font-weight:800;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">{i:02d}</td>
          <td style="padding:12px 12px;border-bottom:1px solid #eee;vertical-align:top;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
            <div style="font-size:13px;font-weight:700;color:#2a2a2a;line-height:1.4;margin-bottom:4px;">
              <a href="{v['url']}" style="color:#2a2a2a;text-decoration:none;">{esc_html(v['title'])}</a>
            </div>
            <div style="font-size:11px;color:#888;margin-bottom:6px;">
              <span style="color:#8b1e3f;font-weight:600;">@{esc_html(v['channel'])}</span>
              &nbsp;·&nbsp; {v['published_at'][:10]} &nbsp;·&nbsp; {v['duration_seconds']}초 &nbsp;·&nbsp; {fmt_views(v['view_count'])}회
            </div>
            <div style="font-size:11px;color:#444;line-height:1.5;background:#fafaf8;border-left:3px solid #c44;padding:6px 10px;">
              <b style="color:#c44;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;">제외 사유 &nbsp;</b>
              {esc_html(reason)}
            </div>
          </td>
        </tr>""")
    return f"""
    <tr><td style="padding:8px 28px 16px;">
      <div style="font-size:12px;font-weight:700;color:#9a9a9a;letter-spacing:0.05em;margin-bottom:6px;">02 / EXCLUDED</div>
      <h2 style="margin:0 0 12px;font-size:18px;font-weight:900;color:#2a2a2a;border-bottom:1px solid #ddd;padding-bottom:6px;">
        Soft Filter 탈락 {len(soft_excluded)}선
      </h2>
      <p style="margin:0 0 12px;font-size:11px;color:#888;line-height:1.6;">
        Hard 통과했지만 Claude가 채널 톤과 어긋난다고 판정. 직캠/뮤비/낚시 등.
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
        {''.join(rows)}
      </table>
    </td></tr>"""


def parse_recipients(value):
    """RECIPIENT_EMAIL을 쉼표/세미콜론/공백으로 분리해 리스트 반환.

    예: "a@x.com, b@y.com; c@z.com" → ["a@x.com", "b@y.com", "c@z.com"]
    """
    if not value:
        return []
    parts = re.split(r"[,;\s]+", value.strip())
    return [p for p in parts if p and "@" in p]


def _load_extra_attachment():
    """BENCHMARK_REPORT_PATH 환경변수로 받은 추가 첨부 파일을 안전하게 로드.

    파일이 없거나 빈 값이거나 읽기 실패해도 weekly 발송은 막지 않는다 — None 반환.
    benchmark 모듈은 weekly와 독립이고 이 함수는 단순 파일 로더 — benchmark 코드를
    import하거나 동작에 영향을 주지 않는다.

    반환: [(filename, content_str)] 형태 리스트 (현재는 최대 1개), 없으면 None
    """
    path_str = (os.environ.get("BENCHMARK_REPORT_PATH") or "").strip()
    if not path_str:
        # 정상 흐름 (benchmark 첨부 없음) — 로그 노이즈 없이 그냥 None
        return None
    p = Path(path_str)
    if not p.exists() or not p.is_file():
        print(f"⚠️ benchmark report not attached — file missing: {p}",
              file=sys.stderr)
        return None
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        print(f"⚠️ benchmark report not attached — read failed: {e}",
              file=sys.stderr)
        return None
    if not content.strip():
        print(f"⚠️ benchmark report not attached — file is empty: {p}",
              file=sys.stderr)
        return None
    return [(p.name, content)]


def _build_email_message(env, subject, html_body,
                         attachment_html, attachment_filename,
                         extra_attachments=None):
    """MIME 메시지 객체를 구성 (SMTP 발송은 안 함). 단위 테스트 가능하도록 분리.

    extra_attachments: [(filename, content_str_or_bytes), ...] 또는 None
    반환: (MIMEMultipart msg, recipients list)
    """
    recipients = parse_recipients(env["RECIPIENT_EMAIL"])
    if not recipients:
        raise ValueError(f"RECIPIENT_EMAIL에 유효한 주소가 없습니다: {env['RECIPIENT_EMAIL']!r}")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr(("털어드림 자동화", env["GMAIL_ADDRESS"]))
    msg["To"] = ", ".join(recipients)

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(body)

    # 기본 첨부 (주간 후보 리포트)
    attach = MIMEApplication(attachment_html.encode("utf-8"), _subtype="html")
    attach.add_header("Content-Disposition", "attachment", filename=attachment_filename)
    msg.attach(attach)

    # 추가 첨부 (선택 — benchmark 등). 실패해도 weekly 발송 자체는 막지 않도록
    # 호출 전에 _load_extra_attachment() 단계에서 이미 필터링됨
    if extra_attachments:
        for fn, content in extra_attachments:
            data = content.encode("utf-8") if isinstance(content, str) else content
            ex = MIMEApplication(data, _subtype="html")
            ex.add_header("Content-Disposition", "attachment", filename=fn)
            msg.attach(ex)

    return msg, recipients


def send_email(env, subject, html_body, attachment_html, attachment_filename,
               extra_attachments=None):
    msg, recipients = _build_email_message(
        env, subject, html_body, attachment_html, attachment_filename,
        extra_attachments=extra_attachments,
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
        s.sendmail(env["GMAIL_ADDRESS"], recipients, msg.as_string())


def send_error_email(env, error_msg):
    """에러 발생 시 RECIPIENT_EMAIL로 알림. 메일 자체가 실패하면 그냥 패스."""
    try:
        recipients = parse_recipients(env["RECIPIENT_EMAIL"])
        if not recipients:
            return
        msg = MIMEMultipart()
        msg["Subject"] = "[털어드림 자동화] ❌ 주간 잡 실행 실패"
        msg["From"] = formataddr(("털어드림 자동화", env["GMAIL_ADDRESS"]))
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(error_msg, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"])
            s.sendmail(env["GMAIL_ADDRESS"], recipients, msg.as_string())
    except Exception as e:
        print(f"에러 메일 발송 자체 실패: {e}", file=sys.stderr)


# ============================================================================
# 메인
# ============================================================================
def main():
    log_path = setup_file_logging()
    env = load_env()
    today_kst = datetime.now(KST)
    today_label = today_kst.strftime("%Y-%m-%d (%a)")
    date_slug = today_kst.strftime("%Y-%m-%d")
    arc_path = HISTORY_DIR / f"{date_slug}.json"

    # 옵션: 오늘 history JSON이 이미 있으면 분석 스킵 (디버깅·메일 재발송용)
    # 강제 재분석: $env:FORCE_RESCAN="1" 또는 .env에 FORCE_RESCAN=1
    force_rescan = os.environ.get("FORCE_RESCAN", "").strip().lower() in ("1", "true", "yes")
    # DRY_RUN: 메일 발송 안 함, history 미변경, preview HTML만 local_output에 저장
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    # RESEND_LATEST: 가장 최근 history를 수집·분석 없이 메일만 재발송
    resend_latest = os.environ.get("RESEND_LATEST", "").strip().lower() in ("1", "true", "yes")

    report_data_path = LOCAL_OUTPUT_DIR / f"report_data_{date_slug}.json"

    print("=" * 60)
    print("털어드림 주간 후보 자동 수집 시작")
    print(f"실행 시각 (KST): {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"로그 파일: {log_path.relative_to(ROOT)}")
    if dry_run:
        print("⚠️  DRY_RUN 모드 — 메일 발송 안 함 / history 미변경")
    if resend_latest:
        print("📨 RESEND_LATEST 모드 — 최신 history 메일만 재발송 (수집·분석 없음)")
    print("=" * 60)

    try:
        bundle = None  # {top, meta, soft_excluded, waitlist, lookback_used, report_lists}

        # ============================================================
        # RESEND_LATEST 모드: 가장 최근 history를 수집·분석 없이 메일만 재발송
        # ============================================================
        if resend_latest:
            files = sorted(p for p in HISTORY_DIR.glob("*.json")
                           if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
            if not files:
                raise RuntimeError("재발송할 history 파일이 없습니다 (history/ 비어있음)")
            latest = files[-1]
            print(f"\n[RESEND] 최신 history 재발송 → {latest.name}")
            archive = json.loads(latest.read_text(encoding="utf-8"))
            cands = archive["candidates"]
            bundle = {
                "top": cands,
                "meta": archive["stats"],
                "soft_excluded": archive.get("soft_excluded", []),
                "waitlist": archive.get("waitlist", []),
                "lookback_used": archive["config"]["lookback_days_used"],
                "report_lists": archive.get("report_lists", {
                    "dedup_excluded": [], "hard_excluded": [], "hard_passed": cands,
                }),
            }
            # 메일 제목/파일명을 해당 history 날짜 기준으로
            date_slug = archive.get("date_kst", latest.stem)
            today_label = f"{date_slug} (재발송)"
            print(f"  로드: 최종 {len(cands)}개 ({date_slug} 분량)")

        # ============================================================
        # DRY_RUN 재렌더 캐시: 같은 날 report_data 캐시 있으면 파이프라인 스킵
        # ============================================================
        elif dry_run and report_data_path.exists() and not force_rescan:
            print(f"\n[DRY_RUN] report_data 캐시 발견 → 파이프라인 스킵, HTML만 재생성")
            print(f"  파일: {report_data_path.relative_to(ROOT)}")
            print(f"  💡 새로 수집하려면: $env:FORCE_RESCAN=\"1\" 추가")
            bundle = json.loads(report_data_path.read_text(encoding="utf-8"))
            print(f"  로드: 최종 {len(bundle['top'])}개, "
                  f"수집 {bundle['meta']['total_collected']}개")

        # ============================================================
        # RESUME 모드: 오늘자 history가 있으면 수집/분석 전부 스킵 (DRY_RUN 아닐 때)
        # ============================================================
        elif arc_path.exists() and not force_rescan and not dry_run:
            print(f"\n[RESUME] 오늘자 history 발견 → 수집·분석 스킵, 메일만 재발송")
            print(f"  파일: {arc_path.relative_to(ROOT)}")
            print(f"  💡 강제 재분석하려면: PowerShell에 $env:FORCE_RESCAN=\"1\" 후 실행")
            archive = json.loads(arc_path.read_text(encoding="utf-8"))
            cands = archive["candidates"]
            bundle = {
                "top": cands,
                "meta": archive["stats"],
                "soft_excluded": archive.get("soft_excluded", []),
                "waitlist": archive.get("waitlist", []),
                "lookback_used": archive["config"]["lookback_days_used"],
                # 구버전 history엔 report_lists 없음 → 빈 리스트로 graceful degrade
                "report_lists": archive.get("report_lists", {
                    "dedup_excluded": [], "hard_excluded": [], "hard_passed": cands,
                }),
            }
            print(f"  로드: 최종 {len(bundle['top'])}개, soft 탈락 "
                  f"{len(bundle['soft_excluded'])}개, 보류 {len(bundle['waitlist'])}개")

        # ============================================================
        # 풀 파이프라인 — 수집 → 분석 → 큐레이션
        # ============================================================
        else:
            if force_rescan and arc_path.exists():
                print(f"\n[FORCE_RESCAN] 기존 history 무시하고 새로 분석합니다")

            # 0. 과거 발송 영상 메타데이터 로드 (중복 제외용). 오늘자 history는 skip.
            seen_meta = load_seen_video_meta(skip_filename=arc_path.name)
            print(f"\n[STEP 0] 과거 발송 영상: {len(seen_meta)}개 (중복 제외 풀)")

            # 1. 주 검색 — 업로드 직후 ~ 1년 이내 (26.07.13 정책 변경)
            oldest = CONFIG["LOOKBACK_DAYS_OLDEST"]
            newest = CONFIG["LOOKBACK_DAYS_NEWEST_PRIMARY"]
            print(f"\n[STEP 1] {newest}일 전 ~ {oldest}일 전 사이 영상 수집")
            result = collect(env["YOUTUBE_API_KEY"], oldest, newest, seen_meta=seen_meta)
            candidates = result["candidates"]
            lookback_used = f"{newest}~{oldest}일 전"

            # 1.5. Hard pass 부족 시 fallback (현재 primary=fallback=0이라 실질 no-op — 정책 변경 시 값만 조정)
            if len(candidates) < CONFIG["MIN_HARD_PASS"]:
                fallback_newest = CONFIG["LOOKBACK_DAYS_NEWEST_FALLBACK"]
                print(f"\n[STEP 1b] hard pass 부족 ({len(candidates)} < {CONFIG['MIN_HARD_PASS']})"
                      f" → {fallback_newest}일 전까지 확장 재검색")
                result = collect(env["YOUTUBE_API_KEY"], oldest, fallback_newest, seen_meta=seen_meta)
                candidates = result["candidates"]
                lookback_used = f"{fallback_newest}~{oldest}일 전 (fallback)"

            # 2. Claude 분석 비용 상한 — 조회수 상위 N개만 분석
            to_analyze = candidates[:CONFIG["MAX_ANALYSIS_CANDIDATES"]]
            if len(candidates) > len(to_analyze):
                print(f"\n[STEP 2] 분석 상한 적용: {len(candidates)} → 상위 {len(to_analyze)}개만 Claude 분석 (비용 컨트롤)")

            # 3. Claude API로 8개 필드 + soft filter 판정
            analyzed = analyze_all(env["ANTHROPIC_API_KEY"], list(to_analyze)) if to_analyze else []

            # 4. Soft filter 적용 + 탈락자 별도 보존 (사유 표시용)
            soft_passed = [c for c in analyzed if c.get("analysis", {}).get("should_include", True)]
            soft_excluded_list = [c for c in analyzed if not c.get("analysis", {}).get("should_include", True)]
            soft_excluded_count = len(soft_excluded_list)
            soft_passed.sort(key=lambda x: -x["view_count"])
            soft_excluded_list.sort(key=lambda x: -x["view_count"])
            top = soft_passed[:CONFIG["TARGET_CANDIDATES"]]

            # 4.5. Waitlist — 다음 주 진입 가능성 있는 보류 후보 풀
            waitlist_soft = soft_passed[CONFIG["TARGET_CANDIDATES"]:]   # soft pass했지만 조회수 밀림
            waitlist_unanalyzed = candidates[CONFIG["MAX_ANALYSIS_CANDIDATES"]:]  # 분석 미실시
            for c in waitlist_soft:
                c["waitlist_type"] = "soft_pass_below_top"
            for c in waitlist_unanalyzed:
                c["waitlist_type"] = "not_analyzed"
            waitlist = waitlist_soft + waitlist_unanalyzed
            waitlist.sort(key=lambda x: -x["view_count"])

            # 4.6. 각 hard-pass 후보에 최종 상태(status_label) 태깅 — 리포트 탭 표시용
            final_ids = {c["video_id"] for c in top}
            soft_ids = {c["video_id"] for c in soft_excluded_list}
            wl_ids = {c["video_id"] for c in waitlist}
            for c in candidates:
                vid = c.get("video_id")
                if vid in final_ids:
                    c["status_label"] = "최종 발송"
                elif vid in soft_ids:
                    c["status_label"] = "AI 컷"
                elif vid in wl_ids:
                    c["status_label"] = "대기"
                else:
                    c["status_label"] = "미분석"
            for c in soft_excluded_list:
                c["exclusion_reason"] = c.get("analysis", {}).get("exclusion_reason", "")
                c["status_label"] = "AI 컷"

            meta = {
                "lookback_days": lookback_used,
                "total_collected": result["total_collected"],
                "dedup_excluded": result["dedup_excluded"],
                "total_after_filter": len(candidates),
                "hard_filter_excluded": (result["total_collected"]
                                         - result["dedup_excluded"]
                                         - len(candidates)),
                "soft_filter_excluded": soft_excluded_count,
                "final_count": len(top),
            }
            print(f"\n[STATS] Collected {meta['total_collected']} → "
                  f"Dedup {meta['dedup_excluded']} → "
                  f"Hard pass {meta['total_after_filter']} (hard excl {meta['hard_filter_excluded']}) → "
                  f"Soft pass {len(soft_passed)} (soft excl {soft_excluded_count}) → Final {len(top)}")

            # 단계별 전체 리스트 (리포트 탭 렌더링용) — collected/final/soft는 JS에서 파생
            report_lists = {
                "dedup_excluded": result["dedup_list"],
                "hard_excluded": result["hard_excluded"],
                "hard_passed": candidates,
            }
            bundle = {
                "top": top, "meta": meta, "soft_excluded": soft_excluded_list,
                "waitlist": waitlist, "lookback_used": lookback_used,
                "report_lists": report_lists,
            }

        # ── bundle 언팩 ──
        top = bundle["top"]
        meta = bundle["meta"]
        soft_excluded_list = bundle["soft_excluded"]
        waitlist = bundle["waitlist"]
        lookback_used = bundle["lookback_used"]
        report_lists = bundle["report_lists"]
        attachment_filename = f"teoldeurim_{date_slug}.html"

        # 추가 첨부 (선택): BENCHMARK_REPORT_PATH 환경변수로 지정된 경우만
        # 파일 없거나 실패해도 weekly 발송 자체는 영향 X — None으로 fallback
        extra_attachments = _load_extra_attachment()
        if extra_attachments:
            print(f"  📎 추가 첨부: {extra_attachments[0][0]} "
                  f"({len(extra_attachments[0][1]):,} bytes)")

        # ============================================================
        # DRY_RUN: 메일 발송 안 함, history 미변경, preview만 저장
        # ============================================================
        if dry_run:
            LOCAL_OUTPUT_DIR.mkdir(exist_ok=True)
            report_data_path.write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            preview_path = LOCAL_OUTPUT_DIR / f"preview_{date_slug}.html"
            preview_path.write_text(
                render_standalone_report(top, meta, today_label,
                                         soft_excluded_list, waitlist, report_lists),
                encoding="utf-8")
            # DRY_RUN에서도 본문 미리보기 (intro/footer 분기 확인용)
            body_preview_path = LOCAL_OUTPUT_DIR / f"preview_email_body_{date_slug}.html"
            body_preview_path.write_text(
                render_email_html(top, meta, today_label,
                                  soft_excluded_list, waitlist,
                                  has_extra_attachment=bool(extra_attachments)),
                encoding="utf-8")
            print(f"\n[DRY_RUN] report_data 캐시 저장: {report_data_path.relative_to(ROOT)}")
            print(f"[DRY_RUN] 미리보기 HTML 저장: {preview_path.relative_to(ROOT)}")
            print(f"[DRY_RUN] 메일 본문 미리보기: {body_preview_path.relative_to(ROOT)}")
            if extra_attachments:
                # benchmark HTML도 local_output에 복사해 시각 확인 가능
                extra_preview = LOCAL_OUTPUT_DIR / extra_attachments[0][0]
                extra_preview.write_text(extra_attachments[0][1], encoding="utf-8")
                print(f"[DRY_RUN] 추가 첨부 미리보기: {extra_preview.relative_to(ROOT)}")
            print(f"[DRY_RUN] ⚠️ 메일 발송 SKIP — 실제 수신자에게 발송 안 됨")
            print(f"[DRY_RUN] ⚠️ history 파일 변경 SKIP — {arc_path.name} 그대로")
            print("\n" + "=" * 60)
            print(f"DRY_RUN 완료: 최종 {len(top)}개 (메일 미발송)")
            print("=" * 60)
            return

        # ============================================================
        # 일반 모드 — history 저장 (파이프라인 새로 돌렸을 때만) + 메일 발송
        # RESEND_LATEST 모드는 history 절대 안 건드림
        # ============================================================
        if not resend_latest and ((not arc_path.exists()) or force_rescan):
            print(f"\n[STEP 5] history 아카이브")
            archive = {
                "date_kst": date_slug,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "min_views": CONFIG["MIN_VIEWS"],
                    "max_views": CONFIG["MAX_VIEWS"],
                    "max_duration_seconds": CONFIG["MAX_DURATION_SEC"],
                    "lookback_days_used": lookback_used,
                },
                "stats": meta,
                "candidates": top,
                "soft_excluded": soft_excluded_list,
                "waitlist": waitlist,
                "report_lists": report_lists,
            }
            arc_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✅ {arc_path.relative_to(ROOT)}")

        # 메일 발송
        print(f"\n[STEP 4] 메일 발송 → {env['RECIPIENT_EMAIL']}")
        send_email(
            env,
            subject=f"[털어드림 격주 후보] {today_label} · 신규 {len(top)}개",
            html_body=render_email_html(top, meta, today_label,
                                        soft_excluded_list, waitlist,
                                        has_extra_attachment=bool(extra_attachments)),
            attachment_html=render_standalone_report(top, meta, today_label,
                                                     soft_excluded_list, waitlist, report_lists),
            attachment_filename=attachment_filename,
            extra_attachments=extra_attachments,
        )
        attach_n = 1 + (len(extra_attachments) if extra_attachments else 0)
        print(f"  ✅ 메일 발송 완료: {len(top)}개 (첨부 {attach_n}종)")

        print("\n" + "=" * 60)
        print(f"완료: 최종 {len(top)}개 발송 ({lookback_used} 기준)")
        print("=" * 60)

    except Exception as e:
        err = f"실행 중 에러:\n\n{e}\n\n{traceback.format_exc()}"
        print(err, file=sys.stderr)
        send_error_email(env, err)
        sys.exit(1)


if __name__ == "__main__":
    main()
