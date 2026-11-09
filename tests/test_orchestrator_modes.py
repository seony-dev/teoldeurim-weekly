# -*- coding: utf-8 -*-
"""friday_orchestrator 실행 mode (auto / weekly-bundle / friday) 회귀.

지키는 원칙:
  · 인자 없음 = auto = 기존 흐름 그대로 (Weekly → KST ISO 홀수 주 Standard, Weekly 실패 시 Standard 금지).
  · weekly-bundle: 홀수 주에도 Weekly 만. Standard 판정·실행 없음.
  · friday: Standard 만. Weekly 선행 조건·격주 판정 없음 (수동 Standard 실행 의미 보존).
  · 모든 mode 가 같은 date lock · {date}_{mode} status/marker · ambiguous 판정 · TEST_MODE namespace 를 사용.

GCS·send_report subprocess 는 fake. 외부 API·메일 없음.

실행:
  python tests/test_orchestrator_modes.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))

_ENV_KEYS = ("SENT_HISTORY_BACKEND", "SENT_HISTORY_GCS_BUCKET", "STATE_NAMESPACE", "TEST_MODE",
             "EXECUTION_STATE_KEY", "EXECUTION_RUN_ID", "FORCE_BIWEEKLY_RUN", "DRY_RUN", "REPORT_DATE")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import send_report
import friday_orchestrator as orch
from fake_gcs import FakeBucket

send_report._load_dotenv_if_present = lambda: None

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def set_env(**kv):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(kv)


KST = timezone(timedelta(hours=9))
ODD = datetime(2026, 11, 6, 8, 20, tzinfo=KST)    # W45
EVEN = datetime(2026, 10, 30, 8, 20, tzinfo=KST)  # W44
W, S = orch.MODE_WEEKLY, orch.MODE_STANDARD


def fresh(ns="prod"):
    b = FakeBucket(f"bk-{ns}")
    return b, storage.GcsStore(b, ns)


class Runner:
    def __init__(self, store, scenarios=None, on_call=None):
        self.store, self.scenarios, self.on_call = store, scenarios or {}, on_call
        self.calls = []

    def __call__(self, mode, extra_env):
        key, run_id = extra_env["EXECUTION_STATE_KEY"], extra_env["EXECUTION_RUN_ID"]
        self.calls.append((mode, key))
        if self.on_call:
            self.on_call(mode, key)
        sc = self.scenarios.get(mode, "success")
        if sc == "fail_pending":
            return 1
        self.store.write_status(key, "sending", run_id=run_id)
        if sc == "crash_sending":
            return 1
        self.store.write_status(key, "sent", run_id=run_id)
        self.store.create_marker(key, {"key": key})
        self.store.write_status(key, "completed", run_id=run_id)
        return 0


def modes(r):
    return [m for m, _ in r.calls]


def state(store, key):
    st = store.read_status(key)
    return st and st["state"]


# ═══════════════════════════════════════════════════════════════════
# 0) CLI · 기본값
# ═══════════════════════════════════════════════════════════════════
chk("CLI-01: 인자 없음 → auto", orch.parse_args([]).mode == "auto")
chk("CLI-02: --mode weekly-bundle", orch.parse_args(["--mode", "weekly-bundle"]).mode == "weekly-bundle")
chk("CLI-03: --mode=friday", orch.parse_args(["--mode=friday"]).mode == "friday")
try:
    orch.parse_args(["--mode", "standard"])
    _bad = False
except SystemExit:
    _bad = True
chk("CLI-04: 알 수 없는 mode → 거부", _bad)
chk("CLI-05: main() 기본 mode 인자 = auto", orch.main.__defaults__[-1] == "auto")
try:
    orch.main(now=ODD, runner=Runner(fresh()[1]), store=fresh()[1], mode="all")
    _bad = False
except ValueError:
    _bad = True
chk("CLI-06: main(mode=알 수 없음) → ValueError", _bad)
_saved_argv = sys.argv
sys.argv = ["send_report.py", "--mode=weekly-bundle"]   # 다른 스크립트의 argv 가 남아 있어도
b, st = fresh()
r = Runner(st)
orch.main(now=ODD, runner=r, store=st)
sys.argv = _saved_argv
chk("CLI-07: main() 은 sys.argv 를 읽지 않음 (기본 auto 유지)", modes(r) == [W, S])


# ═══════════════════════════════════════════════════════════════════
# 1) auto — 기존 동작
# ═══════════════════════════════════════════════════════════════════
set_env()
b, st = fresh()
r = Runner(st)
chk("AUTO-01: 홀수 주 → Weekly → Standard", orch.main(now=ODD, runner=r, store=st) == 0 and modes(r) == [W, S])
b, st = fresh()
r = Runner(st)
chk("AUTO-02: 짝수 주 → Weekly 만", orch.main(now=EVEN, runner=r, store=st) == 0 and modes(r) == [W])
b, st = fresh()
r = Runner(st, {W: "fail_pending"})
chk("AUTO-03: Weekly 실패 → Standard 금지, exit 1", orch.main(now=ODD, runner=r, store=st) == 1 and modes(r) == [W])
b, st = fresh()
r = Runner(st)
chk("AUTO-04: mode='auto' 명시 = 인자 없음과 동일",
    orch.main(now=ODD, runner=r, store=st, mode="auto") == 0 and modes(r) == [W, S])
set_env(FORCE_BIWEEKLY_RUN="1")
b, st = fresh()
r = Runner(st)
chk("AUTO-05: 짝수 주 + FORCE_BIWEEKLY_RUN → Weekly → Standard",
    orch.main(now=EVEN, runner=r, store=st) == 0 and modes(r) == [W, S])
set_env()


# ═══════════════════════════════════════════════════════════════════
# 2) weekly-bundle
# ═══════════════════════════════════════════════════════════════════
b, st = fresh()
r = Runner(st)
rc = orch.main(now=ODD, runner=r, store=st, mode=W)
chk("WB-01: 홀수 주에도 Weekly 만, exit 0", rc == 0 and modes(r) == [W])
chk("WB-02: Standard status/marker 생성 0", state(st, "2026-11-06_friday") is None
    and not st.marker_exists("2026-11-06_friday"))
chk("WB-03: 기존 key {date}_weekly-bundle 로 completed", st.marker_exists("2026-11-06_weekly-bundle")
    and state(st, "2026-11-06_weekly-bundle") == "completed")
chk("WB-04: lock 해제", st.read_lock("2026-11-06") is None)
set_env(FORCE_BIWEEKLY_RUN="1")
b, st = fresh()
r = Runner(st)
chk("WB-05: FORCE_BIWEEKLY_RUN 이 있어도 Standard 없음",
    orch.main(now=EVEN, runner=r, store=st, mode=W) == 0 and modes(r) == [W])
set_env()
b, st = fresh()
st.create_marker("2026-11-06_weekly-bundle", {"key": "pre"})
r = Runner(st)
chk("WB-06: Weekly completed → skip (재발송 0), exit 0",
    orch.main(now=ODD, runner=r, store=st, mode=W) == 0 and r.calls == [])
b, st = fresh()
r = Runner(st, {W: "fail_pending"})
chk("WB-07: Weekly 실패 → exit 1", orch.main(now=ODD, runner=r, store=st, mode=W) == 1)
b, st = fresh()
r = Runner(st, {W: "crash_sending"})
chk("WB-08: Weekly ambiguous → exit 3", orch.main(now=ODD, runner=r, store=st, mode=W) == 3)
r2 = Runner(st)
chk("WB-09: ambiguous 후 재실행 → runner 0회, exit 3",
    orch.main(now=ODD, runner=r2, store=st, mode=W) == 3 and r2.calls == [])
b, st = fresh()
nested = {}
def _dup(mode, key):
    if "rc" not in nested:
        nested["rc"] = orch.main(now=ODD, runner=Runner(st), store=st, mode=W)
r = Runner(st, on_call=_dup)
rc = orch.main(now=ODD, runner=r, store=st, mode=W)
chk("WB-10: 실행 중 두 번째 execution → duplicate exit 0, Weekly 1회",
    rc == 0 and nested.get("rc") == 0 and modes(r) == [W])
b, st = fresh()
nested = {}
def _dup_auto(mode, key):
    if "rc" not in nested:
        r_auto = Runner(st)
        nested["rc"] = orch.main(now=ODD, runner=r_auto, store=st)
        nested["calls"] = r_auto.calls
r = Runner(st, on_call=_dup_auto)
orch.main(now=ODD, runner=r, store=st, mode=W)
chk("WB-11: weekly-bundle 실행 중 auto execution → 같은 date lock 으로 duplicate",
    nested.get("rc") == 0 and nested.get("calls") == [])


# ═══════════════════════════════════════════════════════════════════
# 3) friday (Standard 단독)
# ═══════════════════════════════════════════════════════════════════
b, st = fresh()
r = Runner(st)
rc = orch.main(now=ODD, runner=r, store=st, mode=S)
chk("FR-01: Standard 만, Weekly 0회, exit 0", rc == 0 and modes(r) == [S])
chk("FR-02: Weekly marker 없이도 실행 · Weekly status 미생성",
    state(st, "2026-11-06_weekly-bundle") is None and st.marker_exists("2026-11-06_friday"))
b, st = fresh()
r = Runner(st)
chk("FR-03: 짝수 주에도 실행 (격주 판정 없음 — 수동 Standard 의미)",
    orch.main(now=EVEN, runner=r, store=st, mode=S) == 0 and modes(r) == [S])
b, st = fresh()
st.create_marker("2026-11-06_friday", {"key": "pre"})
r = Runner(st)
chk("FR-04: Standard completed → skip, exit 0", orch.main(now=ODD, runner=r, store=st, mode=S) == 0 and r.calls == [])
b, st = fresh()
st.write_status("2026-11-06_friday", "sent", run_id="old")
r = Runner(st)
chk("FR-05: Standard ambiguous(sent) → runner 0회, exit 3",
    orch.main(now=ODD, runner=r, store=st, mode=S) == 3 and r.calls == [])
b, st = fresh()
st.write_status("2026-11-06_weekly-bundle", "sending", run_id="old")
r = Runner(st)
chk("FR-06: Weekly 가 ambiguous 여도 friday 모드는 Standard 실행 (Weekly 선행 조건 없음)",
    orch.main(now=ODD, runner=r, store=st, mode=S) == 0 and modes(r) == [S])
b, st = fresh()
r = Runner(st, {S: "fail_pending"})
chk("FR-07: Standard 실패 → exit 1 · status pending", orch.main(now=ODD, runner=r, store=st, mode=S) == 1
    and state(st, "2026-11-06_friday") == "pending")
set_env(REPORT_DATE="2026-07-17")
b, st = fresh()
r = Runner(st)
orch.main(now=EVEN, runner=r, store=st, mode=S)
chk("FR-08: REPORT_DATE → key 2026-07-17_friday", r.calls == [(S, "2026-07-17_friday")])
set_env()


# ═══════════════════════════════════════════════════════════════════
# 4) TEST_MODE namespace — 모든 mode 동일
# ═══════════════════════════════════════════════════════════════════
shared = FakeBucket("shared")
_orig = storage._make_gcs_bucket
storage._make_gcs_bucket = lambda name: shared
for m in ("auto", W, S):
    set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="shared", STATE_NAMESPACE="prod", TEST_MODE="1",
            REPORT_DATE={"auto": "2099-01-02", W: "2099-01-09", S: "2099-01-16"}[m])
    r = Runner(storage.get_state_store())
    rc = orch.main(now=ODD, runner=r, mode=m)
    chk(f"NS-{m}: TEST_MODE → test namespace 만 사용 (prod/ 0건)",
        rc == 0 and shared.keys("prod/") == [] and len(shared.keys("test/markers/")) > 0)
storage._make_gcs_bucket = _orig
set_env()


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" friday orchestrator 실행 mode (auto / weekly-bundle / friday) 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
