# -*- coding: utf-8 -*-
"""Orchestrator DRY_RUN 구조 검증 (subprocess/SMTP 없이).

지키는 원칙:
  · Weekly Bundle 은 3 target × mode=weekly benchmark subprocess 를 순차 호출.
    각 subprocess 는 TARGET/MODE/REPORT_FRAGMENT_PATH env 를 정확히 넘겨야 함.
  · Standard 는 target=teoldeurim + mode=standard benchmark subprocess 1회 호출.
  · DRY_RUN=1 에서는 Gmail send · sent history 기록 · git push 는 발생하지 않음.

실행:
  python tests/test_orchestrator_dryrun.py
"""
import os
import sys
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for m in ["send_report", "benchmark_config"]:
    if m in sys.modules:
        del sys.modules[m]

import send_report

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


# ═══════════════════════════════════════════════════════════════════
# monkeypatch: _run_child (subprocess) 를 fake 로 대체 + fragment 파일 fake 생성
# ═══════════════════════════════════════════════════════════════════
_run_child_calls = []

def _fake_run_child(script, extra_env, timeout=1200):
    _run_child_calls.append({
        "script": Path(script).name,
        "extra_env": dict(extra_env),
        "timeout": timeout,
    })
    # fragment 파일 만들어 성공 시뮬레이션
    frag = extra_env.get("REPORT_FRAGMENT_PATH")
    if frag:
        Path(frag).parent.mkdir(parents=True, exist_ok=True)
        Path(frag).write_text(f"<html>fake {extra_env.get('TARGET','?')}"
                              f"/{extra_env.get('MODE','?')}</html>",
                              encoding="utf-8")
    return 0

send_report._run_child = _fake_run_child


# _load_benchmark_result_ids stub — filtered_raw 없어도 empty 반환
def _fake_load_ids(_path):
    return ["v_stub_1", "v_stub_2"]
send_report._load_benchmark_result_ids = _fake_load_ids

_send_calls = []
def _fake_send(env, **kwargs):
    _send_calls.append(kwargs)
send_report.send_email = _fake_send

_record_calls = []
def _fake_record(profile, date_slug, video_ids, target_slug=None):
    _record_calls.append({
        "profile": profile, "target_slug": target_slug,
        "count": len(video_ids), "date_slug": date_slug,
    })
send_report._record_benchmark_sent = _fake_record

send_report._env_snapshot = lambda: {
    "GMAIL_ADDRESS": "bot@x.com",
    "GMAIL_APP_PASSWORD": "pass",
    "RECIPIENT_EMAIL": "user@x.com",
}


# ═══════════════════════════════════════════════════════════════════
# CASE A) Weekly Bundle · dry_run=False → 정상 흐름 (subprocess 3회 · 발송 1회)
# ═══════════════════════════════════════════════════════════════════
tmp = ROOT / "local_output" / "_dryrun_tmp"
tmp.mkdir(parents=True, exist_ok=True)

_run_child_calls.clear(); _send_calls.clear(); _record_calls.clear()
send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=False)

chk("BUNDLE-01: benchmark subprocess 정확히 3회 호출",
    len(_run_child_calls) == 3, f"got {len(_run_child_calls)}")
targets_called = [c["extra_env"].get("TARGET") for c in _run_child_calls]
chk("BUNDLE-02: 3 target 순서 = teoldeurim, myohanduk, jjalduk",
    targets_called == ["teoldeurim", "myohanduk", "jjalduk"],
    f"got {targets_called}")
modes_called = [c["extra_env"].get("MODE") for c in _run_child_calls]
chk("BUNDLE-03: 모든 호출 MODE=weekly",
    modes_called == ["weekly", "weekly", "weekly"])
# teoldeurim 은 backward-compat 로 PROFILE=recent 를 함께 전달
chk("BUNDLE-04: teoldeurim 호출은 PROFILE=recent (backward compat 파일명)",
    _run_child_calls[0]["extra_env"].get("PROFILE") == "recent")
chk("BUNDLE-05: myohanduk 호출은 PROFILE=weekly",
    _run_child_calls[1]["extra_env"].get("PROFILE") == "weekly")
chk("BUNDLE-06: send_email 1회 호출",
    len(_send_calls) == 1)
chk("BUNDLE-07: sent history 3회 기록 (target namespace 각각)",
    len(_record_calls) == 3)
target_slugs_recorded = {r["target_slug"] for r in _record_calls}
chk("BUNDLE-08: 기록된 target slug = 3 target",
    target_slugs_recorded == {"teoldeurim", "myohanduk", "jjalduk"})


# ═══════════════════════════════════════════════════════════════════
# CASE B) Weekly Bundle · dry_run=True → subprocess 는 실행되지만 send/record 없음
# ═══════════════════════════════════════════════════════════════════
_run_child_calls.clear(); _send_calls.clear(); _record_calls.clear()
send_report.run_weekly_bundle("2026-09-04", tmp, dry_run=True)
chk("DRY-BUNDLE-01: subprocess 3회 실행 (Apify/Claude 유료 호출 자체는 실행)",
    len(_run_child_calls) == 3)
chk("DRY-BUNDLE-02: DRY_RUN send_email 0회",
    len(_send_calls) == 0)
chk("DRY-BUNDLE-03: DRY_RUN sent history 0회 (production 오염 방지)",
    len(_record_calls) == 0)


# ═══════════════════════════════════════════════════════════════════
# CASE C) Standard (run_friday) · dry_run=False → subprocess 1회 · 발송 1회
# ═══════════════════════════════════════════════════════════════════
_run_child_calls.clear(); _send_calls.clear(); _record_calls.clear()
send_report.run_friday("2026-09-04", tmp, dry_run=False)
chk("STD-01: benchmark subprocess 1회 호출",
    len(_run_child_calls) == 1)
chk("STD-02: TARGET=teoldeurim, MODE=standard",
    _run_child_calls[0]["extra_env"].get("TARGET") == "teoldeurim"
    and _run_child_calls[0]["extra_env"].get("MODE") == "standard")
chk("STD-03: send_email 1회",
    len(_send_calls) == 1)
chk("STD-04: sent history 1회 (target=teoldeurim)",
    len(_record_calls) == 1
    and _record_calls[0]["target_slug"] == "teoldeurim"
    and _record_calls[0]["profile"] == "standard")
chk("STD-05: subject 에 '[털어드림 격주 후보]' prefix",
    "[털어드림 격주 후보]" in _send_calls[0]["subject"])
chk("STD-06: 첨부 파일명 teoldeurim_standard_*",
    _send_calls[0]["attachment_filename"].startswith("teoldeurim_standard_"))
chk("STD-07: Weekly Bundle 과 별개 메일 (extra_attachments 없음)",
    not _send_calls[0].get("extra_attachments"))


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" Orchestrator DRY_RUN 구조 회귀 결과")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
