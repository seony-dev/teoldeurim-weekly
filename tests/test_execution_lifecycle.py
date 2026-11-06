# -*- coding: utf-8 -*-
"""send_report.py SMTP lifecycle 상태 기록 (EXECUTION_STATE_KEY hook) 회귀.

지키는 원칙:
  · EXECUTION_STATE_KEY 가 없으면 hook no-op — GitHub Actions 경로 동작·저장소 접근 무변화.
  · sending 은 send_email() 호출 직전, sent 는 성공 반환 직후, completed 는 sent history 기록 후.
  · completed 순서: sent history → marker(create-if-absent) → status completed.
  · SMTP 실패 → status=sending 유지 · sent history/marker 없음.
  · sent history 기록 실패 → status=sent 유지 · marker 없음.
  · sending 기록 실패 → send_email 호출 자체가 일어나지 않음.
  · TEST_MODE → test namespace 만 사용, sent history 기록 없음.
  · DRY_RUN → 상태 기록 없음.

실제 send_report.run_weekly_bundle / run_friday 를 실행하되 benchmark 실행·SMTP·GCS 는 fake.

실행:
  python tests/test_execution_lifecycle.py
"""
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
             "EXECUTION_STATE_KEY", "EXECUTION_RUN_ID", "REISSUE", "NOTICE", "REPORT_DATE")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import send_report
from fake_gcs import FakeBucket, FakeGcsError

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def clear_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


def set_env(**kv):
    clear_env()
    for k, v in kv.items():
        os.environ[k] = v


_buckets = {}
def _fake_factory(name):
    return _buckets.setdefault(name, FakeBucket(name))
storage._make_gcs_bucket = _fake_factory

_TMP = Path(tempfile.mkdtemp(prefix="lifecycle_"))
send_report.ROOT = _TMP
send_report.LOCAL_OUTPUT_DIR = _TMP / "local_output"
send_report.LOCAL_OUTPUT_DIR.mkdir()
FRAG = _TMP / "frag"
FRAG.mkdir()

send_report._env_snapshot = lambda: {"GMAIL_ADDRESS": "bot@x.com", "GMAIL_APP_PASSWORD": "p",
                                     "RECIPIENT_EMAIL": "user@x.com"}

_WEEKLY = {
    "teoldeurim": {"html": "<html>te</html>", "video_ids": ["t1", "t2"], "raw_path": Path()},
    "myohanduk":  {"html": "<html>mh</html>", "video_ids": ["m1"], "raw_path": Path()},
    "jjalduk":    {"html": "<html>jj</html>", "video_ids": ["j1", "j2", "j3"], "raw_path": Path()},
}
send_report._run_target_weekly = lambda date_slug, tmp_dir, slug: _WEEKLY[slug]


def _fake_run_child(script, extra_env, timeout=1200):
    Path(extra_env["REPORT_FRAGMENT_PATH"]).write_text("<html>std</html>", encoding="utf-8")
    date_slug = Path(extra_env["REPORT_FRAGMENT_PATH"]).name[:10]
    raw = send_report.ROOT / "benchmark" / f"{date_slug}_standard_filtered_raw.json"
    raw.parent.mkdir(exist_ok=True)
    raw.write_text(json.dumps({"stages": {"direct_final": [{"video_id": "s1"}],
                                          "benchmark_final": [{"video_id": "s2"}]}}), encoding="utf-8")
    return 0
send_report._run_child = _fake_run_child


class Smtp:
    """send_email spy — 호출 순간의 status state 를 events 에 남긴다."""
    def __init__(self, bucket=None, ns="prod", key=None, fail=False):
        self.bucket, self.ns, self.key, self.fail = bucket, ns, key, fail
        self.calls = []

    def __call__(self, env, **kw):
        state = None
        if self.bucket is not None and self.key:
            sk = f"{self.ns}/status/{self.key}.json"
            if sk in self.bucket.objects:
                state = json.loads(self.bucket.text(sk))["state"]
        self.calls.append({"subject": kw["subject"], "state_at_call": state})
        if self.bucket is not None and self.bucket.events is not None:
            self.bucket.events.append(("SMTP", state))
        if self.fail:
            raise OSError("SMTP connection reset")


def status_of(bucket, ns, key):
    k = f"{ns}/status/{key}.json"
    return json.loads(bucket.text(k)) if k in bucket.objects else {}


def state_of(bucket, ns, key):
    return status_of(bucket, ns, key).get("state")


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    return False


def raises_with_cause(fn, exc, cause):
    try:
        fn()
    except exc as e:
        return isinstance(e.__cause__, cause)
    except Exception:
        return False
    return False


def upload_index(events, needle):
    for i, e in enumerate(events):
        if e[0] == "upload" and needle in e[1]:
            return i
    return -1


# ═══════════════════════════════════════════════════════════════════
# A) EXECUTION_STATE_KEY 없음 → hook no-op (GitHub Actions 경로)
# ═══════════════════════════════════════════════════════════════════
clear_env()
_state_store_calls = []
_orig_get_state_store = storage.get_state_store
storage.get_state_store = lambda: _state_store_calls.append(1) or (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
smtp = Smtp()
send_report.send_email = smtp
send_report.run_weekly_bundle("2099-03-06", FRAG, dry_run=False)
storage.get_state_store = _orig_get_state_store
chk("NOKEY-01: send_email 1회 (기존 동작)", len(smtp.calls) == 1)
chk("NOKEY-02: 상태 저장소 접근 0회", _state_store_calls == [])
chk("NOKEY-03: sent history 는 기존처럼 local 에 3개 기록",
    all((_TMP / "benchmark/history/sent" / s / "2099-03-06_weekly.json").exists()
        for s in ("teoldeurim", "myohanduk", "jjalduk")))
chk("NOKEY-04: local 경로에서 google SDK 미로드", "google.cloud.storage" not in sys.modules)


# ═══════════════════════════════════════════════════════════════════
# B) Weekly Bundle 정상 — pending(orchestrator) → sending → SMTP → sent → history → marker → completed
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-03-13_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcB", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-B")
bB = _fake_factory("lcB")
bB.events = []
smtp = Smtp(bB, "prod", KEY)
send_report.send_email = smtp
send_report.run_weekly_bundle("2099-03-13", FRAG, dry_run=False)
ev = bB.events
i_sending = upload_index(ev, f"prod/status/{KEY}.json")
i_smtp = ev.index(("SMTP", "sending")) if ("SMTP", "sending") in ev else -1
i_sent_status = next((i for i, e in enumerate(ev) if i > i_smtp and e == ("upload", f"prod/status/{KEY}.json")), -1)
i_hist = [upload_index(ev, f"prod/sent/{s}/2099-03-13_weekly.json") for s in ("teoldeurim", "myohanduk", "jjalduk")]
i_marker = upload_index(ev, f"prod/markers/{KEY}.completed")
i_completed = max((i for i, e in enumerate(ev) if e == ("upload", f"prod/status/{KEY}.json")), default=-1)
chk("OK-01: send_email 호출 순간 status = sending", bool(smtp.calls) and smtp.calls[0]["state_at_call"] == "sending")
chk("OK-02: 순서 sending → SMTP → sent → history ×3 → marker → completed",
    -1 < i_sending < i_smtp < i_sent_status < min(i_hist) and max(i_hist) < i_marker < i_completed,
    f"events={ev}")
st = status_of(bB, "prod", KEY)
chk("OK-03: 최종 status completed · run_id 기록", st.get("state") == "completed" and st.get("run_id") == "RUN-B")
chk("OK-04: sent_history_plan 에 3 target video_ids",
    [(p["target_slug"], p["video_ids"]) for p in st["sent_history_plan"]]
    == [("teoldeurim", ["t1", "t2"]), ("myohanduk", ["m1"]), ("jjalduk", ["j1", "j2", "j3"])]
    if st.get("sent_history_plan") else False)
chk("OK-05: completed marker 존재", f"prod/markers/{KEY}.completed" in bB.objects)
chk("OK-06: sent history 3개 (payload video_ids 동일)",
    json.loads(bB.text("prod/sent/jjalduk/2099-03-13_weekly.json"))["candidates_video_ids"] == ["j1", "j2", "j3"])
chk("OK-07: test namespace 미사용", bB.keys("test/") == [])
chk("OK-08: 메일 내용 무변화 (subject)",
    smtp.calls[0]["subject"] == "[털어드림/묘한덕질/짤덕방 Weekly] 2099-03-13 · 2건 / 1건 / 3건")


# ═══════════════════════════════════════════════════════════════════
# C) SMTP 실패 → status=sending 유지, history/marker 없음
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-03-20_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcC", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-C")
bC = _fake_factory("lcC")
smtp = Smtp(bC, "prod", KEY, fail=True)
send_report.send_email = smtp
chk("SMTPFAIL-01: 예외 전파", raises(lambda: send_report.run_weekly_bundle("2099-03-20", FRAG, dry_run=False), OSError))
chk("SMTPFAIL-02: status = sending (ambiguous 로 남음)", state_of(bC, "prod", KEY) == "sending")
chk("SMTPFAIL-03: sent history 없음", bC.keys("prod/sent/") == [])
chk("SMTPFAIL-04: marker 없음", bC.keys("prod/markers/") == [])


# ═══════════════════════════════════════════════════════════════════
# D) SMTP 성공 후 sent history 기록 실패 → status=sent, marker 없음
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-03-27_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcD", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-D")
bD = _fake_factory("lcD")
bD.faults.append(("upload", "prod/sent/myohanduk/2099-03-27", "before", FakeGcsError(503, "unavailable")))
smtp = Smtp(bD, "prod", KEY)
send_report.send_email = smtp
chk("HISTFAIL-01: ExecutionPersistenceError 로 전파 (원인 GCS 503 보존)",
    raises_with_cause(lambda: send_report.run_weekly_bundle("2099-03-27", FRAG, dry_run=False),
                      send_report.ExecutionPersistenceError, FakeGcsError))
chk("HISTFAIL-02: SMTP 1회", len(smtp.calls) == 1)
chk("HISTFAIL-03: status = sent (자동 재발송 금지 상태)", state_of(bD, "prod", KEY) == "sent")
chk("HISTFAIL-04: sent 상태에도 sent_history_plan 보존 (수동 복구용)",
    len(status_of(bD, "prod", KEY).get("sent_history_plan") or []) == 3)
chk("HISTFAIL-05: marker 없음", bD.keys("prod/markers/") == [])


# ═══════════════════════════════════════════════════════════════════
# E) sending 기록 실패 → SMTP 호출 자체가 없음 (재실행 안전)
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-04-03_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcE", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-E")
bE = _fake_factory("lcE")
bE.faults.append(("upload", f"prod/status/{KEY}", "before", FakeGcsError(503, "unavailable")))
smtp = Smtp(bE, "prod", KEY)
send_report.send_email = smtp
chk("SENDINGFAIL-01: ExecutionStateError 로 전파 (원인 GCS 503 보존)",
    raises_with_cause(lambda: send_report.run_weekly_bundle("2099-04-03", FRAG, dry_run=False),
                      send_report.ExecutionStateError, FakeGcsError))
chk("SENDINGFAIL-02: send_email 호출 0회", smtp.calls == [])
chk("SENDINGFAIL-03: status 없음 (재실행 가능)", status_of(bE, "prod", KEY) == {})


# ═══════════════════════════════════════════════════════════════════
# F) marker 가 이미 있음 → create 는 False, 그래도 completed 기록 (idempotent)
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-04-10_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcF", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-F")
bF = _fake_factory("lcF")
bF.put(f"prod/markers/{KEY}.completed", '{"key": "pre"}')
send_report.send_email = Smtp(bF, "prod", KEY)
send_report.run_weekly_bundle("2099-04-10", FRAG, dry_run=False)
chk("MARKER-IDEMP-01: 기존 marker 보존 + status completed",
    json.loads(bF.text(f"prod/markers/{KEY}.completed"))["key"] == "pre"
    and state_of(bF, "prod", KEY) == "completed")


# ═══════════════════════════════════════════════════════════════════
# G) Standard (run_friday) 정상
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-04-17_friday"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcG", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-G")
bG = _fake_factory("lcG")
bG.events = []
smtp = Smtp(bG, "prod", KEY)
send_report.send_email = smtp
send_report.run_friday("2099-04-17", FRAG, dry_run=False)
ev = bG.events
i_smtp = ev.index(("SMTP", "sending")) if ("SMTP", "sending") in ev else -1
i_hist = upload_index(ev, "prod/sent/teoldeurim/2099-04-17_standard.json")
i_marker = upload_index(ev, f"prod/markers/{KEY}.completed")
st = status_of(bG, "prod", KEY)
chk("STD-01: send_email 호출 순간 status = sending", bool(smtp.calls) and smtp.calls[0]["state_at_call"] == "sending")
chk("STD-02: SMTP → history → marker 순서", -1 < i_smtp < i_hist < i_marker, f"events={ev}")
chk("STD-03: status completed · plan = standard/teoldeurim/[s1,s2]",
    st.get("state") == "completed"
    and st.get("sent_history_plan") == [{"profile": "standard", "date_slug": "2099-04-17",
                                     "target_slug": "teoldeurim", "video_ids": ["s1", "s2"]}])
chk("STD-04: subject 무변화", smtp.calls[0]["subject"] == "[털어드림 격주 후보] 2099-04-17 · Benchmark 2건")


# ═══════════════════════════════════════════════════════════════════
# H) TEST_MODE — STATE_NAMESPACE=prod 여도 test namespace, sent history 없음
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-04-24_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcH", STATE_NAMESPACE="prod", TEST_MODE="1",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-H")
bH = _fake_factory("lcH")
smtp = Smtp(bH, "test", KEY)
send_report.send_email = smtp
send_report.run_weekly_bundle("2099-04-24", FRAG, dry_run=False)
chk("TESTMODE-01: prod/ 객체 0개", bH.keys("prod/") == [])
chk("TESTMODE-02: test/status · test/markers 만 기록",
    bH.keys("test/") == [f"test/markers/{KEY}.completed", f"test/status/{KEY}.json"])
chk("TESTMODE-03: SMTP 직전 test status = sending", smtp.calls[0]["state_at_call"] == "sending")
chk("TESTMODE-04: subject [TEST] 접두어 유지", smtp.calls[0]["subject"].startswith("[TEST] "))


# ═══════════════════════════════════════════════════════════════════
# I) DRY_RUN — 상태 기록 없음 · SMTP 없음
# ═══════════════════════════════════════════════════════════════════
KEY = "2099-05-01_weekly-bundle"
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="lcI", STATE_NAMESPACE="prod",
        EXECUTION_STATE_KEY=KEY, EXECUTION_RUN_ID="RUN-I")
bI = _fake_factory("lcI")
smtp = Smtp(bI, "prod", KEY)
send_report.send_email = smtp
send_report.run_weekly_bundle("2099-05-01", FRAG, dry_run=True)
chk("DRYRUN-01: SMTP 0회 · GCS 기록 0개", smtp.calls == [] and bI.keys() == [])


# ═══════════════════════════════════════════════════════════════════
# J) KEY 는 있는데 backend=local → SMTP 전에 오류 (조용히 상태 없이 발송하지 않음)
# ═══════════════════════════════════════════════════════════════════
set_env(EXECUTION_STATE_KEY="2099-05-08_weekly-bundle", EXECUTION_RUN_ID="RUN-J")
smtp = Smtp()
send_report.send_email = smtp
chk("LOCALKEY-01: ExecutionStateError 로 중단 (원인 UnsupportedStorageBackend 보존)",
    raises_with_cause(lambda: send_report.run_weekly_bundle("2099-05-08", FRAG, dry_run=False),
                      send_report.ExecutionStateError, storage.UnsupportedStorageBackend))
chk("LOCALKEY-02: send_email 호출 0회", smtp.calls == [])

clear_env()

# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" send_report SMTP lifecycle 상태 기록 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
