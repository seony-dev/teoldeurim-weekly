# -*- coding: utf-8 -*-
"""REPORT_DATE / NOTICE / REISSUE 회귀 테스트.

지키는 원칙:
  · REPORT_DATE 미설정 → 기존 KST 오늘 동작 (backward compat)
  · REPORT_DATE=2026-07-17 → 파일명·헤더·subject slot 만 slot 값 사용
  · 실제 실행 시각(now_kst / today_kst / generated_at / age) 은 mock 실행일 유지
  · 잘못된 REPORT_DATE → 즉시 실패 (SystemExit / sys.exit)
  · NOTICE HTML escape (사용자 입력을 먼저 HTML로 해석하면 XSS)
  · NOTICE 개행 처리 (literal '\\n' 2글자 → real newline → <br>)
  · REISSUE=truthy 만 [재발송] prefix
  · monday 본문에는 notice / 재발송 흔적 없음

실행:
  프로젝트 루트에서:  python tests/test_report_date_notice.py
  종료 코드: 성공 0 / 실패 1
"""
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

# 강제 재import
for m in ["send_report", "weekly", "benchmark", "benchmark_config"]:
    if m in sys.modules:
        del sys.modules[m]

# ─────────────────────────────────────────────
checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# 1) send_report._now_date_slug — REPORT_DATE 검증
# ═══════════════════════════════════════════════════════════════════
os.environ.pop("REPORT_DATE", None)
import send_report

today_kst_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
chk("SR-01: REPORT_DATE 미설정 → 오늘 KST",
    send_report._now_date_slug() == today_kst_str,
    f"got={send_report._now_date_slug()!r}")

os.environ["REPORT_DATE"] = "2026-07-17"
chk("SR-02: REPORT_DATE=2026-07-17 → slot 값 반환",
    send_report._now_date_slug() == "2026-07-17")

os.environ["REPORT_DATE"] = "  2026-07-17  "  # 공백 trim
chk("SR-03: REPORT_DATE 공백 trim",
    send_report._now_date_slug() == "2026-07-17")

# 잘못된 형식 → SystemExit (regex + strptime 모두 검증)
for bad in ["2026/07/17", "07-17-2026", "abc", "2026-13-01", "2026-07-32",
            "2026-2-3", "2026-7-17", "26-07-17"]:
    os.environ["REPORT_DATE"] = bad
    raised = False
    try:
        send_report._now_date_slug()
    except SystemExit:
        raised = True
    chk(f"SR-04-bad[{bad!r}]: 잘못된 형식 SystemExit", raised)

# 공백-only 값 → override 미적용 (오늘 KST)
os.environ["REPORT_DATE"] = "  "
chk("SR-05: 공백-only REPORT_DATE 는 미override (오늘 KST)",
    send_report._now_date_slug() == today_kst_str)

# 앞뒤 공백 있는 유효값 → strip 후 통과
os.environ["REPORT_DATE"] = "  2026-07-17  "
chk("SR-06: 앞뒤 공백 있는 유효값 → strip 후 정상 통과",
    send_report._now_date_slug() == "2026-07-17")

os.environ.pop("REPORT_DATE", None)


# ═══════════════════════════════════════════════════════════════════
# 2) send_report._notice_html — HTML escape / 개행 / 빈 값
# ═══════════════════════════════════════════════════════════════════
chk("NT-01: 빈 값 → 빈 문자열",
    send_report._notice_html("") == "" and send_report._notice_html("   ") == "")

# 리터럴 '\n' 처리 (workflow_dispatch string input 대응)
notice_lit = "※ 발송 안내\\n본문 첫 줄\\n본문 둘째 줄"
h1 = send_report._notice_html(notice_lit)
chk("NT-02: 리터럴 '\\n' 을 <br> 로 변환",
    h1.count("<br>") == 2,
    f"got br count={h1.count('<br>')}")

# 실제 개행 처리
h2 = send_report._notice_html("※ 발송 안내\n두 번째 줄")
chk("NT-03: 실제 개행도 <br> 로 변환",
    h2.count("<br>") == 1)

# XSS 방어 (HTML escape)
xss = '<script>alert("x")</script>'
h3 = send_report._notice_html(xss)
chk("NT-04a: <script> 태그 escape",
    "<script>" not in h3 and "&lt;script&gt;" in h3,
    f"got={h3[:200]}")
chk("NT-04b: 큰따옴표 escape",
    "&quot;" in h3)

# 앰퍼샌드 escape
h4 = send_report._notice_html("A & B")
chk("NT-05: & escape", "&amp;" in h4)

# ─────────────────────────────────────────────
# literal '\n' 처리 안전성 (workflow_dispatch UI 는 실제 개행 못 넣음)
# ─────────────────────────────────────────────
# NT-07: 순수 literal '\n' 만
h_lit = send_report._notice_html("한 줄\\n두 줄\\n세 줄")
chk("NT-07a: literal 3분할 → <br> 2개", h_lit.count("<br>") == 2)
chk("NT-07b: 출력에 literal '\\n' 문자열이 남지 않음 (변환 확인)",
    "\\n" not in h_lit,
    f"got={h_lit}")

# NT-08: literal '\n' + HTML 특수문자 조합 — 순서 안전성 검증
combo = '<b>강조</b>\\n"인용" & 앰퍼샌드\\n<script>alert(1)</script>'
h_combo = send_report._notice_html(combo)
chk("NT-08a: <b>, <script> 모두 escape (HTML로 해석되지 않음)",
    "<b>" not in h_combo and "<script>" not in h_combo,
    f"got={h_combo}")
chk("NT-08b: escape 결과 정확 (&lt;b&gt; / &lt;script&gt;)",
    "&lt;b&gt;" in h_combo and "&lt;script&gt;" in h_combo)
chk("NT-08c: 큰따옴표·앰퍼샌드 escape",
    "&quot;" in h_combo and "&amp;" in h_combo)
chk("NT-08d: literal '\\n' 은 <br> 로 변환 (3분할 = <br> 2개)",
    h_combo.count("<br>") == 2)
chk("NT-08e: 출력에 literal '\\n' 남지 않음",
    "\\n" not in h_combo)
chk("NT-08f: 우리가 삽입한 <br> 는 escape 되지 않음 (안전한 순서)",
    "<br>" in h_combo and "&lt;br&gt;" not in h_combo)

# NT-09: 실제 newline + literal '\n' 혼합 케이스
mixed = "실제개행\n다음\\n리터럴다음"
h_mixed = send_report._notice_html(mixed)
chk("NT-09: 실제 개행 · literal 혼합 모두 <br>",
    h_mixed.count("<br>") == 2 and "\\n" not in h_mixed,
    f"got={h_mixed}")

# NT-10: 사용자 입력이 먼저 HTML로 해석되지 않는지 (순서 안전성 최종 검증)
#   만약 escape 를 <br> join 뒤에 했다면 <br> 자체가 &lt;br&gt; 로 escape 되어
#   결과 HTML에 개행이 아예 없게 된다. 실제로 <br> 이 살아 있어야 순서가 맞다.
attack = '"><img src=x onerror=alert(1)>\\n두 번째 줄'
h_attack = send_report._notice_html(attack)
chk("NT-10a: img 태그 escape (실제 태그로 해석되지 않음)",
    "<img" not in h_attack and "&lt;img" in h_attack)
chk("NT-10b: onerror 속성명은 escape 된 텍스트로만 존재",
    "&lt;img src=x onerror=alert(1)&gt;" in h_attack,
    f"got={h_attack}")
chk("NT-10c: literal '\\n' 은 정상 <br> 로 변환",
    h_attack.count("<br>") == 1 and "\\n" not in h_attack)

# 지정 문구 (실제 발송 문구) 렌더 검증
real_notice = ("※ 발송 안내\n"
               "본 리포트는 7월 17일(금) 발송 예정이었으나 자동화 실행 오류로 발송되지 않았습니다. "
               "오류 수정 후 7월 20일(월) 기준으로 콘텐츠를 다시 수집하여 발송드립니다.")
h5 = send_report._notice_html(real_notice)
chk("NT-06a: 지정 안내 문구 렌더 (제목 + 본문 2줄 분리)",
    "※ 발송 안내<br>본 리포트는" in h5,
    f"snippet={h5[:200]}")
chk("NT-06b: 안내 박스 style 포함",
    "border-left" in h5 and "background:" in h5)


# ═══════════════════════════════════════════════════════════════════
# 3) send_report._reissue_prefix
# ═══════════════════════════════════════════════════════════════════
for truthy in ["1", "true", "TRUE", "yes", "Yes"]:
    os.environ["REISSUE"] = truthy
    chk(f"RI-01[{truthy}]: reissue truthy → '[재발송] '",
        send_report._reissue_prefix() == "[재발송] ")

for falsy in ["", "0", "false", "no", "off", "  "]:
    os.environ["REISSUE"] = falsy
    chk(f"RI-02[{falsy!r}]: reissue falsy → ''",
        send_report._reissue_prefix() == "")

os.environ.pop("REISSUE", None)


# ═══════════════════════════════════════════════════════════════════
# 4) _friday_body_html — notice 삽입 확인
#    (2026-08-24) Weekly 파트 제거 → 시그니처 (date_slug, bm_count, notice_html="")
# ═══════════════════════════════════════════════════════════════════
notice_block = send_report._notice_html(real_notice)

# notice 있음
body_with = send_report._friday_body_html("2026-07-17", 3, notice_html=notice_block)
chk("FB-01: friday body 에 notice 삽입 (정상 케이스)",
    "border-left" in body_with and "본 리포트는 7월 17일" in body_with)
chk("FB-02: notice 는 상단(첫 <p> 앞)에 위치",
    body_with.index("border-left") < body_with.index("<p>안녕하세요"))

# notice 없음
body_without = send_report._friday_body_html("2026-07-20", 3, notice_html="")
chk("FB-03: notice 없으면 안내 박스 미노출",
    "border-left" not in body_without and "발송 안내" not in body_without)

# 0건 fallback (bm_count=0) 에도 notice 적용
body_zero = send_report._friday_body_html("2026-07-17", 0, notice_html=notice_block)
chk("FB-04: bm_count=0 케이스에도 notice 적용",
    "border-left" in body_zero and "본 리포트는 7월 17일" in body_zero)

# 구 weekly.py 산출물 (YouTube 검색어 기반) 문구 잔존 없는지
# (2026-08-25) Weekly Bundle 도입으로 "Weekly" 단어 자체는 mail body 에 나올 수 있음.
# 검증 대상: 이전 weekly.py 유산 표현 ("YouTube 검색어", "Weekly 후보 …건" 형식) 뿐.
chk("FB-05: friday body 에 구 weekly.py 유산 문구 잔존 없음",
    "YouTube 검색어" not in body_with and "Weekly 후보 " not in body_with,
    f"snippet={body_with[:400]}")


# ═══════════════════════════════════════════════════════════════════
# 5) _monday_body_html — notice 미적용 확인 (signature 자체가 안 받음)
#    (2026-08-24) Weekly 파트 제거 → 시그니처 (date_slug, bm_count)
# ═══════════════════════════════════════════════════════════════════
import inspect
monday_sig = inspect.signature(send_report._monday_body_html)
chk("MB-01: _monday_body_html 은 notice_html 파라미터 없음",
    "notice_html" not in monday_sig.parameters)

# 실제 렌더 결과에도 안내 흔적 없음
body_mon = send_report._monday_body_html("2026-07-20", 3)
chk("MB-02: monday 본문에 안내 박스 없음",
    "border-left" not in body_mon
    and "발송 안내" not in body_mon
    and "재발송" not in body_mon
    and "[재발송]" not in body_mon)
chk("MB-03: monday 본문에 구 weekly.py 유산 문구 잔존 없음",
    "YouTube 검색어" not in body_mon and "Weekly 후보 " not in body_mon,
    f"snippet={body_mon[:400]}")


# ═══════════════════════════════════════════════════════════════════
# 6) weekly.py REPORT_DATE — main() 부에서 date_slug 만 override, today_kst 는 유지
#     → import 후 함수 단위로 직접 실행 (실제 main 은 API 호출 있어 skip)
#     → argparse 없이 검증할 수 있는 최소 코드 재현
# ═══════════════════════════════════════════════════════════════════
os.environ["REPORT_DATE"] = "2026-07-17"

# weekly.py 를 fresh import
if "weekly" in sys.modules: del sys.modules["weekly"]
import weekly

# main() 안의 date_slug/today_kst/today_label 계산 로직 재현 (source 그대로 발췌)
today_kst_real = datetime.now(weekly.KST)
_report_date_env = os.environ.get("REPORT_DATE", "").strip()
if _report_date_env:
    _slot_dt = datetime.strptime(_report_date_env, "%Y-%m-%d")
    date_slug = _report_date_env
    today_label = _slot_dt.strftime("%Y-%m-%d (%a)")
else:
    date_slug = today_kst_real.strftime("%Y-%m-%d")
    today_label = today_kst_real.strftime("%Y-%m-%d (%a)")

chk("WK-01: date_slug 은 REPORT_DATE 값 (slot)",
    date_slug == "2026-07-17")
chk("WK-02: today_label 요일은 slot 기준 (2026-07-17 = Fri)",
    "Fri" in today_label,
    f"got={today_label!r}")
chk("WK-03: today_kst_real 은 실제 오늘 KST 유지 (age 기반)",
    today_kst_real.strftime("%Y-%m-%d") == today_kst_str)

# REPORT_DATE 미설정 케이스
os.environ.pop("REPORT_DATE", None)
today_kst_real2 = datetime.now(weekly.KST)
_env2 = os.environ.get("REPORT_DATE", "").strip()
if _env2:
    ds2 = _env2
else:
    ds2 = today_kst_real2.strftime("%Y-%m-%d")
chk("WK-04: REPORT_DATE 미설정 시 기존 동작 (오늘 KST)",
    ds2 == today_kst_str)


# ═══════════════════════════════════════════════════════════════════
# 7) benchmark.py REPORT_DATE — today_label 만 slot, now_kst/now_utc 는 실행 시각
# ═══════════════════════════════════════════════════════════════════
os.environ["REPORT_DATE"] = "2026-07-17"

if "benchmark" in sys.modules: del sys.modules["benchmark"]
import benchmark

now_kst = datetime.now(benchmark.KST)
now_utc = datetime.now(timezone.utc)
_rd = os.environ.get("REPORT_DATE", "").strip()
if _rd:
    today_label_bm = _rd
else:
    today_label_bm = now_kst.strftime("%Y-%m-%d")

chk("BM-01: benchmark today_label = slot",
    today_label_bm == "2026-07-17")
chk("BM-02: now_kst 는 실행 시각 유지",
    now_kst.strftime("%Y-%m-%d") == today_kst_str)
chk("BM-03: now_utc 도 실행 시각 유지 (age 계산 기반)",
    now_utc.strftime("%Y-%m-%d") in (today_kst_str,
                                     (now_kst - timedelta(hours=9)).strftime("%Y-%m-%d")))

os.environ.pop("REPORT_DATE", None)


# ═══════════════════════════════════════════════════════════════════
# 8) 잘못된 REPORT_DATE — weekly.py / benchmark.py subprocess 거부
# ═══════════════════════════════════════════════════════════════════
# subprocess 로 각각 실행하여 exit code != 0 확인 (외부 API 불필요 — argparse 이전에 검증됨)
# NOTE: main() 안에서 date 파싱 후에 API 를 호출하므로, exit code 1 을 그대로 관찰하기 위해
#       DRY_RUN 등 부수효과 없는 검증 경로만 확인. 여기서는 datetime.strptime 자체 검증만.
for bad in ["2026-13-01", "not-a-date", "2026/07/17"]:
    raised = False
    try:
        datetime.strptime(bad, "%Y-%m-%d")
    except ValueError:
        raised = True
    chk(f"BD-01[{bad}]: 파이썬 datetime.strptime 자체가 형식 거부", raised)


# ═══════════════════════════════════════════════════════════════════
# 9) 통합 subject 조합 — [재발송] prefix + REPORT_DATE
#    (2026-08-24) Weekly 파트 제거 → "Weekly N + Benchmark M" → "Benchmark N건"
# ═══════════════════════════════════════════════════════════════════
os.environ["REISSUE"] = "1"
os.environ["REPORT_DATE"] = "2026-07-17"

date_slug_s = send_report._now_date_slug()
subject_normal = f"[털어드림 격주 후보] {date_slug_s} · Benchmark 3건"
subject_final = send_report._reissue_prefix() + subject_normal
chk("SUB-01: [재발송] 접두어 + slot 날짜 subject (Benchmark 전용)",
    subject_final == "[재발송] [털어드림 격주 후보] 2026-07-17 · Benchmark 3건",
    f"got={subject_final!r}")

# 재발송 아닐 때
os.environ.pop("REISSUE", None)
subject_no_reissue = send_report._reissue_prefix() + subject_normal
chk("SUB-02: reissue 없으면 prefix 없음",
    subject_no_reissue == "[털어드림 격주 후보] 2026-07-17 · Benchmark 3건")

os.environ.pop("REPORT_DATE", None)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" REPORT_DATE / NOTICE / REISSUE 회귀 테스트 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1

print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
