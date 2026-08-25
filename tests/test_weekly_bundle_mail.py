# -*- coding: utf-8 -*-
"""Weekly Bundle 3첨부 1메일 · 부분실패 시 skip 회귀.

지키는 원칙:
  · Weekly Bundle 실행 성공 시 Gmail send_email 은 정확히 1회 호출.
  · 첨부는 3개 (attachment_html 1개 + extra_attachments 2개).
  · 3 target 중 하나라도 실패하면 raise → send_email 호출 없음.
  · sent history 는 발송 성공 후에만 각 target namespace 에 기록.
  · Standard 실행은 별도 메일 1개 (첨부 1개).

fixture 기반이라 실제 subprocess 실행·SMTP 없이 검증.

실행:
  python tests/test_weekly_bundle_mail.py
"""
import sys
from datetime import datetime
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["send_report", "benchmark_config", "emailer"]:
    if m in sys.modules:
        del sys.modules[m]

import send_report

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# monkeypatch: _run_target_weekly / send_email / _record_benchmark_sent / _env_snapshot
# ═══════════════════════════════════════════════════════════════════
_ENV_STUB = {
    "GMAIL_ADDRESS": "bot@x.com",
    "GMAIL_APP_PASSWORD": "pass",
    "RECIPIENT_EMAIL": "user@x.com",
}
send_report._env_snapshot = lambda: dict(_ENV_STUB)

_send_calls = []
def _fake_send(env, **kwargs):
    _send_calls.append({"env": env, **kwargs})
send_report.send_email = _fake_send

_record_calls = []
def _fake_record(profile, date_slug, video_ids, target_slug=None):
    _record_calls.append({
        "profile": profile, "date_slug": date_slug,
        "video_ids": list(video_ids), "target_slug": target_slug,
    })
send_report._record_benchmark_sent = _fake_record

_run_results = {}
def _fake_run(date_slug, tmp_dir, target_slug):
    return _run_results.get(target_slug)
send_report._run_target_weekly = _fake_run


# ═══════════════════════════════════════════════════════════════════
# CASE 1) 정상 케이스 — 3 target 모두 성공 → 1 메일 · 3 첨부 · 3 sent 기록
# ═══════════════════════════════════════════════════════════════════
_send_calls.clear(); _record_calls.clear()
_run_results.clear()
_run_results.update({
    "teoldeurim": {"html": "<html>te</html>", "video_ids": ["v1", "v2"], "raw_path": Path()},
    "myohanduk":  {"html": "<html>mh</html>", "video_ids": ["v3"], "raw_path": Path()},
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": ["v4", "v5", "v6"], "raw_path": Path()},
})

# tmp_dir 필수 인자 (실제 fs 안 씀)
tmp = ROOT / "local_output"
tmp.mkdir(exist_ok=True)
send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=False)

chk("OK-01: Gmail send_email 정확히 1회 호출",
    len(_send_calls) == 1, f"got {len(_send_calls)}")
call = _send_calls[0]
chk("OK-02: attachment_html 은 첫 target(teoldeurim) HTML",
    call["attachment_html"] == "<html>te</html>")
chk("OK-03: extra_attachments 는 2개 (myohanduk + jjalduk)",
    len(call["extra_attachments"]) == 2)
chk("OK-04: 두 번째 첨부 = myohanduk",
    call["extra_attachments"][0][0].startswith("myohanduk_weekly_2026-09-04"))
chk("OK-05: 세 번째 첨부 = jjalduk",
    call["extra_attachments"][1][0].startswith("jjalduk_weekly_2026-09-04"))
chk("OK-06: attachment_filename = teoldeurim_weekly_2026-09-04.html",
    call["attachment_filename"] == "teoldeurim_weekly_2026-09-04.html")
chk("OK-07: subject 에 3개 채널 카운트 포함",
    "2건" in call["subject"] and "1건" in call["subject"] and "3건" in call["subject"],
    f"subject={call['subject']!r}")
chk("OK-08: subject 에 3개 target 이름 prefix 포함",
    "[털어드림/묘한덕질/짤덕방 Weekly]" in call["subject"])

chk("OK-09: sent history 정확히 3회 기록",
    len(_record_calls) == 3)
by_target = {r["target_slug"]: r for r in _record_calls}
chk("OK-10: teoldeurim sent target_slug='teoldeurim'",
    "teoldeurim" in by_target and by_target["teoldeurim"]["video_ids"] == ["v1", "v2"])
chk("OK-11: myohanduk sent target_slug='myohanduk'",
    "myohanduk" in by_target and by_target["myohanduk"]["video_ids"] == ["v3"])
chk("OK-12: jjalduk sent target_slug='jjalduk'",
    "jjalduk" in by_target and by_target["jjalduk"]["video_ids"] == ["v4", "v5", "v6"])
chk("OK-13: 모든 기록의 profile='weekly'",
    all(r["profile"] == "weekly" for r in _record_calls))


# ═══════════════════════════════════════════════════════════════════
# CASE 2) 부분 실패 — myohanduk 실패 시 raise · send_email 0회 · sent 0회
# ═══════════════════════════════════════════════════════════════════
_send_calls.clear(); _record_calls.clear()
_run_results.clear()
_run_results.update({
    "teoldeurim": {"html": "<html>te</html>", "video_ids": ["v1"], "raw_path": Path()},
    "myohanduk":  None,   # 실패
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": ["v9"], "raw_path": Path()},
})

_raised = False
try:
    send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=False)
except RuntimeError as e:
    _raised = True
    err_msg = str(e)

chk("FAIL-01: 부분 실패 시 RuntimeError raise",
    _raised)
chk("FAIL-02: raise 메시지에 실패 target 이름 포함 (묘한덕질)",
    _raised and "묘한덕질" in err_msg)
chk("FAIL-03: send_email 0회 호출",
    len(_send_calls) == 0)
chk("FAIL-04: sent history 0회 기록 (부분 첨부·부분 dedup 방지)",
    len(_record_calls) == 0)


# ═══════════════════════════════════════════════════════════════════
# CASE 3) DRY_RUN — 3 target 성공하지만 send / sent 모두 skip
# ═══════════════════════════════════════════════════════════════════
_send_calls.clear(); _record_calls.clear()
_run_results.clear()
_run_results.update({
    "teoldeurim": {"html": "<html>te</html>", "video_ids": ["v1"], "raw_path": Path()},
    "myohanduk":  {"html": "<html>mh</html>", "video_ids": ["v2"], "raw_path": Path()},
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": ["v3"], "raw_path": Path()},
})
send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=True)
chk("DRY-01: DRY_RUN 에서 send_email 호출 0회",
    len(_send_calls) == 0)
chk("DRY-02: DRY_RUN 에서 sent history 기록 0회 (production 오염 방지)",
    len(_record_calls) == 0)


# ═══════════════════════════════════════════════════════════════════
# CASE 4) Weekly Bundle 은 3 개 모두 0건이어도 정상 발송
# ═══════════════════════════════════════════════════════════════════
_send_calls.clear(); _record_calls.clear()
_run_results.clear()
_run_results.update({
    "teoldeurim": {"html": "<html>te</html>", "video_ids": [], "raw_path": Path()},
    "myohanduk":  {"html": "<html>mh</html>", "video_ids": [], "raw_path": Path()},
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": [], "raw_path": Path()},
})
send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=False)
chk("ZERO-01: 3개 모두 0건이어도 send_email 1회 호출 (0건 알림)",
    len(_send_calls) == 1)
chk("ZERO-02: subject 에 '신규 후보 없음'",
    "신규 후보 없음" in _send_calls[0]["subject"])
# sent history 는 video_ids 가 empty 라 skip 되는 것이 자연 (내부 record 함수 로직).


# ═══════════════════════════════════════════════════════════════════
# CASE 5) TEST_MODE=1 — subject '[TEST] ' 접두어 + sent history skip
# ═══════════════════════════════════════════════════════════════════
import os as _os
_os.environ["TEST_MODE"] = "1"
_send_calls.clear(); _record_calls.clear()
_run_results.clear()
_run_results.update({
    "teoldeurim": {"html": "<html>te</html>", "video_ids": ["v_t1", "v_t2"], "raw_path": Path()},
    "myohanduk":  {"html": "<html>mh</html>", "video_ids": ["v_m1"], "raw_path": Path()},
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": ["v_j1"], "raw_path": Path()},
})

# 실 record 함수로 복원 (앞선 fake 는 skip 로직 검증에 부적합)
import benchmark_config as _bcfg
_saved_root = ROOT / _bcfg.BENCHMARK_SENT_HISTORY_DIR
# _record_benchmark_sent 원본 재-import
import importlib as _il
_il.reload(send_report)
_il.reload(send_report)
# 다시 monkeypatch (reload 후)
send_report._env_snapshot = lambda: dict(_ENV_STUB)
send_report._run_target_weekly = _fake_run
_send_calls.clear()
send_report.send_email = lambda env, **kw: _send_calls.append(kw)

# 실 _record_benchmark_sent 를 감싸 어떤 파일이 저장 시도되는지 관찰
_saved_files = []
_orig_record = send_report._record_benchmark_sent
def _spy_record(*args, **kwargs):
    r = _orig_record(*args, **kwargs)
    _saved_files.append(r)
    return r
send_report._record_benchmark_sent = _spy_record

send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=False)

chk("TEST-01: send_email 1회 호출 (실 발송 자체는 정상)",
    len(_send_calls) == 1)
chk("TEST-02: subject 앞에 '[TEST] ' 접두어",
    _send_calls[0]["subject"].startswith("[TEST] "),
    f"got={_send_calls[0]['subject']!r}")
chk("TEST-03: _record_benchmark_sent 3회 호출은 됨 (내부에서 skip)",
    len(_saved_files) == 3)
chk("TEST-04: 모든 record 결과 None (TEST_MODE skip)",
    all(r is None for r in _saved_files))

# 실 파일 시스템에 새 파일 생성 안 됨 확인 — target namespace 디렉토리 미생성
for slug in ("teoldeurim", "myohanduk", "jjalduk"):
    ns = _saved_root / slug
    fresh = [f for f in ns.glob("2026-09-04_*.json") if f.is_file()] if ns.exists() else []
    chk(f"TEST-05[{slug}]: TEST_MODE 실행 후 2026-09-04 신규 파일 없음",
        len(fresh) == 0, f"unexpected files: {fresh}")

_os.environ.pop("TEST_MODE", None)


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" Weekly Bundle 메일 처리 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
