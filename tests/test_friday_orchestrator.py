# -*- coding: utf-8 -*-
"""scripts/friday_orchestrator.py — lock · 상태 머신 · Weekly→Standard 순차 실행 회귀.

지키는 원칙:
  · 같은 날짜 execution 이 겹치면 lock 을 얻은 하나만 report/send 경로를 실행.
  · status 없음/pending → 실행, sending/sent → ambiguous (자동 재발송 금지, exit 3), completed/marker → skip.
  · stale lock 은 status 가 sending/sent 면 회수하지 않음.
  · Weekly 가 완료(또는 이미 완료)된 경우에만 Standard 판정. KST ISO 홀수 주 또는 FORCE_BIWEEKLY_RUN.
  · TEST_MODE 이면 test namespace 만 사용 (prod lock/status/marker/sent 미생성).

GCS·send_report subprocess·SMTP 는 전부 fake. 외부 API·메일 없음.

실행:
  python tests/test_friday_orchestrator.py
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))

_ENV_KEYS = ("SENT_HISTORY_BACKEND", "SENT_HISTORY_GCS_BUCKET", "STATE_NAMESPACE", "TEST_MODE",
             "EXECUTION_STATE_KEY", "EXECUTION_RUN_ID", "FORCE_BIWEEKLY_RUN", "DRY_RUN",
             "REPORT_DATE", "REISSUE", "NOTICE", "CLOUD_RUN_EXECUTION")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import send_report
import friday_orchestrator as orch
from fake_gcs import FakeBucket, FakeGcsError

send_report._load_dotenv_if_present = lambda: None   # 로컬 .env 가 테스트에 섞이지 않도록

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def set_env(**kv):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in kv.items():
        os.environ[k] = v


KST = timezone(timedelta(hours=9))
ODD_FRI = datetime(2026, 11, 6, 8, 20, tzinfo=KST)    # W45
EVEN_FRI = datetime(2026, 10, 30, 8, 20, tzinfo=KST)  # W44
W = orch.MODE_WEEKLY
S = orch.MODE_STANDARD


def fresh(ns="prod"):
    b = FakeBucket(f"bk-{ns}")
    return b, storage.GcsStore(b, ns)


class Runner:
    """send_report.py subprocess 대역 — 시나리오대로 SMTP lifecycle 상태를 기록."""
    def __init__(self, store, scenarios=None, on_call=None):
        self.store = store
        self.scenarios = scenarios or {}
        self.on_call = on_call
        self.calls = []
        self.state_at_start = []
        self.lock_at_start = []

    def __call__(self, mode, extra_env):
        key, run_id = extra_env["EXECUTION_STATE_KEY"], extra_env["EXECUTION_RUN_ID"]
        self.calls.append(mode)
        st = self.store.read_status(key)
        self.state_at_start.append(st and st["state"])
        self.lock_at_start.append(self.store.read_lock(key[:10]))
        if self.on_call:
            self.on_call(mode, key)
        sc = self.scenarios.get(mode, "success")
        w = lambda s: self.store.write_status(key, s, run_id=run_id)
        if sc == "raise":
            raise RuntimeError("runner crashed")
        if sc == "fail_pending":
            return 1
        if sc == "rc0_no_state":
            return 0
        w("sending")
        if sc in ("crash_sending", "timeout_sending"):
            return 124 if sc == "timeout_sending" else 1
        w("sent")
        if sc == "crash_sent":
            return 1
        self.store.create_marker(key, {"key": key, "run_id": run_id})
        w("completed")
        return 1 if sc == "marker_then_rc1" else 0


def run(store, now, runner):
    return orch.main(now=now, runner=runner, store=store)


def state(store, key):
    st = store.read_status(key)
    return st and st["state"]


# ═══════════════════════════════════════════════════════════════════
# 1) 정상 흐름 · Weekly → Standard 순서
# ═══════════════════════════════════════════════════════════════════
set_env()
b, st = fresh()
r = Runner(st)
rc = run(st, ODD_FRI, r)
kw, ks = "2026-11-06_weekly-bundle", "2026-11-06_friday"
chk("FLOW-01: 홀수 주 → Weekly → Standard 순차 실행, exit 0", rc == 0 and r.calls == [W, S], f"rc={rc} calls={r.calls}")
chk("FLOW-02: runner 시작 시점 status = pending (orchestrator 가 SMTP 전 기록)",
    r.state_at_start == ["pending", "pending"])
chk("FLOW-03: none→pending→sending→sent→completed 후 marker + status completed",
    st.marker_exists(kw) and st.marker_exists(ks) and state(st, kw) == "completed" and state(st, ks) == "completed")
chk("FLOW-04: 정상 종료 후 lock 해제", st.read_lock("2026-11-06") is None)
lk = r.lock_at_start[0]
chk("FLOW-05: 실행 중 lock payload (run_id · ttl_sec=12h · expected_completion_by=start+12h)",
    lk is not None and lk[0]["ttl_sec"] == 12 * 3600
    and datetime.fromisoformat(lk[0]["expected_completion_by"]) - datetime.fromisoformat(lk[0]["start_ts"])
    == timedelta(hours=12) and lk[0]["run_id"].startswith("local-"))
chk("FLOW-06: 모든 객체가 prod/ 아래", all(k.startswith("prod/") for k in b.keys()))

b, st = fresh()
r = Runner(st)
rc = run(st, EVEN_FRI, r)
chk("FLOW-07: 짝수 주 → Weekly 만, exit 0", rc == 0 and r.calls == [W])
chk("FLOW-08: 짝수 주 Standard status/marker 없음", state(st, "2026-10-30_friday") is None
    and not st.marker_exists("2026-10-30_friday"))

set_env(FORCE_BIWEEKLY_RUN="1")
b, st = fresh()
r = Runner(st)
chk("FLOW-09: 짝수 주 + FORCE_BIWEEKLY_RUN=1 → Standard 실행", run(st, EVEN_FRI, r) == 0 and r.calls == [W, S])
set_env(FORCE_BIWEEKLY_RUN="0")
b, st = fresh()
r = Runner(st)
chk("FLOW-10: FORCE_BIWEEKLY_RUN=0 → 짝수 주 skip", run(st, EVEN_FRI, r) == 0 and r.calls == [W])
set_env()


# ═══════════════════════════════════════════════════════════════════
# 2) Weekly 실패 → Standard 실행 금지
# ═══════════════════════════════════════════════════════════════════
b, st = fresh()
r = Runner(st, {W: "fail_pending"})
rc = run(st, ODD_FRI, r)
chk("WFAIL-01: Weekly pending 실패 → exit 1, Standard 미실행", rc == 1 and r.calls == [W])
chk("WFAIL-02: Weekly status = pending (재실행 가능) · lock 해제", state(st, kw) == "pending"
    and st.read_lock("2026-11-06") is None)
r2 = Runner(st)
rc = run(st, ODD_FRI, r2)
chk("WFAIL-03: 같은 날 재실행 → Weekly 다시 실행 후 Standard (안전한 retry)", rc == 0 and r2.calls == [W, S])

b, st = fresh()
r = Runner(st, {W: "crash_sending"})
chk("WFAIL-04: Weekly ambiguous → exit 3, Standard 미실행", run(st, ODD_FRI, r) == 3 and r.calls == [W])


# ═══════════════════════════════════════════════════════════════════
# 3) 상태 머신 — ambiguous / completed / marker
# ═══════════════════════════════════════════════════════════════════
for sc, expect_state in (("crash_sending", "sending"), ("timeout_sending", "sending"), ("crash_sent", "sent")):
    b, st = fresh()
    r = Runner(st, {S: sc})
    rc = run(st, ODD_FRI, r)
    chk(f"SM-{sc}-01: Standard 가 {expect_state} 에서 중단 → exit 3", rc == 3 and state(st, ks) == expect_state)
    r2 = Runner(st)
    rc = run(st, ODD_FRI, r2)
    chk(f"SM-{sc}-02: 재실행 → 자동 재발송 없음 (runner 0회), exit 3", rc == 3 and r2.calls == [])
    chk(f"SM-{sc}-03: ambiguous 후에도 lock 은 해제 (재발송 방지는 status 가 담당)",
        st.read_lock("2026-11-06") is None)

b, st = fresh()
run(st, ODD_FRI, Runner(st))
r = Runner(st)
chk("SM-COMPLETED: 둘 다 completed 후 재실행 → runner 0회, exit 0", run(st, ODD_FRI, r) == 0 and r.calls == [])

b, st = fresh()
st.create_marker(kw, {"key": kw})
r = Runner(st)
chk("SM-MARKER-ONLY: Weekly marker 만 존재 → Weekly skip, Standard 진행",
    run(st, ODD_FRI, r) == 0 and r.calls == [S])

b, st = fresh()
st.create_marker(ks, {"key": ks})
r = Runner(st)
chk("SM-STD-MARKER: Standard marker 존재 → Standard 재발송 없음", run(st, ODD_FRI, r) == 0 and r.calls == [W])

b, st = fresh()
st.write_status(kw, "completed", run_id="old")
r = Runner(st)
chk("SM-STATUS-COMPLETED: status completed (marker 없음) → skip", run(st, ODD_FRI, r) == 0 and r.calls == [S])

b, st = fresh()
r = Runner(st, {W: "rc0_no_state"})
chk("SM-RC0-NOSTATE: rc=0 인데 completed 기록 없음 → ambiguous, exit 3, Standard 미실행",
    run(st, ODD_FRI, r) == 3 and r.calls == [W])

b, st = fresh()
r = Runner(st, {W: "marker_then_rc1"})
chk("SM-MARKER-RC1: 발송·기록 완료 후 rc=1 → completed 로 보고 Standard 진행",
    run(st, ODD_FRI, r) == 0 and r.calls == [W, S])

b, st = fresh()
b.put(st.status_key(kw), "{broken")
r = Runner(st)
chk("SM-CORRUPT-STATUS: status JSON 깨짐 → ambiguous, 실행 안 함", run(st, ODD_FRI, r) == 3 and r.calls == [])

set_env(DRY_RUN="1")
b, st = fresh()
r = Runner(st, {W: "rc0_no_state", S: "rc0_no_state"})
rc = run(st, ODD_FRI, r)
chk("SM-DRYRUN: DRY_RUN rc=0 + pending → 정상 처리, Standard 진행, marker 없음",
    rc == 0 and r.calls == [W, S] and not st.marker_exists(kw) and not st.marker_exists(ks))
set_env()


# ═══════════════════════════════════════════════════════════════════
# 4) lock — 중복 / stale / 깨진 payload / 자기 요청 재시도
# ═══════════════════════════════════════════════════════════════════
def other_lock(now, age):
    start = now - age
    return {"run_id": "other-run", "start_ts": start.astimezone(timezone.utc).isoformat(),
            "ttl_sec": orch.LOCK_TTL_SEC,
            "expected_completion_by": (start + timedelta(seconds=orch.LOCK_TTL_SEC)).astimezone(timezone.utc).isoformat()}

b, st = fresh()
st.try_create_lock("2026-11-06", other_lock(ODD_FRI, timedelta(minutes=5)))
before = st.read_lock("2026-11-06")
r = Runner(st)
rc = run(st, ODD_FRI, r)
chk("LOCK-DUP-01: 살아 있는 lock → duplicate, exit 0, runner 0회", rc == 0 and r.calls == [])
chk("LOCK-DUP-02: 남의 lock 은 건드리지 않음", st.read_lock("2026-11-06") == before)
chk("LOCK-DUP-03: status/marker 미생성", b.keys("prod/status/") == [] and b.keys("prod/markers/") == [])

for label, seed in (("none", None), ("pending", "pending")):
    b, st = fresh()
    st.try_create_lock("2026-11-06", other_lock(ODD_FRI, timedelta(hours=13)))
    if seed:
        st.write_status(kw, seed, run_id="other-run")
    r = Runner(st)
    rc = run(st, ODD_FRI, r)
    chk(f"LOCK-STALE-{label}: stale lock + status {label} → 회수 후 실행", rc == 0 and r.calls == [W, S]
        and r.lock_at_start[0][0]["run_id"] != "other-run")
    chk(f"LOCK-STALE-{label}-REL: 회수한 lock 도 종료 시 해제", st.read_lock("2026-11-06") is None)

for mode_key, seed in ((kw, "sending"), (kw, "sent"), (ks, "sending"), (ks, "sent")):
    b, st = fresh()
    st.try_create_lock("2026-11-06", other_lock(ODD_FRI, timedelta(hours=13)))
    before = st.read_lock("2026-11-06")
    if mode_key == ks:
        st.create_marker(kw, {"key": kw})
    st.write_status(mode_key, seed, run_id="other-run")
    r = Runner(st)
    rc = run(st, ODD_FRI, r)
    chk(f"LOCK-STALE-{mode_key[11:]}-{seed}: stale lock + {seed} → exit 3, 자동 발송 없음, lock 회수 안 함",
        rc == 3 and r.calls == [] and st.read_lock("2026-11-06") == before)

b, st = fresh()
st.try_create_lock("2026-11-06", other_lock(ODD_FRI, timedelta(hours=11, minutes=59)))
r = Runner(st)
chk("LOCK-TTL-EDGE: 11h59m 된 lock 은 아직 살아 있음 → duplicate", run(st, ODD_FRI, r) == 0 and r.calls == [])

b, st = fresh()
b.put(st.lock_key("2026-11-06"), "{corrupt")
r = Runner(st)
chk("LOCK-CORRUPT: 깨진 lock payload → exit 3, 실행 안 함", run(st, ODD_FRI, r) == 3 and r.calls == [])
b, st = fresh()
b.put(st.lock_key("2026-11-06"), json.dumps({"run_id": "x"}))
r = Runner(st)
chk("LOCK-NO-DEADLINE: expected_completion_by 없는 lock → exit 3", run(st, ODD_FRI, r) == 3 and r.calls == [])

b, st = fresh()
b.faults.append(("upload", "prod/locks/2026-11-06.lock", "after", FakeGcsError(412, "retried own create")))
r = Runner(st)
chk("LOCK-OWN-RETRY: 자기 create 가 재시도되어 412 → run_id 일치로 획득 판단 후 실행",
    run(st, ODD_FRI, r) == 0 and r.calls == [W, S] and st.read_lock("2026-11-06") is None)

b, st = fresh()
b.faults.append(("upload", "prod/locks/2026-11-06.lock", "before", FakeGcsError(503, "unavailable")))
r = Runner(st)
raised = False
try:
    run(st, ODD_FRI, r)
except FakeGcsError:
    raised = True
chk("LOCK-503: lock 생성 503 은 duplicate 로 오인하지 않고 오류 전파, 실행 안 함", raised and r.calls == [])

b, st = fresh()
b.faults.append(("upload", "prod/locks/2026-11-06.lock", "after", FakeGcsError(503, "response lost")))
r = Runner(st)
raised = False
try:
    run(st, ODD_FRI, r)
except FakeGcsError:
    raised = True
chk("LOCK-503-AFTER: 서버 생성 후 응답 503 → 오류 전파 · 실행 안 함 · 내 run_id lock 정리",
    raised and r.calls == [] and st.read_lock("2026-11-06") is None)
r2 = Runner(st)
chk("LOCK-503-AFTER-RETRY: 같은 날 수동 재실행이 막히지 않음",
    run(st, ODD_FRI, r2) == 0 and r2.calls == [W, S])

b, st = fresh()
st.try_create_lock("2026-11-06", {"run_id": "other-run", "start_ts": "x", "ttl_sec": 1,
                                  "expected_completion_by": "2099-01-01T00:00:00+00:00"})
before = st.read_lock("2026-11-06")
_orig_read = st.read_lock
st.read_lock = lambda d: (_ for _ in ()).throw(FakeGcsError(503, "read failed"))
r = Runner(st)
raised = False
try:
    run(st, ODD_FRI, r)
except FakeGcsError:
    raised = True
st.read_lock = _orig_read
chk("LOCK-READ-FAIL: 획득 중 오류 시에도 남의 lock 은 삭제하지 않음",
    raised and r.calls == [] and st.read_lock("2026-11-06") == before)

b, st = fresh()
r = Runner(st, {W: "raise"})
raised = False
try:
    run(st, ODD_FRI, r)
except RuntimeError:
    raised = True
chk("LOCK-RELEASE-ON-EXC: runner 예외 → 예외 전파 + lock 해제", raised and st.read_lock("2026-11-06") is None)
chk("LOCK-RELEASE-ON-EXC-STATE: 예외 시 status pending 유지 (재실행 가능)", state(st, kw) == "pending")

b, st = fresh()
b.faults.append(("delete", "prod/locks/2026-11-06.lock", "before", FakeGcsError(503, "unavailable")))
r = Runner(st, {S: "crash_sending"})
rc = run(st, ODD_FRI, r)
chk("LOCK-RELEASE-FAIL: lock 해제 503 이 ambiguous exit 3 을 덮지 않음 (lock 은 stale 회수 대상으로 남음)",
    rc == 3 and st.read_lock("2026-11-06") is not None)


# ═══════════════════════════════════════════════════════════════════
# 5) 동시 실행 — report/send 경로는 정확히 한 번
# ═══════════════════════════════════════════════════════════════════
b, st = fresh()
nested = {}
def _second_execution(mode, key):
    if mode == W and "rc" not in nested:
        nested["rc"] = run(st, ODD_FRI, Runner(st))
r = Runner(st, on_call=_second_execution)
rc = run(st, ODD_FRI, r)
chk("CONC-NESTED-01: 첫 execution 진행 중 두 번째 execution → duplicate exit 0",
    nested.get("rc") == 0 and rc == 0)
chk("CONC-NESTED-02: Weekly/Standard 각 1회만 실행", r.calls == [W, S])

b, st = fresh()
inside, second_done = threading.Event(), threading.Event()
calls, results = [], {}
def _blocking_runner(mode, extra_env):
    calls.append(mode)
    if mode == W:
        inside.set()
        second_done.wait(5)
    return Runner(st)(mode, extra_env)
def _exec(name):
    if name == "B":
        inside.wait(5)
    results[name] = run(st, ODD_FRI, _blocking_runner)
    if name == "B":
        second_done.set()
ta, tb = threading.Thread(target=_exec, args=("A",)), threading.Thread(target=_exec, args=("B",))
ta.start(); tb.start(); ta.join(10); tb.join(10)
chk("CONC-THREAD-01: 두 스레드 동시 실행 → 둘 다 exit 0", results == {"A": 0, "B": 0}, f"{results}")
chk("CONC-THREAD-02: Weekly 1회 · Standard 1회", calls == [W, S], f"{calls}")

b, st = fresh()
barrier = threading.Barrier(12)
outcomes = []
def _acq(i):
    barrier.wait()
    outcomes.append(orch.acquire_lock(st, "2026-11-13", f"run-{i}", ODD_FRI))
ths = [threading.Thread(target=_acq, args=(i,)) for i in range(12)]
[t.start() for t in ths]; [t.join() for t in ths]
chk("CONC-RACE: 12 execution 동시 lock 시도 → 정확히 1개 acquired, 나머지 duplicate",
    outcomes.count(orch.ACQUIRED) == 1 and outcomes.count(orch.DUPLICATE) == 11, f"{outcomes}")


# ═══════════════════════════════════════════════════════════════════
# 6) KST / ISO 주차 경계 · date_slug
# ═══════════════════════════════════════════════════════════════════
b, st = fresh()
r = Runner(st)
run(st, datetime(2026, 11, 5, 23, 20, tzinfo=timezone.utc), r)
chk("KST-01: 목 23:20 UTC → KST 금 2026-11-06 slot · W45 → Standard",
    r.calls == [W, S] and st.marker_exists("2026-11-06_friday"))
b, st = fresh()
r = Runner(st)
run(st, datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc), r)
chk("KST-02: 일 15:30 UTC → KST 월 2026-11-09 · W46 → Standard skip",
    r.calls == [W] and st.marker_exists("2026-11-09_weekly-bundle"))
b, st = fresh()
r = Runner(st)
run(st, datetime(2027, 1, 1, 8, 20, tzinfo=KST), r)
chk("ISO-01: 2027-01-01 (ISO 2026-W53, 홀수) → Standard", r.calls == [W, S])
b, st = fresh()
r = Runner(st)
run(st, datetime(2026, 12, 25, 8, 20, tzinfo=KST), r)
chk("ISO-02: 2026-12-25 (W52) → Standard skip", r.calls == [W])
set_env(REPORT_DATE="2026-07-17")
b, st = fresh()
r = Runner(st)
run(st, EVEN_FRI, r)
chk("DATE-OVERRIDE: REPORT_DATE → state key/lock 은 slot 날짜, 주차 판정은 실제 실행일",
    st.marker_exists("2026-07-17_weekly-bundle") and r.calls == [W])
set_env()
raised = False
try:
    orch.main(now=datetime(2026, 11, 6, 8, 20), runner=Runner(fresh()[1]), store=fresh()[1])
except ValueError:
    raised = True
chk("NAIVE: timezone 없는 now → ValueError", raised)
r = Runner(fresh()[1])
chk("STATE-KEY: runner 에 EXECUTION_STATE_KEY = {date}_{mode} 전달",
    orch.state_key("2026-11-06", W) == "2026-11-06_weekly-bundle"
    and orch.state_key("2026-11-06", S) == "2026-11-06_friday")


# ═══════════════════════════════════════════════════════════════════
# 7) prod / test 격리 (get_state_store 경유)
# ═══════════════════════════════════════════════════════════════════
shared = FakeBucket("shared")
_orig_factory = storage._make_gcs_bucket
storage._make_gcs_bucket = lambda name: shared

set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="shared", STATE_NAMESPACE="prod")
prod_store = storage.get_state_store()
prod_store.create_marker(kw, {"key": kw})
prod_store.create_marker(ks, {"key": ks})
prod_snapshot = {k: shared.objects[k]["generation"] for k in shared.keys("prod/")}

set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="shared", STATE_NAMESPACE="prod", TEST_MODE="1")
test_runner_store = storage.get_state_store()
r = Runner(test_runner_store)
rc = orch.main(now=ODD_FRI, runner=r)
chk("ISO-NS-01: TEST_MODE + STATE_NAMESPACE=prod → test namespace 로 실행 (prod marker 에 막히지 않음)",
    rc == 0 and r.calls == [W, S] and test_runner_store.namespace == "test")
chk("ISO-NS-02: prod/ 객체 무변화 (lock/status/marker 생성·수정 없음)",
    {k: shared.objects[k]["generation"] for k in shared.keys("prod/")} == prod_snapshot)
chk("ISO-NS-03: test/markers 에만 완료 기록",
    shared.keys("test/markers/") == sorted([f"test/markers/{kw}.completed", f"test/markers/{ks}.completed"]))

set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="shared", STATE_NAMESPACE="prod")
test_snapshot = {k: shared.objects[k]["generation"] for k in shared.keys("test/")}
r = Runner(storage.get_state_store())
rc = orch.main(now=datetime(2026, 11, 20, 8, 20, tzinfo=KST), runner=r)
chk("ISO-NS-04: prod 실행은 test/ 를 건드리지 않음",
    rc == 0 and {k: shared.objects[k]["generation"] for k in shared.keys("test/")} == test_snapshot)

set_env()
r = Runner(fresh()[1])
chk("CONFIG-01: backend=local 로 orchestrator 실행 → exit 1, runner 0회",
    orch.main(now=ODD_FRI, runner=r) == 1 and r.calls == [])
set_env(SENT_HISTORY_BACKEND="gcs", STATE_NAMESPACE="prod")
chk("CONFIG-02: bucket 미설정 → exit 1, runner 0회", orch.main(now=ODD_FRI, runner=r) == 1 and r.calls == [])
storage._make_gcs_bucket = _orig_factory
set_env()


# ═══════════════════════════════════════════════════════════════════
# 8) end-to-end — 실제 send_report.run_weekly_bundle / run_friday 를 in-process 실행
# ═══════════════════════════════════════════════════════════════════
e2e = FakeBucket("e2e")
storage._make_gcs_bucket = lambda name: e2e
_TMP = Path(tempfile.mkdtemp(prefix="orch_e2e_"))
send_report.ROOT = _TMP
send_report.LOCAL_OUTPUT_DIR = _TMP / "local_output"
send_report.LOCAL_OUTPUT_DIR.mkdir()
FRAG = _TMP / "frag"
FRAG.mkdir()
send_report._env_snapshot = lambda: {"GMAIL_ADDRESS": "bot@x.com", "GMAIL_APP_PASSWORD": "p",
                                     "RECIPIENT_EMAIL": "user@x.com"}
send_report._run_target_weekly = lambda d, t, slug: {"html": f"<html>{slug}</html>",
                                                      "video_ids": [f"{slug}-1"], "raw_path": Path()}
def _fake_run_child(script, extra_env, timeout=1200):
    frag = Path(extra_env["REPORT_FRAGMENT_PATH"])
    frag.write_text("<html>std</html>", encoding="utf-8")
    raw = send_report.ROOT / "benchmark" / f"{frag.name[:10]}_standard_filtered_raw.json"
    raw.parent.mkdir(exist_ok=True)
    raw.write_text(json.dumps({"stages": {"direct_final": [{"video_id": "s1"}], "benchmark_final": []}}),
                   encoding="utf-8")
    return 0
send_report._run_child = _fake_run_child

smtp_log = []
smtp_fail = set()
def _smtp(env, **kw):
    key = os.environ.get("EXECUTION_STATE_KEY")
    st_ = json.loads(e2e.text(f"prod/status/{key}.json"))["state"] if key else None
    smtp_log.append((kw["subject"], st_))
    if any(s in kw["subject"] for s in smtp_fail):
        raise OSError("SMTP 421")
send_report.send_email = _smtp

def inproc_runner(mode, extra_env):
    saved = {k: os.environ.get(k) for k in extra_env}
    os.environ.update(extra_env)
    try:
        date = extra_env["EXECUTION_STATE_KEY"][:10]
        if mode == W:
            send_report.run_weekly_bundle(date, FRAG, dry_run=False)
        else:
            send_report.run_friday(date, FRAG, dry_run=False)
        return 0
    except Exception as e:
        print(f"  (in-process send_report 실패: {e})")
        return 1
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="e2e", STATE_NAMESPACE="prod")
rc = orch.main(now=ODD_FRI, runner=inproc_runner)
chk("E2E-01: exit 0 · SMTP 2회 (Weekly(털어드림·묘한덕질) → Standard 순서)",
    rc == 0 and [s for s, _ in smtp_log] == [
        "[털어드림/묘한덕질 Weekly] 2026-11-06 · 1건 / 1건",
        "[털어드림 격주 후보] 2026-11-06 · Benchmark 1건"], f"{smtp_log}")
chk("E2E-02: 각 SMTP 호출 순간 status = sending", [s for _, s in smtp_log] == ["sending", "sending"])
chk("E2E-03: prod/sent 에 Weekly 2 + Standard 1 기록 (jjalduk 없음)",
    e2e.keys("prod/sent/") == ["prod/sent/myohanduk/2026-11-06_weekly.json",
                               "prod/sent/teoldeurim/2026-11-06_standard.json",
                               "prod/sent/teoldeurim/2026-11-06_weekly.json"])
chk("E2E-04: marker 2개 · lock 해제",
    len(e2e.keys("prod/markers/")) == 2 and e2e.keys("prod/locks/") == [])
smtp_log.clear()
rc = orch.main(now=ODD_FRI + timedelta(minutes=30), runner=inproc_runner)
chk("E2E-05: 같은 날 Scheduler 재호출 → SMTP 0회, exit 0", rc == 0 and smtp_log == [])

smtp_log.clear()
smtp_fail.add("격주 후보")
NEXT_ODD = datetime(2026, 11, 20, 8, 20, tzinfo=KST)   # W47
rc = orch.main(now=NEXT_ODD, runner=inproc_runner)
chk("E2E-06: Standard SMTP 실패 → Weekly 는 완료, exit 3 (Standard status=sending)",
    rc == 3 and len(smtp_log) == 2
    and json.loads(e2e.text("prod/status/2026-11-20_friday.json"))["state"] == "sending"
    and e2e.keys("prod/markers/2026-11-20") == ["prod/markers/2026-11-20_weekly-bundle.completed"])
chk("E2E-07: 실패한 Standard 의 sent history 없음",
    "prod/sent/teoldeurim/2026-11-20_standard.json" not in e2e.objects)
smtp_fail.clear()
smtp_log.clear()
rc = orch.main(now=NEXT_ODD + timedelta(hours=1), runner=inproc_runner)
chk("E2E-08: 재실행 → ambiguous 유지, SMTP 0회 (자동 재발송 없음)", rc == 3 and smtp_log == [])

storage._make_gcs_bucket = _orig_factory
set_env()

# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" friday orchestrator — lock · 상태 머신 · Weekly→Standard 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
