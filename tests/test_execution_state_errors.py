# -*- coding: utf-8 -*-
"""send_report.main() top-level 오류 처리 — execution state 장애와 기존 오류 메일 정책 분리 회귀.

지키는 원칙:
  · execution state(status/marker) 기록 실패 → 수신자 오류 메일 0회 · stderr 로그 · exit 1.
      - sending 기록 실패: 리포트 SMTP 0회
      - sent 기록 실패   : 리포트 SMTP 1회 · sent history 0 · status=sending(ambiguous) 유지
      - marker 기록 실패 : 리포트 SMTP 1회 · status=sent(ambiguous) 유지
  · 리포트 생성 실패 · Gmail 리포트 발송 실패 → 기존처럼 오류 메일 1회 (GitHub/Cloud Run 공통).
  · EXECUTION_STATE_KEY 없는 GitHub/local 경로 무변화.

send_report.main() 을 그대로 실행. benchmark 실행·SMTP·GCS 는 fake.

실행:
  python tests/test_execution_state_errors.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))

_ENV_KEYS = ("SENT_HISTORY_BACKEND", "SENT_HISTORY_GCS_BUCKET", "STATE_NAMESPACE", "TEST_MODE",
             "EXECUTION_STATE_KEY", "EXECUTION_RUN_ID", "REISSUE", "NOTICE", "REPORT_DATE", "DRY_RUN")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import send_report
from fake_gcs import FakeBucket, FakeGcsError

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


_buckets = {}
storage._make_gcs_bucket = lambda name: _buckets.setdefault(name, FakeBucket(name))

send_report._load_dotenv_if_present = lambda: None   # 로컬 .env 가 섞이지 않도록
_TMP = Path(tempfile.mkdtemp(prefix="state_err_"))
send_report.ROOT = _TMP
send_report.LOCAL_OUTPUT_DIR = _TMP / "local_output"
send_report._env_snapshot = lambda: {"GMAIL_ADDRESS": "bot@x.com", "GMAIL_APP_PASSWORD": "p",
                                     "RECIPIENT_EMAIL": "user@x.com"}

_failing_targets = set()
def _fake_target(date_slug, tmp_dir, slug):
    if slug in _failing_targets:
        return None
    return {"html": f"<html>{slug}</html>", "video_ids": [f"{slug}-1"], "raw_path": Path()}
send_report._run_target_weekly = _fake_target

def _fake_run_child(script, extra_env, timeout=1200):
    frag = Path(extra_env["REPORT_FRAGMENT_PATH"])
    frag.write_text("<html>std</html>", encoding="utf-8")
    raw = send_report.ROOT / "benchmark" / f"{frag.name[:10]}_standard_filtered_raw.json"
    raw.parent.mkdir(exist_ok=True)
    raw.write_text(json.dumps({"stages": {"direct_final": [{"video_id": "s1"}], "benchmark_final": []}}),
                   encoding="utf-8")
    return 0
send_report._run_child = _fake_run_child


class Mail:
    """리포트 SMTP(send_email) · 오류 메일(send_error_email) spy."""
    def __init__(self, after_report=None, fail_report=False):
        self.reports, self.errors = [], []
        self.after_report, self.fail_report = after_report, fail_report

    def send_email(self, env, **kw):
        self.reports.append(kw["subject"])
        if self.fail_report:
            raise OSError("SMTP 421 service not available")
        if self.after_report:
            self.after_report()

    def send_error_email(self, env, error_msg, subject=""):
        self.errors.append(subject)


def invoke_main(mode, mail):
    """현재 os.environ 그대로 send_report.main() 실행 → (exit code, stderr)."""
    send_report.send_email = mail.send_email
    send_report.send_error_email = mail.send_error_email
    sys.argv = ["send_report.py", f"--mode={mode}"]
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            send_report.main()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, err.getvalue()


def run_main(mode, mail, **env):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    rc, err = invoke_main(mode, mail)
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    return rc, err


def gcs_env(bucket, date, mode):
    return {"SENT_HISTORY_BACKEND": "gcs", "SENT_HISTORY_GCS_BUCKET": bucket, "STATE_NAMESPACE": "prod",
            "REPORT_DATE": date, "EXECUTION_STATE_KEY": f"{date}_{mode}", "EXECUTION_RUN_ID": f"RUN-{bucket}"}


def seed_pending(bucket, key):
    storage.GcsStore(_buckets.setdefault(bucket, FakeBucket(bucket)), "prod").write_status(key, "pending", run_id="orch")


def state_of(bucket, key):
    b = _buckets[bucket]
    k = f"prod/status/{key}.json"
    return json.loads(b.text(k))["state"] if k in b.objects else None


W, S = "weekly-bundle", "friday"


# ═══════════════════════════════════════════════════════════════════
# 1) sending 기록 실패 — SMTP 전
# ═══════════════════════════════════════════════════════════════════
key = f"2099-06-05_{W}"
seed_pending("e1", key)
_buckets["e1"].faults.append(("upload", f"prod/status/{key}", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
rc, err = run_main(W, mail, **gcs_env("e1", "2099-06-05", W))
chk("SENDING-01: exit != 0", rc != 0, f"rc={rc}")
chk("SENDING-02: 리포트 SMTP 0회", mail.reports == [])
chk("SENDING-03: 수신자 오류 메일 0회", mail.errors == [], f"{mail.errors}")
chk("SENDING-04: status = pending 유지 (재실행 가능)", state_of("e1", key) == "pending")
chk("SENDING-05: stderr 에 원인·state·메일 미발송 안내",
    "execution state 기록 실패" in err and "→ sending" in err and "FakeGcsError" in err
    and "오류 메일은 보내지 않습니다" in err, err[:300])


# ═══════════════════════════════════════════════════════════════════
# 2) 리포트 SMTP 성공 후 sent 기록 실패
# ═══════════════════════════════════════════════════════════════════
key = f"2099-06-12_{W}"
seed_pending("e2", key)
mail = Mail(after_report=lambda: _buckets["e2"].faults.append(
    ("upload", f"prod/status/{key}", "before", FakeGcsError(503, "unavailable"))))
rc, err = run_main(W, mail, **gcs_env("e2", "2099-06-12", W))
chk("SENT-01: exit != 0", rc != 0)
chk("SENT-02: 리포트 SMTP 정확히 1회", len(mail.reports) == 1)
chk("SENT-03: 추가 오류 메일 0회", mail.errors == [], f"{mail.errors}")
chk("SENT-04: sent history 기록 0회", _buckets["e2"].keys("prod/sent/") == [])
chk("SENT-05: status = sending 유지 (ambiguous → 자동 재발송 금지)", state_of("e2", key) == "sending")
chk("SENT-06: marker 없음", _buckets["e2"].keys("prod/markers/") == [])
chk("SENT-07: stderr 에 '→ sent' 실패 기록", "→ sent" in err)


# ═══════════════════════════════════════════════════════════════════
# 3) sent history 기록 후 marker 기록 실패
# ═══════════════════════════════════════════════════════════════════
key = f"2099-06-19_{W}"
seed_pending("e3", key)
_buckets["e3"].faults.append(("upload", f"prod/markers/{key}", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
rc, err = run_main(W, mail, **gcs_env("e3", "2099-06-19", W))
chk("MARKER-01: exit != 0 · SMTP 1회 · 오류 메일 0회", rc != 0 and len(mail.reports) == 1 and mail.errors == [])
chk("MARKER-02: sent history 2개 기록됨 (Weekly target 2개, jjalduk 없음)",
    len(_buckets["e3"].keys("prod/sent/")) == 2 and _buckets["e3"].keys("prod/sent/jjalduk/") == [])
chk("MARKER-03: status = sent 유지 (ambiguous)", state_of("e3", key) == "sent")
chk("MARKER-04: marker 없음", _buckets["e3"].keys("prod/markers/") == [])


# ═══════════════════════════════════════════════════════════════════
# 4) Standard 도 동일 (sent 기록 실패)
# ═══════════════════════════════════════════════════════════════════
key = f"2099-06-26_{S}"
seed_pending("e4", key)
mail = Mail(after_report=lambda: _buckets["e4"].faults.append(
    ("upload", f"prod/status/{key}", "before", FakeGcsError(503, "unavailable"))))
rc, err = run_main(S, mail, **gcs_env("e4", "2099-06-26", S))
chk("STD-SENT-01: exit != 0 · SMTP 1회 · 오류 메일 0회",
    rc != 0 and len(mail.reports) == 1 and mail.errors == [])
chk("STD-SENT-02: sent history 0 · status = sending",
    _buckets["e4"].keys("prod/sent/") == [] and state_of("e4", key) == "sending")


# ═══════════════════════════════════════════════════════════════════
# 5) EXECUTION_STATE_KEY 는 있는데 backend 설정 오류 (local)
# ═══════════════════════════════════════════════════════════════════
mail = Mail()
rc, err = run_main(W, mail, REPORT_DATE="2099-07-03", EXECUTION_STATE_KEY=f"2099-07-03_{W}",
                   EXECUTION_RUN_ID="RUN-e5")
chk("CONFIG-01: exit != 0 · SMTP 0회 · 오류 메일 0회",
    rc != 0 and mail.reports == [] and mail.errors == [])
chk("CONFIG-02: stderr 에 UnsupportedStorageBackend 원인", "UnsupportedStorageBackend" in err)


# ═══════════════════════════════════════════════════════════════════
# 6) 기존 오류 메일 정책 유지 — 리포트 생성 실패 · Gmail 발송 실패
# ═══════════════════════════════════════════════════════════════════
_failing_targets.add("myohanduk")
key = f"2099-07-10_{W}"
seed_pending("e6", key)
mail = Mail()
rc, _ = run_main(W, mail, **gcs_env("e6", "2099-07-10", W))
chk("EXISTING-01: Cloud Run 경로 리포트 생성 실패 → 오류 메일 1회 (기존 정책)",
    rc == 1 and mail.reports == [] and mail.errors == ["[털어드림 자동화] ❌ orchestrator 실패 (weekly-bundle)"],
    f"{mail.errors}")
chk("EXISTING-02: 리포트 생성 실패 시 status = pending 유지", state_of("e6", key) == "pending")
mail = Mail()
rc, _ = run_main(W, mail, REPORT_DATE="2099-07-10")
chk("EXISTING-03: GitHub/local 경로 리포트 생성 실패 → 오류 메일 1회 (무변화)",
    rc == 1 and mail.errors == ["[털어드림 자동화] ❌ orchestrator 실패 (weekly-bundle)"])
_failing_targets.clear()

key = f"2099-07-17_{W}"
seed_pending("e7", key)
mail = Mail(fail_report=True)
rc, _ = run_main(W, mail, **gcs_env("e7", "2099-07-17", W))
chk("EXISTING-04: Gmail 리포트 발송 실패 → 오류 메일 1회 (기존 정책) · status = sending",
    rc == 1 and len(mail.reports) == 1 and len(mail.errors) == 1 and state_of("e7", key) == "sending")


# ═══════════════════════════════════════════════════════════════════
# 6-1) SMTP 성공 → sent 기록 → GCS sent history 기록 실패 (Cloud Run 경로)
# ═══════════════════════════════════════════════════════════════════
key = f"2099-07-24_{W}"
seed_pending("e8", key)
_buckets["e8"].faults.append(("upload", "prod/sent/myohanduk/", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
rc, err = run_main(W, mail, **gcs_env("e8", "2099-07-24", W))
chk("HIST-01: exit != 0", rc != 0)
chk("HIST-02: 리포트 SMTP 정확히 1회", len(mail.reports) == 1)
chk("HIST-03: 수신자 오류 메일 0회", mail.errors == [], f"{mail.errors}")
chk("HIST-04: status = sent 유지 (ambiguous)", state_of("e8", key) == "sent")
chk("HIST-05: completed marker 없음", _buckets["e8"].keys("prod/markers/") == [])
chk("HIST-06: stderr 에 실패한 sent history 위치·원인·메일 미발송 안내",
    "sent history 기록 실패" in err and "myohanduk/2099-07-24_weekly.json" in err
    and "FakeGcsError" in err and "오류 메일은 보내지 않습니다" in err, err[:300])

key = f"2099-07-24_{S}"
seed_pending("e8s", key)
_buckets["e8s"].faults.append(("upload", "prod/sent/teoldeurim/", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
rc, _ = run_main(S, mail, **gcs_env("e8s", "2099-07-24", S))
chk("HIST-STD: Standard 도 동일 — SMTP 1 · 오류 메일 0 · status sent · marker 없음",
    rc != 0 and len(mail.reports) == 1 and mail.errors == [] and state_of("e8s", key) == "sent"
    and _buckets["e8s"].keys("prod/markers/") == [])


# ═══════════════════════════════════════════════════════════════════
# 6-2) GitHub/local 경로의 sent history 기록 실패 → 기존 semantics (오류 메일 1회)
# ═══════════════════════════════════════════════════════════════════
_orig_local_write = storage.LocalSentHistoryStore.write_sent
def _failing_local_write(self, name, payload, target_slug=None):
    raise OSError("disk full")
storage.LocalSentHistoryStore.write_sent = _failing_local_write
mail = Mail()
rc, err = run_main(W, mail, REPORT_DATE="2099-07-24")
storage.LocalSentHistoryStore.write_sent = _orig_local_write
chk("LOCAL-HIST-01: GitHub/local 경로 — SMTP 1회 후 기존처럼 오류 메일 1회 · exit 1",
    rc == 1 and len(mail.reports) == 1
    and mail.errors == ["[털어드림 자동화] ❌ orchestrator 실패 (weekly-bundle)"], f"{mail.errors}")
chk("LOCAL-HIST-02: GitHub/local 경로는 원래 예외 그대로 (ExecutionPersistenceError 로 바꾸지 않음)",
    "ExecutionPersistenceError" not in err and "오류 메일은 보내지 않습니다" not in err)


# ═══════════════════════════════════════════════════════════════════
# 6-3) orchestrator 까지 연결 — 최종 exit 3 · 재실행 시 자동 재발송 없음
# ═══════════════════════════════════════════════════════════════════
import friday_orchestrator as orch
from datetime import datetime, timedelta, timezone

def orch_run(bucket_name, report_date, now, mail):
    """send_report.main() 을 subprocess 대신 in-process 로 실행하는 runner 로 orchestrator 실행."""
    base = {"SENT_HISTORY_BACKEND": "gcs", "SENT_HISTORY_GCS_BUCKET": bucket_name,
            "STATE_NAMESPACE": "prod", "REPORT_DATE": report_date}
    def runner(mode, extra_env):
        os.environ.update(extra_env)
        try:
            rc, _ = invoke_main(mode, mail)
        finally:
            for k in extra_env:
                os.environ.pop(k, None)
        mail.child_rcs.append(rc)
        return rc
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(base)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = orch.main(now=now, runner=runner)
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    return rc, err.getvalue()

ODD_FRI = datetime(2026, 11, 6, 8, 20, tzinfo=timezone(timedelta(hours=9)))   # W45

# Weekly Bundle 마지막 target(myohanduk) 의 sent history 쓰기 실패
_buckets.setdefault("o1", FakeBucket("o1")).faults.append(
    ("upload", "prod/sent/myohanduk/", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
mail.child_rcs = []
rc, err = orch_run("o1", "2099-08-14", ODD_FRI, mail)
chk("ORCH-HIST-01: send_report 자체는 exit 1, orchestrator 최종 exit 3 (ambiguous)",
    mail.child_rcs == [1] and rc == 3, f"child={mail.child_rcs} rc={rc}")
chk("ORCH-HIST-02: 리포트 SMTP 1회 · 오류 메일 0회", len(mail.reports) == 1 and mail.errors == [])
chk("ORCH-HIST-03: Weekly status = sent · marker 없음 · Standard 미실행",
    state_of("o1", "2099-08-14_weekly-bundle") == "sent"
    and _buckets["o1"].keys("prod/markers/") == []
    and state_of("o1", "2099-08-14_friday") is None)
chk("ORCH-HIST-04: orchestrator stderr 에 AMBIGUOUS 안내", "AMBIGUOUS — 2099-08-14_weekly-bundle (state=sent)" in err)
chk("ORCH-HIST-05: lock 해제", _buckets["o1"].keys("prod/locks/") == [])
mail2 = Mail()
mail2.child_rcs = []
rc, _ = orch_run("o1", "2099-08-14", ODD_FRI + timedelta(hours=1), mail2)
chk("ORCH-HIST-06: 재실행 → send_report 미실행 · SMTP 0 · 오류 메일 0 · exit 3",
    rc == 3 and mail2.child_rcs == [] and mail2.reports == [] and mail2.errors == [])

_buckets.setdefault("o2", FakeBucket("o2")).faults.append(
    ("upload", "prod/sent/teoldeurim/2099-08-21_standard", "before", FakeGcsError(503, "unavailable")))
mail = Mail()
mail.child_rcs = []
rc, _ = orch_run("o2", "2099-08-21", ODD_FRI, mail)
chk("ORCH-HIST-07: Weekly 완료 후 Standard history 실패 → exit 3 · SMTP 2 · 오류 메일 0",
    rc == 3 and mail.child_rcs == [0, 1] and len(mail.reports) == 2 and mail.errors == []
    and state_of("o2", "2099-08-21_weekly-bundle") == "completed"
    and state_of("o2", "2099-08-21_friday") == "sent")


# ═══════════════════════════════════════════════════════════════════
# 7) 정상 경로 무변화
# ═══════════════════════════════════════════════════════════════════
key = f"2099-07-31_{W}"
seed_pending("e9", key)
mail = Mail()
rc, _ = run_main(W, mail, **gcs_env("e9", "2099-07-31", W))
chk("OK-01: Cloud Run 경로 정상 → exit 0 · SMTP 1 · 오류 메일 0 · completed",
    rc == 0 and len(mail.reports) == 1 and mail.errors == [] and state_of("e9", key) == "completed")
mail = Mail()
rc, _ = run_main(W, mail, REPORT_DATE="2099-07-31")
chk("OK-02: GitHub/local 경로 정상 → exit 0 · SMTP 1 · 오류 메일 0",
    rc == 0 and len(mail.reports) == 1 and mail.errors == [])
mail = Mail()
rc, _ = run_main(W, mail, DRY_RUN="1", **gcs_env("e10", "2099-08-07", W))
chk("OK-03: DRY_RUN → exit 0 · SMTP 0 · 오류 메일 0 · GCS 기록 0",
    rc == 0 and mail.reports == [] and mail.errors == [] and _buckets.get("e10", FakeBucket()).keys() == [])


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" send_report.main() — execution state 장애 / 기존 오류 메일 정책 분리 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
