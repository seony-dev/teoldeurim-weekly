# -*- coding: utf-8 -*-
"""
털어드림 통합 리포트 오케스트레이터.

책임:
  - 월·수·금 최근 콘텐츠 (--mode=monday): PROFILE=recent 로 benchmark + weekly recent 실행
        (2026-07-21 확장 — mode 이름은 backward compat 상 monday 유지, 실제로는 월·수·금 공용)
  - 격주 금요일 (--mode=friday):
      1) benchmark 실행 (PROFILE=standard)     ← 먼저!
      2) weekly 실행 (SEND_EMAIL_DISABLED=1)
      3) 상위 2탭 shell HTML 조립
      4) 발송
      5) 발송 성공 후 benchmark 공용 sent history 기록

- weekly.py / benchmark.py 를 subprocess로 호출 → 스크립트 독립성 유지
- 발송 성공 후에만 benchmark/history/sent/ 에 기록 (실패 시 미기록)
- weekly history 는 weekly.py 가 자체 로직으로 저장 (건드리지 않음)
- benchmark 실패 시 weekly-only 발송 fallback
- stale 파일 방지: 매 실행마다 tmp fragment 경로를 사전에 정리
"""

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 콘솔 한국어 출력 깨짐 방지 (Windows CP949 → UTF-8)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
CONFIG_DIR = ROOT / "config"
LOCAL_OUTPUT_DIR = ROOT / "local_output"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from emailer import send_email, send_error_email, parse_recipients  # noqa: E402
import benchmark_config  # noqa: E402

KST = timezone(timedelta(hours=9))


# ============================================================================
# 유틸
# ============================================================================
def _log(msg):
    print(msg, flush=True)


_REPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_report_date(value):
    """엄격 YYYY-MM-DD 검증 (zero-padded, 10글자, 실제 존재 날짜).

    통과하면 원본 문자열 반환. 실패면 SystemExit.
    (`2026-2-3` 처럼 zero-padding 안 된 값은 파일명 regex 와 어긋나므로 거부)
    """
    if not _REPORT_DATE_RE.match(value):
        raise SystemExit(
            f"❌ REPORT_DATE 형식 오류: {value!r} — 정확한 YYYY-MM-DD (zero-padded) 필요"
        )
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(
            f"❌ REPORT_DATE 형식 오류: {value!r} — 존재하지 않는 날짜"
        )
    return value


def _now_date_slug():
    """리포트 slot 날짜 (파일명·subject·history namespace).

    REPORT_DATE env가 있으면 그 값을 slot으로 사용 (YYYY-MM-DD 엄격 검증).
    비어 있으면 KST 실행 당일. 실제 실행 시각·age·generated_at 과는 무관.
    """
    override = (os.environ.get("REPORT_DATE") or "").strip()
    if override:
        return _validate_report_date(override)
    return datetime.now(KST).strftime("%Y-%m-%d")


def _notice_html(notice_raw):
    """NOTICE env → HTML 안내 박스. 비면 빈 문자열.

    안전한 처리 순서 (이 순서를 절대 뒤바꾸지 말 것 — 사용자 입력을 먼저 HTML로
    해석하면 XSS 로 이어짐):

      (1) literal '\\n' (역슬래시 + n 두 글자, workflow_dispatch string input UI 는 실제
          개행을 못 넣으므로 이 조합으로 들어옴) → 실제 개행 문자로 정규화
      (2) 실제 개행으로 split — 결과 line 들에는 개행이 남지 않음
      (3) 각 line 을 html.escape 로 escape (& < > " ' 모두 이스케이프)
      (4) 이스케이프된 각 line 을 우리 소유의 안전한 마크업 '<br>' 로 join

    이 순서 덕에 우리가 삽입하는 '<br>' 는 절대 이스케이프되지 않고, 반대로
    사용자 입력에 담긴 어떤 HTML 도 실제 태그로 해석되지 않는다.
    """
    if not notice_raw or not notice_raw.strip():
        return ""
    text = notice_raw.replace("\\n", "\n")            # (1) literal 정규화
    lines = [_html.escape(ln) for ln in text.split("\n")]  # (2) split → (3) escape
    body = "<br>".join(lines)                          # (4) 안전한 <br> join
    return (
        '<div style="background:#fff8e1;border-left:4px solid #f6b73c;'
        'padding:12px 16px;margin:0 0 20px 0;border-radius:4px;'
        'font-size:14px;line-height:1.6;color:#5a3a00;">'
        f'{body}'
        '</div>'
    )


def _reissue_prefix():
    """REISSUE=truthy 이면 '[재발송] ' 반환. friday subject 에만 사용."""
    v = (os.environ.get("REISSUE") or "").strip().lower()
    return "[재발송] " if v in ("1", "true", "yes") else ""


def _is_test_mode():
    """TEST_MODE=1/true/yes 이면 True. 검증용 workflow (recipient=RECIPIENT_EMAIL_SELF) 에서 설정.

    영향:
      · subject 맨 앞에 '[TEST] ' 접두어 추가 (기존 _reissue_prefix 앞에 붙음)
      · 발송 자체는 정상 수행 (SMTP OK · Gmail 실제 발송)
      · production sent history 기록은 skip (benchmark/history/sent/{target}/ 저장 X)
      · 결과: 검증 완료 후에도 후보 dedup pool 이 오염되지 않음

    운영 전환 시엔 workflow yml 에서 TEST_MODE env 만 걷어내면 원상복구.
    """
    v = (os.environ.get("TEST_MODE") or "").strip().lower()
    return v in ("1", "true", "yes")


def _test_prefix():
    """TEST_MODE 이면 '[TEST] ' 반환 (subject 맨 앞)."""
    return "[TEST] " if _is_test_mode() else ""


def _env_snapshot():
    """수신자·SMTP 관련 env 로드 (weekly.load_env 대신 최소 세트만)."""
    keys = ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL"]
    env = {}
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if not v:
            raise RuntimeError(f"환경변수 {k}가 비어 있습니다.")
        env[k] = v
    return env


def _load_dotenv_if_present():
    """ROOT/.env 있으면 로드 (이미 있는 키는 유지)."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ============================================================================
# subprocess 실행
# ============================================================================
def _run_child(script, extra_env, timeout=1200):
    """자식 스크립트 실행. 표준 출력/에러는 그대로 상속 (실행 흐름 관찰용).

    returncode == 0 이면 성공.
    """
    env = os.environ.copy()
    env.update(extra_env)
    _log(f"\n▶️ subprocess: {script.name}  env-overrides={sorted(extra_env.keys())}")
    r = subprocess.run(
        [sys.executable, "-u", str(script)],
        cwd=str(ROOT), env=env, timeout=timeout,
    )
    _log(f"   ← exit code {r.returncode}")
    return r.returncode


# ============================================================================
# (2026-08-24) Weekly + Benchmark 통합 shell 조립 로직 (`_extract_fragment` /
# `_build_shell_html` / CSS scope helper 전체) 는 대표님 요청으로 Weekly 파트를
# 리포트에서 제외하면서 dead code 가 되어 제거되었습니다.
#
# 이후 리포트는 benchmark.py 가 자체 생성한 HTML 을 그대로 첨부합니다. Weekly
# 재활성화가 필요하면 이 커밋 이전 revision 을 참고하세요.
# ============================================================================


# ============================================================================
# benchmark sent history 저장 (발송 성공 후에만)
# ============================================================================
def _record_benchmark_sent(profile, date_slug, video_ids, target_slug=None):
    """benchmark/history/sent/{target}/{date}_{profile}.json 생성.

    · target_slug 를 넘기면 target namespace 서브디렉에 저장 (신규 구조).
      target_slug=None 또는 "teoldeurim" 이면서 profile in {recent, standard}
      인 legacy 호출은 backward compat 로 flat 위치에 저장 — 단, 이후에는 사용 안 함.
    · legacy call: _record_benchmark_sent(profile, date_slug, video_ids)  → flat 저장
    · new    call: _record_benchmark_sent(profile, date_slug, video_ids, target_slug="jjalduk")
                                                                → benchmark/history/sent/jjalduk/*.json
    """
    if _is_test_mode():
        _log(f"  ⚠️ TEST_MODE — production sent history 저장 SKIP "
             f"(target={target_slug!r}, profile={profile!r}, video_ids={len(video_ids)}개)")
        return None
    if not video_ids:
        _log("  ⚠️ 기록할 video_id 없음 (발송 후보 0개) — sent history 저장 skip")
        return None
    root = ROOT / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
    if target_slug:
        sent_dir = root / target_slug
    else:
        sent_dir = root
    sent_dir.mkdir(parents=True, exist_ok=True)
    p = sent_dir / f"{date_slug}_{profile}.json"
    payload = {
        "date_kst": date_slug,
        "profile": profile,
        "target": target_slug or "",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates_video_ids": list(video_ids),
        "count": len(video_ids),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  💾 benchmark sent history 저장: {p.relative_to(ROOT)}")
    return p


def _load_benchmark_result_ids(raw_json_path):
    """benchmark/{date}_filtered_raw.json에서 direct_final + benchmark_final의 video_id 합집합 반환."""
    if not raw_json_path.exists():
        return []
    try:
        d = json.loads(raw_json_path.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"  ⚠️ benchmark raw json 읽기 실패: {e}")
        return []
    stages = d.get("stages") or {}
    ids = []
    seen = set()
    for key in ("direct_final", "benchmark_final"):
        for v in (stages.get(key) or []):
            vid = v.get("video_id")
            if vid and vid not in seen:
                ids.append(vid)
                seen.add(vid)
    return ids


# ============================================================================
# 본문 조립 (정상 0건 vs benchmark 실패는 이 함수 밖에서 이미 분기됨)
# ============================================================================
def _monday_body_html(date_slug, bm_count):
    """월·수·금 recent 본문. Benchmark recent 전용 (2026-08-24 Weekly 제거).

    · 이전 시그니처: (date_slug, wk_count, bm_count, wk_ok, bm_ok)
    · 이번 시그니처: (date_slug, bm_count)
      → 발송은 benchmark 성공 시에만 진행되므로 실패 fallback 은 orchestrator raise.
    """
    zero_block = ""
    if bm_count == 0:
        zero_block = (
            "<p><b>ℹ️ 이번 실행 결과 신규 후보 0건입니다.</b> "
            "수집·필터링·분석은 정상 완료되었으며, 조건에 맞는 새 영상이 없습니다.</p>"
        )

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
<p>안녕하세요, 박서은입니다.</p>
<p><b>최근 콘텐츠 리포트</b>를 첨부드립니다. (0일 ~ 7일 이내 신규 콘텐츠 · 월·수·금 3회 발송)</p>
<ul>
  <li>Benchmark 최근 후보: {bm_count}건 (참고 채널 인기 Shorts 기반)</li>
  <li>조건: 조회수 5만~300만, 업로드 0~7일</li>
  <li>중복 제외: 이전 Benchmark 리포트 발송 이력</li>
</ul>
{zero_block}
<p>첨부 HTML 에서 상세 후보를 확인해주세요.</p>
</body></html>"""


def _friday_body_html(date_slug, bm_count, notice_html=""):
    """격주 금 · 털어드림 Standard 본문 (Weekly Bundle 과 별도 메일).

    notice_html 은 최상단에 삽입될 안내 박스 (비어 있으면 미노출).
    """
    zero_block = ""
    if bm_count == 0:
        zero_block = (
            "<p style='background:#fffbeb;border-left:3px solid #d97706;"
            "padding:8px 12px;margin:12px 0;border-radius:0 4px 4px 0'>"
            "ℹ️ <b>이번 격주 신규 후보 0건입니다.</b> "
            "수집·필터링·AI 분석은 정상 완료되었으며, 조건에 맞는 새 영상이 없습니다.</p>"
        )
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;line-height:1.6;color:#111;max-width:640px">
{notice_html}<p>안녕하세요, 박서은입니다.</p>
<p>이번 격주 <b>털어드림 Standard Benchmark 리포트</b> 를 첨부드립니다. Weekly (매주 금 발송) 와 별개로, <b>업로드 30일 초과 ~ 1년 이내 · 조회수 50만~900만</b> 조건의 후보를 뽑아드리는 격주 리포트입니다.</p>

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">📎 이번 리포트</h3>
<ul style="margin:4px 0 12px 20px">
  <li><b>털어드림 Standard</b> · 이번 격주 후보 <b>{bm_count}건</b> (첨부 HTML 참조)</li>
</ul>
{zero_block}

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">🔎 후보 선정 방식</h3>
<ul style="margin:4px 0 12px 20px">
  <li>털어드림 Reference 7개 채널의 인기 Shorts 를 수집한 뒤 조건 통과 영상 중 <b>조회수 상위 최대 30개</b> 를 AI 로 분석합니다. 조건 통과가 30개보다 적으면 실제 남은 후보만 분석합니다.</li>
  <li>AI 분석에서 <b>직접 활용 후보 최대 5개</b> + <b>Benchmark 활용 후보 최대 5개</b> 를 각각 뽑습니다 (상호 배타적, 실제 노출 0~10건 범위).</li>
  <li>이전에 이미 발송된 후보는 Weekly · Standard 통틀어 자동 제외됩니다.</li>
</ul>

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">💡 참고 사항</h3>
<ul style="margin:4px 0 12px 20px">
  <li>영상 <b>제목은 유튜브 원본(한국어)</b> 을 사용합니다.</li>
  <li>리포트의 <b>공통 패턴·리프트·소재 인사이트는 상관관계 기반 관찰</b> 로, 정답이나 성과 예측값이 아닙니다. <b>기획 참고 및 A/B 테스트 신호</b> 로만 활용해주세요.</li>
</ul>

<p style="margin-top:20px;color:#71717a;font-size:12px">— 자동화 봇 · {date_slug} KST</p>
</body></html>"""


# ============================================================================
# mode: monday (recent)
# ============================================================================
def run_monday(date_slug, tmp_dir, dry_run=False):
    """월·수·금 recent 리포트: Benchmark recent 전용 (2026-08-24 대표님 요청으로 Weekly 제거).

    흐름: benchmark recent 실행 → HTML 그대로 발송 → 성공 시 sent history 기록.

    이전 (Weekly + Benchmark 통합) 구조:
      benchmark → weekly → 상위 2탭 shell 조립 → 발송
    이번 변경 (Benchmark 전용):
      benchmark → HTML 그대로 → 발송

    weekly.py 자체는 남겨두어 향후 재활성화 시 활용 가능. workflow_dispatch
    옵션 (recipient_override / report_date / notice / reissue / exclude_top_n_views)
    은 그대로 유지.
    """
    profile = "recent"
    bm_frag_path = tmp_dir / f"{date_slug}_bm_{profile}.html"
    if bm_frag_path.exists():
        bm_frag_path.unlink()  # stale 방지

    # benchmark recent 실행
    # 2026-08-21: 참고 채널 10 → 22개 확장 + Claude 분석 상한 15 → 30 상향 후 첫 실행이
    # ~14분 걸림. 안전 마진 포함해 timeout 900 → 1800 초 (30분) 로 상향.
    bm_rc = _run_child(
        SCRIPTS_DIR / "benchmark.py",
        extra_env={
            "PROFILE": profile,
            "REPORT_FRAGMENT_PATH": str(bm_frag_path),
        },
        timeout=1800,
    )
    bm_ok = (bm_rc == 0) and bm_frag_path.exists()
    if not bm_ok:
        raise RuntimeError(
            f"Monday recent: benchmark 실패 (rc={bm_rc}, fragment={bm_frag_path.exists()}) "
            f"— 리포트 조립 불가"
        )

    bm_html = bm_frag_path.read_text(encoding="utf-8")
    bm_raw_path = ROOT / "benchmark" / f"{date_slug}_{profile}_filtered_raw.json"
    bm_video_ids = _load_benchmark_result_ids(bm_raw_path)
    bm_count = len(bm_video_ids)

    # subject — Benchmark 전용 (Weekly 카운트 제거)
    if bm_count == 0:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · 신규 후보 없음"
    else:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · Benchmark {bm_count}건"
    subject = _test_prefix() + subject

    body_html = _monday_body_html(date_slug, bm_count)

    # preview 저장 (첨부용 HTML = benchmark 자체 HTML 그대로)
    preview_path = LOCAL_OUTPUT_DIR / f"preview_monday_{date_slug}.html"
    preview_path.write_text(bm_html, encoding="utf-8")
    body_preview_path = LOCAL_OUTPUT_DIR / f"preview_monday_body_{date_slug}.html"
    body_preview_path.write_text(body_html, encoding="utf-8")
    _log(f"  💾 preview 저장: {preview_path.relative_to(ROOT)}")
    _log(f"  💾 body preview: {body_preview_path.relative_to(ROOT)}")

    # DRY_RUN 분기 — 발송·sent history 기록 skip
    if dry_run:
        _log("  ⚠️ DRY_RUN — Gmail 발송 SKIP / benchmark sent history 기록 SKIP")
        _log(f"     상태: bm_count={bm_count}, subject={subject!r}")
        return

    env = _env_snapshot()
    _log(f"\n📮 [monday] 발송 → {env['RECIPIENT_EMAIL']}")
    send_email(
        env,
        subject=subject,
        html_body=body_html,
        attachment_html=bm_html,
        attachment_filename=f"teoldeurim_recent_{date_slug}.html",
    )
    _log("  ✅ 발송 완료")

    # 발송 성공 후 benchmark sent history 기록
    _record_benchmark_sent(profile, date_slug, bm_video_ids)


# ============================================================================
# mode: weekly-bundle — 매주 금 3-target Weekly Bundle (2026-08-25 신설)
#   · target=teoldeurim / myohanduk / jjalduk 각각 mode=weekly 실행
#   · HTML 3개 생성 성공 후에만 Gmail 1통 발송 (첨부 3개)
#   · 부분 실패 시 발송·sent history 기록 모두 skip
# ============================================================================
_WEEKLY_BUNDLE_TARGETS = [
    ("teoldeurim", "털어드림"),
    ("myohanduk",  "묘한덕질"),
    ("jjalduk",    "짤덕방"),
]


def _run_target_weekly(date_slug, tmp_dir, target_slug):
    """단일 target × mode=weekly 실행. 성공 시 (html, video_ids) 반환, 실패 시 None."""
    bm_frag_path = tmp_dir / f"{date_slug}_{target_slug}_weekly.html"
    if bm_frag_path.exists():
        bm_frag_path.unlink()
    rc = _run_child(
        SCRIPTS_DIR / "benchmark.py",
        extra_env={
            "TARGET": target_slug,
            "MODE": "weekly",
            # backward compat: teoldeurim/weekly → PROFILE=recent (기존 파일명 유지)
            "PROFILE": "recent" if target_slug == "teoldeurim" else "weekly",
            "REPORT_FRAGMENT_PATH": str(bm_frag_path),
        },
        timeout=1800,
    )
    ok = (rc == 0) and bm_frag_path.exists()
    if not ok:
        _log(f"  ⚠️ Weekly Bundle · {target_slug}: benchmark 실패 "
             f"(rc={rc}, fragment={bm_frag_path.exists()})")
        return None
    html = bm_frag_path.read_text(encoding="utf-8")
    # filtered_raw 위치 규칙: teoldeurim 은 기존 형식 (_recent), 그 외는 _weekly
    if target_slug == "teoldeurim":
        raw_path = ROOT / "benchmark" / f"{date_slug}_recent_filtered_raw.json"
    else:
        raw_path = ROOT / "benchmark" / f"{date_slug}_{target_slug}_weekly_filtered_raw.json"
    video_ids = _load_benchmark_result_ids(raw_path)
    return {"html": html, "video_ids": video_ids, "raw_path": raw_path}


def _weekly_bundle_body_html(date_slug, counts, notice_html=""):
    """Weekly Bundle 본문 — 사람이 바로 이해할 수 있게 작성.

    counts: [("털어드림", N), ("묘한덕질", N), ("짤덕방", N)]
    """
    count_lines = "".join(
        f'  <li><b>{_html.escape(name)}</b> · 이번 주 후보 <b>{c}건</b> '
        f'(첨부 HTML 참조)</li>'
        for name, c in counts
    )
    zero_note = ""
    if all(c == 0 for _, c in counts):
        zero_note = (
            "<p style='background:#fffbeb;border-left:3px solid #d97706;"
            "padding:8px 12px;margin:12px 0;border-radius:0 4px 4px 0'>"
            "ℹ️ <b>3개 채널 모두 이번 주 신규 후보 0건입니다.</b> "
            "수집·필터링·AI 분석은 정상 완료되었으며, 조건에 맞는 새 영상이 없습니다.</p>"
        )
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;line-height:1.6;color:#111;max-width:640px">
{notice_html}<p>안녕하세요, 박서은입니다.</p>
<p>이번 주 <b>Weekly Benchmark 리포트</b> 3개 채널분을 한 통에 모아 보내드립니다. 첨부된 HTML 파일 3개 (각 채널별) 에서 상세 후보와 기획 포인트를 확인하실 수 있습니다.</p>

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">📎 첨부된 채널 리포트</h3>
<ul style="margin:4px 0 12px 20px">
{count_lines}
</ul>
{zero_note}

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">🔎 이번 주 후보 선정 방식</h3>
<ul style="margin:4px 0 12px 20px">
  <li>각 채널은 <b>자기 채널의 성격·운영 방향·Reference 채널 목록</b> 을 기준으로 후보를 <b>별도로</b> 분석합니다. 세 채널 후보는 서로 섞이지 않습니다.</li>
  <li>Reference 채널의 인기 Shorts 를 수집한 뒤 조회수·업로드 기간·길이 조건을 통과한 영상 중 <b>조회수 상위 최대 30개</b> 를 AI 로 분석합니다. 조건 통과 후보가 30개보다 적으면 남은 후보 수만큼만 분석합니다.</li>
  <li>AI 분석 결과에서 두 축으로 후보를 뽑습니다:
    <ul style="margin:2px 0 2px 18px">
      <li><b>직접 활용 후보 최대 5개</b> — 우리 채널에 그대로 (또는 재해석해) 가져갈 만한 영상</li>
      <li><b>Benchmark 활용 후보 최대 5개</b> — 훅·제목 구조·팬덤 반응 등 재사용 가치가 높은 참고 영상</li>
    </ul>
    각각 최대 5개까지이며 두 리스트는 상호 배타적입니다. 실제 노출 수는 분석 결과에 따라 <b>0~10건</b> 범위에서 결정됩니다.</li>
  <li>이전에 이미 발송된 후보는 채널별로 자동 제외됩니다 (묘한덕질/짤덕방은 신규 도입이므로 첫 주는 제외 없음).</li>
</ul>

<h3 style="margin-top:20px;margin-bottom:8px;font-size:15px">💡 참고 사항</h3>
<ul style="margin:4px 0 12px 20px">
  <li>영상 <b>제목은 유튜브 원본(한국어)</b> 을 사용합니다 (번역본 아님).</li>
  <li>리포트에 표시된 <b>공통 패턴·리프트·소재 인사이트는 상관관계 기반 관찰</b> 로, 정답이나 성과 예측값이 아닙니다. <b>기획 참고 및 A/B 테스트 신호</b> 로만 활용해주세요.</li>
</ul>

<p style="margin-top:20px;color:#71717a;font-size:12px">— 자동화 봇 · {date_slug} KST</p>
</body></html>"""


def run_weekly_bundle(date_slug, tmp_dir, dry_run=False):
    """매주 금 08:42 KST — 3 target × mode=weekly 통합 Bundle 메일.

    부분 실패 시 발송·sent 기록 모두 skip (raise). 3개 모두 성공한 경우에만 1통 발송.
    """
    results = []
    for slug, name in _WEEKLY_BUNDLE_TARGETS:
        _log(f"\n▶ Weekly Bundle · {name} ({slug}) 실행")
        r = _run_target_weekly(date_slug, tmp_dir, slug)
        results.append({"slug": slug, "name": name, "ok": r is not None, "data": r})

    ok_all = all(r["ok"] for r in results)
    if not ok_all:
        failed = [r["name"] for r in results if not r["ok"]]
        raise RuntimeError(
            f"Weekly Bundle 부분 실패 — 발송 skip. 실패 target: {failed}. "
            f"sent history 기록 없음. 다음 실행에서 재시도됩니다."
        )

    counts = [(r["name"], len(r["data"]["video_ids"])) for r in results]
    total = sum(n for _, n in counts)
    if total == 0:
        subject = f"[털어드림/묘한덕질/짤덕방 Weekly] {date_slug} · 신규 후보 없음"
    else:
        parts = " / ".join(f"{n}건" for _, n in counts)
        subject = f"[털어드림/묘한덕질/짤덕방 Weekly] {date_slug} · {parts}"
    subject = _test_prefix() + _reissue_prefix() + subject

    notice_block = _notice_html(os.environ.get("NOTICE", ""))
    body_html = _weekly_bundle_body_html(date_slug, counts, notice_html=notice_block)

    # preview 저장 (3개 HTML + body)
    body_preview_path = LOCAL_OUTPUT_DIR / f"preview_weekly_bundle_body_{date_slug}.html"
    body_preview_path.write_text(body_html, encoding="utf-8")
    _log(f"  💾 body preview: {body_preview_path.relative_to(ROOT)}")
    for r in results:
        p = LOCAL_OUTPUT_DIR / f"preview_weekly_{r['slug']}_{date_slug}.html"
        p.write_text(r["data"]["html"], encoding="utf-8")
        _log(f"  💾 preview [{r['slug']}]: {p.relative_to(ROOT)}")

    if dry_run:
        _log("  ⚠️ DRY_RUN — Gmail 발송 SKIP / benchmark sent history 기록 SKIP")
        _log(f"     상태: counts={counts}, subject={subject!r}")
        return

    # 3개 첨부 (첫 번째는 attachment_html, 나머지는 extra_attachments)
    first = results[0]
    extras = []
    for r in results[1:]:
        extras.append((f"{r['slug']}_weekly_{date_slug}.html", r["data"]["html"]))

    env = _env_snapshot()
    _log(f"\n📮 [weekly-bundle] 발송 → {env['RECIPIENT_EMAIL']} (첨부 {len(results)}개)")
    send_email(
        env,
        subject=subject,
        html_body=body_html,
        attachment_html=first["data"]["html"],
        attachment_filename=f"{first['slug']}_weekly_{date_slug}.html",
        extra_attachments=extras,
    )
    _log("  ✅ 발송 완료")

    # 발송 성공 후에만 각 target sent history 기록
    for r in results:
        _record_benchmark_sent("weekly", date_slug, r["data"]["video_ids"],
                               target_slug=r["slug"])


# ============================================================================
# mode: friday (standard) — 격주 금 08:47 KST, 털어드림 Standard 별도 메일
# ============================================================================
def run_friday(date_slug, tmp_dir, dry_run=False):
    """금요일 격주 standard 리포트: Benchmark standard 전용 (2026-08-24 대표님 요청으로 Weekly 제거).

    흐름: benchmark standard 실행 → HTML 그대로 발송 → 성공 시 sent history 기록.

    이전 (Weekly + Benchmark 통합) 구조:
      benchmark → weekly → shell 조립 → 발송
    이번 변경 (Benchmark 전용):
      benchmark → HTML 그대로 → 발송

    REISSUE / NOTICE / REPORT_DATE 옵션은 그대로 유지. weekly.py 자체는
    남겨두어 향후 재활성화 시 활용 가능.
    """
    profile = "standard"
    target_slug = "teoldeurim"
    bm_frag_path = tmp_dir / f"{date_slug}_bm_{profile}.html"
    if bm_frag_path.exists():
        bm_frag_path.unlink()  # stale 방지

    bm_rc = _run_child(
        SCRIPTS_DIR / "benchmark.py",
        extra_env={
            "TARGET": target_slug,
            "MODE": profile,
            "PROFILE": profile,   # backward compat
            "REPORT_FRAGMENT_PATH": str(bm_frag_path),
        },
        timeout=1800,
    )
    bm_ok = (bm_rc == 0) and bm_frag_path.exists()
    if not bm_ok:
        raise RuntimeError(
            f"Friday standard: benchmark 실패 (rc={bm_rc}, fragment={bm_frag_path.exists()}) "
            f"— 리포트 조립 불가"
        )

    bm_html = bm_frag_path.read_text(encoding="utf-8")
    # target=teoldeurim 은 기존 파일명 유지 (benchmark.py 규칙).
    bm_raw_path = ROOT / "benchmark" / f"{date_slug}_{profile}_filtered_raw.json"
    bm_video_ids = _load_benchmark_result_ids(bm_raw_path)
    bm_count = len(bm_video_ids)

    # subject: 정상 0건 처리. REISSUE=true 이면 맨 앞에 [재발송] 붙임.
    if bm_count == 0:
        subject = f"[털어드림 격주 후보] {date_slug} · 신규 후보 없음"
    else:
        subject = f"[털어드림 격주 후보] {date_slug} · Benchmark {bm_count}건"
    subject = _test_prefix() + _reissue_prefix() + subject

    notice_block = _notice_html(os.environ.get("NOTICE", ""))
    body_html = _friday_body_html(date_slug, bm_count, notice_html=notice_block)

    # preview 저장 (첨부용 HTML = benchmark 자체 HTML 그대로)
    preview_path = LOCAL_OUTPUT_DIR / f"preview_friday_{date_slug}.html"
    preview_path.write_text(bm_html, encoding="utf-8")
    body_preview_path = LOCAL_OUTPUT_DIR / f"preview_friday_body_{date_slug}.html"
    body_preview_path.write_text(body_html, encoding="utf-8")
    _log(f"  💾 preview 저장: {preview_path.relative_to(ROOT)}")
    _log(f"  💾 body preview: {body_preview_path.relative_to(ROOT)}")

    # DRY_RUN 분기 — 발송·sent history 기록 skip
    if dry_run:
        _log("  ⚠️ DRY_RUN — Gmail 발송 SKIP / benchmark sent history 기록 SKIP")
        _log(f"     상태: bm_count={bm_count}, subject={subject!r}")
        return

    env = _env_snapshot()
    _log(f"\n📮 [friday standard] 발송 → {env['RECIPIENT_EMAIL']}")
    send_email(
        env,
        subject=subject,
        html_body=body_html,
        attachment_html=bm_html,
        attachment_filename=f"teoldeurim_standard_{date_slug}.html",
    )
    _log("  ✅ 발송 완료")

    # 발송 성공 후 sent history 기록 — 신규 namespaced 위치에 저장
    _record_benchmark_sent(profile, date_slug, bm_video_ids,
                           target_slug=target_slug)


# ============================================================================
# main
# ============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", required=True,
        choices=["monday", "friday", "weekly-bundle"],
        help=("monday: legacy Recent 실행 (workflow 미사용, 수동 검증용). "
              "friday: 격주 금 · 털어드림 Standard 별도 메일. "
              "weekly-bundle: 매주 금 · 3-target Weekly 통합 메일."),
    )
    args = parser.parse_args()

    _load_dotenv_if_present()
    LOCAL_OUTPUT_DIR.mkdir(exist_ok=True)
    tmp_dir = LOCAL_OUTPUT_DIR / "_fragments"
    tmp_dir.mkdir(exist_ok=True)

    # DRY_RUN: send_report 자체 발송·sent history 기록만 skip. Apify/Claude 호출은 여전히 O.
    #  · weekly.py에는 DRY_RUN을 전파하지 않는다 (그렇게 하면 weekly history가 저장되지 않아
    #    dedup 근거가 훼손됨). weekly.py의 자체 발송은 SEND_EMAIL_DISABLED=1이 담당.
    #  · orchestrator는 preview HTML을 항상 local_output/preview_{mode}_{date}.html에 남긴다.
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    date_slug = _now_date_slug()
    _log("=" * 60)
    _log(f"털어드림 통합 리포트 오케스트레이터 · mode={args.mode}")
    _log(f"실행 시각 (KST): {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    _log(f"date_slug={date_slug}, tmp_dir={tmp_dir.relative_to(ROOT)}")
    if dry_run:
        _log("⚠️  DRY_RUN 모드 — Gmail 발송 X / benchmark sent history 기록 X "
             "(Apify·Claude 호출 O, weekly history는 정상 저장)")
    _log("=" * 60)

    try:
        if args.mode == "monday":
            run_monday(date_slug, tmp_dir, dry_run=dry_run)
        elif args.mode == "friday":
            run_friday(date_slug, tmp_dir, dry_run=dry_run)
        elif args.mode == "weekly-bundle":
            run_weekly_bundle(date_slug, tmp_dir, dry_run=dry_run)
        _log("\n✅ orchestrator 완료")
    except Exception as e:
        err = f"orchestrator 실행 실패:\n\n{e}\n\n{traceback.format_exc()}"
        _log(err)
        # DRY_RUN에서는 에러 메일도 보내지 않는다 (실제 발송 0회 유지)
        if not dry_run:
            try:
                env = _env_snapshot()
                send_error_email(env, err,
                                 subject=f"[털어드림 자동화] ❌ orchestrator 실패 ({args.mode})")
            except Exception as e2:
                _log(f"에러 메일도 실패: {e2}")
        sys.exit(1)


if __name__ == "__main__":
    main()
