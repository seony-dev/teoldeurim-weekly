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
# CSS scope parser — fragment 통합 시 selector 격리
# ----------------------------------------------------------------------------
# 목적: Weekly / Benchmark 각 fragment 의 CSS 를 통합 shell 에 그대로 넣으면
#       .hero / .hero h1 / body 등 공통 selector 가 서로 override 하여 렌더가 깨짐.
#       → 각 fragment CSS 를 자기 wrapper (.wk-root / .bm-root) scope 로 rewrite.
#
# 원칙:
#  - 원본 fragment (weekly.py / benchmark.py) 는 편집하지 않는다.
#    scope 는 오직 이 통합 shell 조립 단계에서만 적용된다.
#  - @keyframes / @font-face / @import 는 rewrite 대상이 아니다 (그대로 유지).
#  - @media / @supports 는 내부 nested rule 을 각각 scope.
#  - document-level selector 는 실제 declaration 을 유지한 채 wrapper 로 이동:
#      :root       → wrapper (CSS variable 정의)
#      body / html → wrapper (typography · color · background)
#      *           → "wrapper *" (자식 범위로 제한)
#      html.X ...  → "html.X wrapper ..." (html 조건 유지, wrapper 삽입)
# ============================================================================
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_RE_STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
_RE_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)


def _tokenize_rules(css):
    """CSS 텍스트를 top-level rule 로 분할. 각 rule 은 dict.

    반환 rule 은 아래 kind 중 하나:
      kind="rule"          : 일반 selector rule
      kind="at"            : @media / @supports / @keyframes / @font-face 등 block 형태
      kind="at-standalone" : @import 등 semicolon 종료 형태

    필드: prelude (selector 또는 at 조건), body (중괄호 내부 원문), at_name.
    """
    rules = []
    i, n = 0, len(css)
    while i < n:
        while i < n and css[i] in " \t\n\r":
            i += 1
        if i >= n:
            break
        if css[i:i+2] == "/*":
            end = css.find("*/", i + 2)
            i = (end + 2) if end != -1 else n
            continue
        start = i
        depth_paren = 0
        while i < n:
            c = css[i]
            if c == "(":
                depth_paren += 1
            elif c == ")":
                depth_paren -= 1
            elif c == "{" and depth_paren == 0:
                break
            elif c == ";" and depth_paren == 0 and css[start:i].lstrip().startswith("@"):
                rt = css[start:i + 1].strip()
                rules.append({
                    "kind": "at-standalone",
                    "at_name": rt.split(None, 1)[0][1:] if rt.startswith("@") else "",
                    "prelude": rt, "body": "", "raw": rt,
                })
                start = i + 1
                i += 1
                break
            i += 1
        else:
            break
        if i >= n or start == i:
            continue
        prelude = css[start:i].strip()
        i += 1  # {
        body_start, depth = i, 1
        while i < n and depth > 0:
            c = css[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = css[body_start:i]
        raw = css[start:i + 1 if i < n else n]
        i += 1  # }
        if prelude.startswith("@"):
            at_name = prelude[1:].split(None, 1)[0].split("(", 1)[0].split("{", 1)[0]
            rules.append({"kind": "at", "at_name": at_name,
                          "prelude": prelude, "body": body, "raw": raw})
        else:
            rules.append({"kind": "rule", "at_name": None,
                          "prelude": prelude, "body": body, "raw": raw})
    return rules


def _split_selectors(prelude):
    """`a, b(x, y), c` → ['a', 'b(x, y)', 'c']. 괄호 안 콤마는 그대로 유지."""
    parts, cur, depth = [], "", 0
    for ch in prelude:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _scope_single_selector(sel, prefix):
    """selector 하나를 wrapper prefix 로 scope. list of scoped selectors 반환.

    root-self 매치 케이스:
      원본 fragment 의 wrapper 는 `<div class="page wk-root">` 형태로 여러 class 를
      동시 보유 (`.page` + `.wk-root`). CSS 원본이 `.page { padding: … }` 인데
      단순히 `.wk-root .page` 로만 rewrite 하면 descendant 만 매치되어 wrapper
      자신에게는 스타일이 적용 안 됨.
      → class-based selector 는 root-self compound(`prefix.first_class`) 도 함께 반환.
      → wrapper 가 `.wk-root.page` 로도 매치되어 원본 layout 이 보존됨.
    """
    s = sel.strip()
    if not s:
        return []
    # :root → wrapper (CSS variable 정의는 wrapper 안에서만 상속)
    if s == ":root":
        return [prefix]
    # * → wrapper 자신 + 자손 (universal reset 이 wrapper 에도 적용되어야 함)
    if s == "*":
        return [prefix, f"{prefix} *"]
    head = s.split(None, 1)[0]
    # body / html → wrapper
    if s == "body" or s == "html":
        return [prefix]
    # html.X descendant (예: "html.js .js-only" → "html.js .wk-root .js-only")
    if head.startswith("html") and " " in s:
        cond, _, rest = s.partition(" ")
        return [f"{cond} {prefix} {rest.strip()}"]
    if head == "body" and " " in s:
        _, _, rest = s.partition(" ")
        return [f"{prefix} {rest.strip()}"]
    # 일반 selector — descendant 형태 (`.wk-root .foo`)
    result = [f"{prefix} {s}"]
    # class-based selector 는 root-self compound (`.wk-root.foo`) 도 추가.
    # wrapper element 가 여러 class 를 동시에 가질 때 이 형태가 매치됨.
    if s.startswith("."):
        result.append(f"{prefix}{s}")
    return result


def _scope_rule(rule, prefix):
    """단일 rule 을 scope 된 CSS 텍스트로 재조립."""
    selectors = _split_selectors(rule["prelude"])
    scoped = []
    for s in selectors:
        scoped.extend(_scope_single_selector(s, prefix))
    # dedup (`body, html` 같이 둘 다 wrapper 로 축약된 경우)
    seen, uniq = set(), []
    for s in scoped:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return f"{', '.join(uniq)} {{{rule['body']}}}"


def _scope_css(css, prefix):
    """CSS 전체를 prefix 로 scope 해 반환.

    - @keyframes / @font-face / @import 는 그대로 유지.
    - @media / @supports 는 wrapper `@media (...) { ... }` 는 유지하고 내부 nested rule 만 scope.
    - 나머지 top-level rule 은 _scope_rule() 로 rewrite.
    """
    rules = _tokenize_rules(css)
    out = []
    for r in rules:
        if r["kind"] == "at-standalone":
            # @import 등 — 그대로
            out.append(r["raw"])
        elif r["kind"] == "at":
            at = r["at_name"]
            if at in ("keyframes", "font-face"):
                # 애니메이션 이름·폰트 정의 — 그대로 (실측 0개이지만 안전용)
                out.append(r["raw"])
            elif at in ("media", "supports"):
                # 내부 nested rule 만 scope
                nested = _tokenize_rules(r["body"])
                inner = []
                for nr in nested:
                    if nr["kind"] == "at" and nr["at_name"] in ("keyframes", "font-face"):
                        inner.append(nr["raw"])
                    elif nr["kind"] == "rule":
                        inner.append(_scope_rule(nr, prefix))
                    else:
                        inner.append(nr["raw"])
                out.append(f"{r['prelude']} {{\n{chr(10).join(inner)}\n}}")
            else:
                # 알려지지 않은 at-rule (예: @layer) — 그대로 (안전 fallback)
                out.append(r["raw"])
        else:
            out.append(_scope_rule(r, prefix))
    return "\n".join(out)


def _extract_fragment(html, root_class):
    """완전 HTML 에서 body 내용을 root_class(.wk-root/.bm-root) 단위로 추출.

    스타일은 root_class prefix 로 자동 scope 된 형태로 반환.
    - `.wk-root` 라면 실제 prefix 는 ".wk-root"
    - `.bm-root` 라면 실제 prefix 는 ".bm-root"

    반환: {"styles": [scoped css text list], "body": body html string, "title": title text}
    """
    raw_styles = _RE_STYLE.findall(html)
    body_m = _RE_BODY.search(html)
    body_html = body_m.group(1).strip() if body_m else html
    title_m = _RE_TITLE.search(html)
    title = title_m.group(1).strip() if title_m else ""
    prefix = root_class if root_class.startswith(".") else f".{root_class}"
    scoped_styles = [_scope_css(s, prefix) for s in raw_styles]
    if root_class not in body_html:
        _log(f"  ⚠️ fragment 에 {root_class} wrapper 없음 — 격리 위험. shell 조립은 계속.")
    return {"styles": scoped_styles, "body": body_html, "title": title}


SHELL_CSS = """
:root { --shell-bg:#f4f5f7; --shell-ink:#1c1e21; --shell-line:#d6d9de; --shell-active:#111; }
body { margin:0; background:var(--shell-bg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; color:var(--shell-ink); }
.shell-wrap { max-width:1160px; margin:0 auto; padding:24px 16px 48px; }
.shell-header { padding:20px 4px 12px; }
.shell-header h1 { margin:0 0 4px; font-size:18px; letter-spacing:-.01em; }
.shell-header .sub { color:#5d6472; font-size:13px; }
.shell-tabs { display:flex; gap:0; border-bottom:2px solid var(--shell-line); margin:16px 0 0; position:sticky; top:0; background:var(--shell-bg); z-index:50; }
.shell-tab { appearance:none; background:transparent; border:0; border-bottom:3px solid transparent; margin-bottom:-2px; padding:12px 18px; font-size:14px; font-weight:600; color:#5d6472; cursor:pointer; }
.shell-tab.active { color:var(--shell-active); border-bottom-color:var(--shell-active); }
.shell-tab:hover { color:var(--shell-active); }
.shell-panel { padding-top:16px; }
.shell-panel.is-hidden { display:none !important; }
.shell-footer { color:#8b93a1; font-size:12px; padding:24px 4px 0; }
"""


def _build_shell_html(today_label, wk_frag, bm_frag, wk_summary, bm_summary,
                     wk_label="Weekly 후보", bm_label="타 채널 Benchmark",
                     header_sub=None):
    """상위 2탭 shell HTML 조립.

    Monday에서는 wk_label="Weekly 최근 후보", bm_label="타 채널 Benchmark 최근 후보"로 호출.
    """
    all_styles = []
    for f in (wk_frag, bm_frag):
        if f:
            all_styles.extend(f["styles"])
    styles_html = "\n".join(f"<style>{s}</style>" for s in all_styles)

    wk_body = wk_frag["body"] if wk_frag else '<div class="empty">Weekly 결과 없음</div>'
    bm_body = bm_frag["body"] if bm_frag else '<div class="empty">Benchmark 결과 없음 (수집 실패 또는 미실행)</div>'

    only_bm = (bm_frag is not None) and (wk_frag is None)
    # 기본은 wk 탭이 활성 (both/only_wk 케이스). only_bm 이면 bm 탭 활성.
    if not only_bm:
        wk_active, wk_hidden = "active", ""
        bm_active, bm_hidden = "", "is-hidden"
    else:
        wk_active, wk_hidden = "", "is-hidden"
        bm_active, bm_hidden = "active", ""

    sub_line = header_sub or f"{today_label} · {wk_label} + {bm_label}"

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<base target="_blank">
<title>털어드림 통합 리포트 · {today_label}</title>
<style>{SHELL_CSS}</style>
{styles_html}
</head><body>
<div class="shell-wrap">
  <header class="shell-header">
    <h1>털어드림 통합 리포트</h1>
    <div class="sub">{sub_line}</div>
  </header>
  <nav class="shell-tabs" id="shellTabs" role="tablist">
    <button class="shell-tab {wk_active}" data-tab="wk" type="button" role="tab">{wk_label} <small>{wk_summary}</small></button>
    <button class="shell-tab {bm_active}" data-tab="bm" type="button" role="tab">{bm_label} <small>{bm_summary}</small></button>
  </nav>
  <section class="shell-panel {wk_hidden}" data-tab="wk">
    {wk_body}
  </section>
  <section class="shell-panel {bm_hidden}" data-tab="bm">
    {bm_body}
  </section>
  <footer class="shell-footer">털어드림 자동화 · 통합 리포트 shell</footer>
</div>
<script>
(function() {{
  var tabs = document.querySelectorAll('#shellTabs .shell-tab');
  var panels = document.querySelectorAll('.shell-panel');
  function activate(name) {{
    for (var i = 0; i < tabs.length; i++) {{
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === name);
    }}
    for (var j = 0; j < panels.length; j++) {{
      var match = panels[j].getAttribute('data-tab') === name;
      panels[j].classList.toggle('is-hidden', !match);
    }}
  }}
  for (var k = 0; k < tabs.length; k++) {{
    (function(t) {{
      t.addEventListener('click', function() {{
        activate(t.getAttribute('data-tab'));
      }});
    }})(tabs[k]);
  }}
}})();
</script>
</body></html>
"""


# ============================================================================
# benchmark sent history 저장 (발송 성공 후에만)
# ============================================================================
def _record_benchmark_sent(profile, date_slug, video_ids):
    """benchmark/history/sent/{date}_{profile}.json 생성.
    video_ids: 이 발송에 포함된 후보들의 video_id 리스트.
    """
    if not video_ids:
        _log("  ⚠️ 기록할 video_id 없음 (발송 후보 0개) — sent history 저장 skip")
        return None
    sent_dir = ROOT / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
    sent_dir.mkdir(parents=True, exist_ok=True)
    p = sent_dir / f"{date_slug}_{profile}.json"
    payload = {
        "date_kst": date_slug,
        "profile": profile,
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
def _monday_body_html(date_slug, wk_count, bm_count, wk_ok, bm_ok):
    """월요일 recent 통합 본문. Weekly recent + Benchmark recent 두 축.

    상태:
      wk_ok=True, bm_ok=True  → 정상 통합 (양쪽 축 개수 표시)
      wk_ok=True, bm_ok=False → Weekly-only fallback + benchmark 실패 표시
      wk_ok=False, bm_ok=True → Benchmark-only fallback + weekly 실패 표시
      wk_ok=False, bm_ok=False → orchestrator raise (여기까지 오지 않음)
    """
    # 실패 fallback 표시
    fail_note = ""
    if not wk_ok and bm_ok:
        fail_note = "<p><b>⚠️ 이번 실행에서 Weekly recent 파이프라인이 실패했습니다.</b> " \
                    "Benchmark 최근 후보만 확인 가능합니다.</p>"
    elif wk_ok and not bm_ok:
        fail_note = "<p><b>⚠️ 이번 실행에서 Benchmark recent 파이프라인이 실패했습니다.</b> " \
                    "Weekly 최근 후보만 확인 가능합니다.</p>"

    # 정상 0건 표시 (실패와 구분)
    zero_notes = []
    if wk_ok and wk_count == 0:
        zero_notes.append("Weekly 최근 후보 <b>0건</b> — 최근 7일 이내 조건(조회수 5만~300만)에 맞는 새 영상이 없습니다.")
    if bm_ok and bm_count == 0:
        zero_notes.append("타 채널 Benchmark 최근 후보 <b>0건</b> — 최근 7일 이내 조건에 맞는 새 영상이 없습니다.")
    zero_block = ""
    if zero_notes:
        items = "".join(f"<li>{n}</li>" for n in zero_notes)
        zero_block = (f"<p><b>ℹ️ 정상 실행 결과 중 아래 축은 0건입니다.</b> "
                      f"수집·필터링·분석은 정상 완료되었습니다.</p><ul>{items}</ul>")

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
<p>안녕하세요, 박서은입니다.</p>
<p><b>최근 콘텐츠 통합 리포트</b>를 첨부드립니다. (0일 ~ 7일 이내 신규 콘텐츠 · 월·수·금 3회 발송)</p>
{fail_note}
<ul>
  <li>Weekly 최근 후보: {wk_count if wk_ok else "실행 실패"}건 (YouTube 검색어 기반, 최신 업로드 순)</li>
  <li>타 채널 Benchmark 최근 후보: {bm_count if bm_ok else "실행 실패"}건 (4채널 · 최신 업로드 순)</li>
  <li>조건: 조회수 5만~300만, 업로드 0~7일</li>
  <li>중복 제외: 과거 Weekly 발송 + 이전 Benchmark 리포트 이력 (cross-profile dedup)</li>
</ul>
{zero_block}
<p>첨부 HTML 상단의 두 탭 [Weekly 최근 후보 / 타 채널 Benchmark 최근 후보]으로 각각 확인 가능합니다.</p>
</body></html>"""


def _friday_body_html(date_slug, wk_count, bm_count, bm_ok, notice_html=""):
    """금요일 통합 본문. bm_ok=False 는 benchmark 실행 자체 실패 (weekly-only fallback).

    notice_html 은 최상단에 삽입될 안내 박스 (비어 있으면 미노출).
    """
    if not bm_ok:
        # weekly-only fallback
        return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
{notice_html}<p>안녕하세요, 박서은입니다.</p>
<p><b>⚠️ 이번 실행에서 benchmark 파이프라인이 실패했습니다.</b> Weekly 후보만 확인 가능합니다.</p>
<ul>
  <li>Weekly 후보: {wk_count}건 (YouTube 검색어 기반)</li>
  <li>타 채널 Benchmark: 실행 실패 (재실행 시 후보 중복 제외에 영향 없음)</li>
</ul>
<p>첨부 HTML에는 Weekly 탭만 실질 데이터가 들어 있습니다.</p>
</body></html>"""
    # 정상 실행. 각 축의 0건 상태를 문구로 명시.
    zero_notes = []
    if wk_count == 0:
        zero_notes.append("Weekly 신규 후보 <b>0건</b> — 이번 격주 조건에 맞는 새 영상이 없습니다.")
    if bm_count == 0:
        zero_notes.append("타 채널 Benchmark 신규 후보 <b>0건</b> — 조건에 맞는 새 영상이 없습니다.")
    zero_block = ""
    if zero_notes:
        items = "".join(f"<li>{n}</li>" for n in zero_notes)
        zero_block = (
            f"<p><b>ℹ️ 정상 실행 결과 중 아래 축은 0건입니다.</b> "
            f"수집·필터링·분석은 정상 완료되었습니다.</p><ul>{items}</ul>"
        )
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
{notice_html}<p>안녕하세요, 박서은입니다.</p>
<p>이번 격주 <b>통합 리포트</b>를 첨부드립니다.</p>
<ul>
  <li>Weekly 후보: {wk_count}건 (YouTube 검색어 기반)</li>
  <li>타 채널 Benchmark: {bm_count}건 (4채널 인기 Shorts + 두 축 점수)</li>
  <li>첨부 HTML 상단의 두 탭 [Weekly 후보 / 타 채널 Benchmark]으로 각각 확인 가능</li>
</ul>
{zero_block}
</body></html>"""


# ============================================================================
# mode: monday (recent)
# ============================================================================
def run_monday(date_slug, tmp_dir, dry_run=False):
    """월요일 통합 리포트: Weekly recent + Benchmark recent.
    흐름: benchmark → weekly → 상위 2탭 shell 조립 → preview → 발송 → sent 기록
    """
    profile = "recent"
    bm_frag_path = tmp_dir / f"{date_slug}_bm_{profile}.html"
    wk_frag_path = tmp_dir / f"{date_slug}_wk_{profile}.html"
    for p in (bm_frag_path, wk_frag_path):
        if p.exists():
            p.unlink()  # stale 방지

    # 1) benchmark recent 실행
    bm_rc = _run_child(
        SCRIPTS_DIR / "benchmark.py",
        extra_env={
            "PROFILE": profile,
            "REPORT_FRAGMENT_PATH": str(bm_frag_path),
        },
        timeout=900,
    )
    bm_ok = (bm_rc == 0) and bm_frag_path.exists()
    if not bm_ok:
        _log(f"  ⚠️ benchmark({profile}) 실패 (rc={bm_rc}, fragment={bm_frag_path.exists()}) "
             f"— 이어서 weekly recent 시도")

    # 2) weekly recent 실행 (SEND_EMAIL_DISABLED=1 로 자체 발송 스킵)
    #    · DRY_RUN 상속 → weekly.py 자체 로직으로 history 저장도 skip
    #    · production → history/YYYY-MM-DD_recent.json 저장 (cross-profile dedup 근거)
    wk_env = {
        "WEEKLY_PROFILE": profile,
        "REPORT_FRAGMENT_PATH": str(wk_frag_path),
        "SEND_EMAIL_DISABLED": "1",
    }
    wk_rc = _run_child(SCRIPTS_DIR / "weekly.py", extra_env=wk_env, timeout=900)
    wk_ok = (wk_rc == 0) and wk_frag_path.exists()
    if not wk_ok:
        _log(f"  ⚠️ weekly({profile}) 실패 (rc={wk_rc}, fragment={wk_frag_path.exists()})")

    if not wk_ok and not bm_ok:
        raise RuntimeError(f"Monday recent: benchmark·weekly 모두 실패 → orchestrator 종료")

    # 3) fragment 추출 → 상위 2탭 shell 조립
    wk_frag = None
    if wk_ok:
        wk_html = wk_frag_path.read_text(encoding="utf-8")
        wk_frag = _extract_fragment(wk_html, ".wk-root")
    bm_frag = None
    bm_video_ids = []
    if bm_ok:
        bm_html = bm_frag_path.read_text(encoding="utf-8")
        bm_frag = _extract_fragment(bm_html, ".bm-root")
        bm_raw_path = ROOT / "benchmark" / f"{date_slug}_{profile}_filtered_raw.json"
        bm_video_ids = _load_benchmark_result_ids(bm_raw_path)

    wk_count = _count_weekly_candidates(wk_frag_path.read_text(encoding="utf-8")) if wk_ok else 0
    bm_count = len(bm_video_ids)

    wk_summary = "· 실행 실패" if not wk_ok else f"· {wk_count}건"
    bm_summary = "· 실행 실패" if not bm_ok else f"· {bm_count}건"

    shell_html = _build_shell_html(
        date_slug, wk_frag, bm_frag,
        wk_summary=wk_summary, bm_summary=bm_summary,
        wk_label="Weekly 최근 후보",
        bm_label="타 채널 Benchmark 최근 후보",
        header_sub=f"{date_slug} · 최근 7일 이내 콘텐츠 (Weekly recent + Benchmark recent)",
    )

    # subject
    if not wk_ok:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · Benchmark {bm_count}건 (⚠️ Weekly recent 실패)"
    elif not bm_ok:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · Weekly {wk_count}건 (⚠️ Benchmark recent 실패)"
    elif wk_count == 0 and bm_count == 0:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · 신규 후보 없음 (Weekly 0 / Benchmark 0)"
    else:
        subject = f"[털어드림 최근 콘텐츠] {date_slug} · Weekly {wk_count} + Benchmark {bm_count}"

    body_html = _monday_body_html(date_slug, wk_count, bm_count, wk_ok, bm_ok)

    # preview 저장 (항상)
    preview_path = LOCAL_OUTPUT_DIR / f"preview_monday_{date_slug}.html"
    preview_path.write_text(shell_html, encoding="utf-8")
    body_preview_path = LOCAL_OUTPUT_DIR / f"preview_monday_body_{date_slug}.html"
    body_preview_path.write_text(body_html, encoding="utf-8")
    _log(f"  💾 preview 저장: {preview_path.relative_to(ROOT)}")
    _log(f"  💾 body preview: {body_preview_path.relative_to(ROOT)}")

    # DRY_RUN 분기 — 발송·sent history 기록 skip
    if dry_run:
        _log("  ⚠️ DRY_RUN — Gmail 발송 SKIP / benchmark sent history 기록 SKIP")
        _log(f"     상태: wk_ok={wk_ok}({wk_count}), bm_ok={bm_ok}({bm_count}), subject={subject!r}")
        return

    env = _env_snapshot()
    _log(f"\n📮 [monday] 발송 → {env['RECIPIENT_EMAIL']}")
    send_email(
        env,
        subject=subject,
        html_body=body_html,
        attachment_html=shell_html,
        attachment_filename=f"teoldeurim_recent_{date_slug}.html",
    )
    _log("  ✅ 발송 완료")

    # 발송 성공 후에만 benchmark sent history 기록 (benchmark 성공한 경우만)
    if bm_ok:
        _record_benchmark_sent(profile, date_slug, bm_video_ids)
    else:
        _log("  ⚠️ benchmark recent 실패로 sent history 미기록")


# ============================================================================
# mode: friday (standard 통합)
# ============================================================================
def _count_weekly_candidates(wk_html):
    """weekly fragment HTML에서 candidate 카드 개수 대략 추정 (표시용 카운트)."""
    # weekly의 최종 카드는 render_candidate_card가 만드는 .candidate 요소.
    return wk_html.count('class="candidate"')


def run_friday(date_slug, tmp_dir, dry_run=False):
    """금요일 통합 리포트: benchmark(standard) → weekly → 통합 shell → 발송.
    흐름: benchmark → weekly → shell 조립 → preview 저장 → (DRY_RUN skip) → 발송 → 성공 시 sent 기록
    """
    profile = "standard"
    bm_frag_path = tmp_dir / f"{date_slug}_bm_{profile}.html"
    wk_frag_path = tmp_dir / f"{date_slug}_wk.html"
    for p in (bm_frag_path, wk_frag_path):
        if p.exists():
            p.unlink()  # stale 방지

    # 1) benchmark 먼저 (weekly history 오염 방지)
    bm_rc = _run_child(
        SCRIPTS_DIR / "benchmark.py",
        extra_env={
            "PROFILE": profile,
            "REPORT_FRAGMENT_PATH": str(bm_frag_path),
        },
        timeout=900,
    )
    bm_ok = (bm_rc == 0) and bm_frag_path.exists()
    if not bm_ok:
        _log(f"  ⚠️ benchmark 실패 (rc={bm_rc}, fragment={bm_frag_path.exists()}) "
             f"— weekly-only fallback 진행")

    # 2) weekly (자체 SMTP 발송은 SEND_EMAIL_DISABLED=1 로 항상 스킵)
    #    · DRY_RUN 은 extra_env로 명시하지 않음. _run_child가 os.environ.copy()로
    #      부모 shell 의 env 를 전체 상속하므로 DRY_RUN=1 도 자연스레 자식에게 전달됨.
    #    · PROD 실행 (DRY_RUN 미설정):
    #        weekly.py는 history/YYYY-MM-DD.json 정상 저장 + 자체 발송만 SEND_EMAIL_DISABLED로 skip
    #        → orchestrator 가 통합 shell 을 조립해 대신 발송
    #    · DRY_RUN=1 실행:
    #        weekly.py 도 자체 DRY_RUN 로직 발동 → history 저장 skip + 자체 발송 skip
    #        → orchestrator 정의 (DRY_RUN=1 은 mode 무관하게 발송·history 저장 X) 와 일치
    wk_env = {
        "REPORT_FRAGMENT_PATH": str(wk_frag_path),
        "SEND_EMAIL_DISABLED": "1",
    }
    wk_rc = _run_child(SCRIPTS_DIR / "weekly.py", extra_env=wk_env, timeout=900)
    if wk_rc != 0 or not wk_frag_path.exists():
        raise RuntimeError(
            f"weekly 실패 (rc={wk_rc}, fragment={wk_frag_path.exists()}) — "
            f"통합 리포트 조립 불가"
        )

    wk_html = wk_frag_path.read_text(encoding="utf-8")
    bm_html = bm_frag_path.read_text(encoding="utf-8") if bm_ok else None

    # 3) fragment 추출 → shell 조립
    wk_frag = _extract_fragment(wk_html, ".wk-root")
    bm_frag = _extract_fragment(bm_html, ".bm-root") if bm_html else None

    # 후보 id 확보 (bm) + 카운트
    bm_video_ids = []
    if bm_ok:
        bm_raw_path = ROOT / "benchmark" / f"{date_slug}_{profile}_filtered_raw.json"
        bm_video_ids = _load_benchmark_result_ids(bm_raw_path)
    wk_count = _count_weekly_candidates(wk_html)
    bm_count = len(bm_video_ids)

    wk_summary = f"· {wk_count}건"
    bm_summary = "· 실행 실패" if not bm_ok else f"· {bm_count}건"

    shell_html = _build_shell_html(date_slug, wk_frag, bm_frag,
                                   wk_summary=wk_summary,
                                   bm_summary=bm_summary)

    # subject: 정상 0건 vs benchmark 실패 구분. REISSUE=true 이면 맨 앞에 [재발송] 붙임.
    if not bm_ok:
        subject = f"[털어드림 격주 후보] {date_slug} · Weekly {wk_count}건 (⚠️ benchmark 실패)"
    elif wk_count == 0 and bm_count == 0:
        subject = f"[털어드림 격주 후보] {date_slug} · 신규 후보 없음 (Weekly 0 / Benchmark 0)"
    else:
        subject = f"[털어드림 격주 후보] {date_slug} · Weekly {wk_count} + Benchmark {bm_count}"
    subject = _reissue_prefix() + subject

    notice_block = _notice_html(os.environ.get("NOTICE", ""))
    body_html = _friday_body_html(date_slug, wk_count, bm_count, bm_ok,
                                   notice_html=notice_block)

    # preview 저장 (항상)
    preview_path = LOCAL_OUTPUT_DIR / f"preview_friday_{date_slug}.html"
    preview_path.write_text(shell_html, encoding="utf-8")
    body_preview_path = LOCAL_OUTPUT_DIR / f"preview_friday_body_{date_slug}.html"
    body_preview_path.write_text(body_html, encoding="utf-8")
    _log(f"  💾 preview 저장: {preview_path.relative_to(ROOT)}")
    _log(f"  💾 body preview: {body_preview_path.relative_to(ROOT)}")

    # DRY_RUN 분기 — 발송·sent history 기록 skip
    if dry_run:
        _log("  ⚠️ DRY_RUN — Gmail 발송 SKIP / benchmark sent history 기록 SKIP")
        _log(f"     상태: bm_ok={bm_ok}, wk_count={wk_count}, bm_count={bm_count}, subject={subject!r}")
        return

    env = _env_snapshot()
    _log(f"\n📮 [friday] 발송 → {env['RECIPIENT_EMAIL']}")
    send_email(
        env,
        subject=subject,
        html_body=body_html,
        attachment_html=shell_html,
        attachment_filename=f"teoldeurim_integrated_{date_slug}.html",
    )
    _log("  ✅ 발송 완료")

    # 발송 성공 후 sent history 기록 (benchmark 성공한 경우만)
    if bm_ok:
        _record_benchmark_sent(profile, date_slug, bm_video_ids)
    else:
        _log("  ⚠️ benchmark 실패로 sent history 미기록 (다음 실행에서 후보 재검토됨)")


# ============================================================================
# main
# ============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["monday", "friday"])
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
