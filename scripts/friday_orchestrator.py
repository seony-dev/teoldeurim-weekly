# -*- coding: utf-8 -*-
"""금요일 정기 실행 orchestrator (Cloud Run Job 진입점 예정).

흐름:
  1) KST date_slug 결정 (REPORT_DATE override 는 send_report 와 같은 규칙)
  2) GCS atomic execution lock 획득 — 다른 execution 이 쥐고 있으면 중복 실행으로 보고 정상 종료
  3) Weekly Bundle : send_report.py --mode=weekly-bundle
  4) Weekly 가 완료(또는 이미 완료)된 경우에만 KST ISO 홀수 주차 판정 (biweekly)
  5) Standard      : send_report.py --mode=friday
  6) lock 해제

mode 별 상태 (status/{date}_{mode}.json · markers/{date}_{mode}.completed):
  없음 / pending   → 실행 가능 (SMTP 시도 전)
  sending / sent   → ambiguous: 메일이 실제로 나갔는지 판단 불가 → 자동 재발송 금지
  completed/marker → 재실행 skip
pending 은 이 orchestrator 가, sending/sent/completed 는 send_report.py 가 실제 SMTP
호출 직전·직후에 기록한다 (EXECUTION_STATE_KEY 로 연결).

lock 은 동시 실행 방지용, status/marker 는 재발송 방지용이다.

exit code: 0 정상(중복 실행·skip 포함) · 1 실패(SMTP 이전, 재실행 가능) · 3 ambiguous(수동 확인)
"""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(ROOT / "config"))

import biweekly  # noqa: E402
import send_report  # noqa: E402
import storage  # noqa: E402

KST = biweekly.KST

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_AMBIGUOUS = 3

MODE_WEEKLY = "weekly-bundle"
MODE_STANDARD = "friday"
MODES = (MODE_WEEKLY, MODE_STANDARD)

# GitHub workflow timeout-minutes 와 동일 (weekly_bundle.yml 60분, weekly.yml 40분)
MODE_TIMEOUT_SEC = {MODE_WEEKLY: 60 * 60, MODE_STANDARD: 40 * 60}

# lock 생성 후 이 시간이 지나도 lock 이 남아 있으면 abandoned(stale) 로 본다.
# stale 이어도 status 가 sending/sent 면 회수하지 않는다.
# Cloud Run task timeout 은 반드시 이 값보다 짧아야 한다 (살아 있는 execution 의 lock 회수 방지).
LOCK_TTL_SEC = 12 * 60 * 60

# evaluate() 판정
RETRYABLE = "retryable"
AMBIGUOUS = "ambiguous"
COMPLETED = "completed"

# acquire_lock() 결과
ACQUIRED = "acquired"
DUPLICATE = "duplicate"

# run_mode() 결과 (COMPLETED / AMBIGUOUS 포함)
FAILED = "failed"
DRY_RUN_DONE = "dry_run"


def _log(msg):
    print(msg, flush=True)


def _err(msg):
    print(msg, file=sys.stderr, flush=True)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _truthy(value):
    return (value or "").strip().lower() in ("1", "true", "yes")


def state_key(date_slug, mode):
    return f"{date_slug}_{mode}"


def date_slug_for(now):
    override = (os.environ.get("REPORT_DATE") or "").strip()
    if override:
        return send_report._validate_report_date(override)
    return now.astimezone(KST).strftime("%Y-%m-%d")


def new_run_id(now):
    execution = (os.environ.get("CLOUD_RUN_EXECUTION") or "local").strip()
    return f"{execution}-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


# ============================================================================
# 상태 판정
# ============================================================================
def evaluate(store, key):
    """(판정, 상세) — COMPLETED / RETRYABLE / AMBIGUOUS."""
    if store.marker_exists(key):
        return COMPLETED, "completed marker"
    try:
        status = store.read_status(key)
    except ValueError:
        return AMBIGUOUS, "status JSON 해석 불가"
    state = (status or {}).get("state")
    if state is None or state == "pending":
        return RETRYABLE, state or "none"
    if state == "completed":
        return COMPLETED, "status completed (marker 없음)"
    return AMBIGUOUS, state


def report_ambiguous(store, key, detail):
    _err(f"\n⛔ AMBIGUOUS — {key} (state={detail})")
    _err("   SMTP 호출 이후 상태라 메일이 실제로 발송됐는지 프로그램만으로 판단할 수 없습니다.")
    _err("   자동 재발송하지 않습니다. 운영자 확인이 필요합니다.")
    _err(f"   status : {store.uri(store.status_key(key))}")
    _err(f"   marker : {store.uri(store.marker_key(key))} (없음)")
    _err("   1) Gmail 보낸편지함에서 해당 날짜 리포트 메일 확인")
    _err("   2-a) 발송됨  → status 의 sent_history_plan 대로 sent history 기록 후 marker 생성")
    _err("   2-b) 미발송  → status 객체 삭제 후 Job 재실행")


# ============================================================================
# execution lock
# ============================================================================
def lock_payload(run_id, now):
    return {
        "run_id": run_id,
        "start_ts": _iso(now),
        "ttl_sec": LOCK_TTL_SEC,
        "expected_completion_by": _iso(now + timedelta(seconds=LOCK_TTL_SEC)),
    }


def is_stale(lock, now):
    """expected_completion_by 를 지났으면 stale. 값이 없거나 깨졌으면 예외."""
    deadline = datetime.fromisoformat(lock["expected_completion_by"])
    if deadline.tzinfo is None:
        raise ValueError("expected_completion_by 에 timezone 정보 없음")
    return now >= deadline


def acquire_lock(store, date_slug, run_id, now):
    """ACQUIRED / DUPLICATE / AMBIGUOUS."""
    payload = lock_payload(run_id, now)
    lock_uri = store.uri(store.lock_key(date_slug))
    for _ in range(2):
        if store.try_create_lock(date_slug, payload):
            _log(f"🔒 lock 획득: {lock_uri} (run_id={run_id})")
            return ACQUIRED
        try:
            existing = store.read_lock(date_slug)
        except ValueError:
            _err(f"⛔ lock payload 해석 불가 — {lock_uri} 수동 확인 필요. 실행하지 않습니다.")
            return AMBIGUOUS
        if existing is not None:
            break
        # create 와 read 사이에 lock 이 해제됨 → 한 번 더 생성 시도
    else:
        _log(f"⏭️  lock 경합 — 다른 execution 이 진행 중으로 판단: {lock_uri}")
        return DUPLICATE

    lock, generation = existing
    if lock.get("run_id") == run_id:
        # 자신의 create 요청이 재시도되며 412 로 돌아온 경우
        _log(f"🔒 lock 획득 (재시도된 자기 요청 확인): {lock_uri}")
        return ACQUIRED
    try:
        stale = is_stale(lock, now)
    except (KeyError, TypeError, ValueError):
        _err(f"⛔ lock payload 해석 불가 — {lock_uri} 수동 확인 필요. 실행하지 않습니다.")
        return AMBIGUOUS
    if not stale:
        _log(f"⏭️  중복 실행 — 다른 execution 이 lock 보유 중 (run_id={lock.get('run_id')}, "
             f"start_ts={lock.get('start_ts')}). 아무 작업도 하지 않고 종료합니다.")
        return DUPLICATE

    _log(f"⚠️ stale lock 발견 (run_id={lock.get('run_id')}, "
         f"expected_completion_by={lock.get('expected_completion_by')}) — status 확인")
    for mode in MODES:
        key = state_key(date_slug, mode)
        verdict, detail = evaluate(store, key)
        if verdict == AMBIGUOUS:
            report_ambiguous(store, key, detail)
            _err("   stale lock 도 회수하지 않습니다.")
            return AMBIGUOUS
    if store.replace_lock(date_slug, payload, generation):
        _log(f"🔒 stale lock 회수 후 획득: {lock_uri} (이전 run_id={lock.get('run_id')})")
        return ACQUIRED
    again = store.read_lock(date_slug)
    if again is not None and again[0].get("run_id") == run_id:
        _log(f"🔒 stale lock 회수 후 획득 (재시도된 자기 요청 확인): {lock_uri}")
        return ACQUIRED
    _log("⏭️  stale lock 회수 경합에서 밀림 — 다른 execution 이 진행 중으로 판단")
    return DUPLICATE


def release_lock(store, date_slug, run_id):
    lock_uri = store.uri(store.lock_key(date_slug))
    try:
        existing = store.read_lock(date_slug)
    except ValueError:
        _err(f"⚠️ lock 해제 생략 — payload 해석 불가: {lock_uri}")
        return
    if existing is None:
        return
    lock, generation = existing
    if lock.get("run_id") != run_id:
        _err(f"⚠️ lock 해제 생략 — 다른 execution 소유 (run_id={lock.get('run_id')})")
        return
    if store.delete_lock(date_slug, generation):
        _log(f"🔓 lock 해제: {lock_uri}")
    else:
        _err(f"⚠️ lock 해제 생략 — 그 사이 다른 lock 으로 바뀜: {lock_uri}")


# ============================================================================
# mode 실행
# ============================================================================
def subprocess_runner(mode, extra_env):
    env = os.environ.copy()
    env.update(extra_env)
    _log(f"\n▶️ send_report.py --mode={mode}  ({extra_env.get('EXECUTION_STATE_KEY')})")
    try:
        r = subprocess.run(
            [sys.executable, "-u", str(SCRIPTS_DIR / "send_report.py"), f"--mode={mode}"],
            cwd=str(ROOT), env=env, timeout=MODE_TIMEOUT_SEC[mode],
        )
    except subprocess.TimeoutExpired:
        _err(f"⏱️ send_report.py --mode={mode} timeout ({MODE_TIMEOUT_SEC[mode]}s)")
        return 124
    return r.returncode


def run_mode(store, date_slug, mode, run_id, dry_run, runner):
    """COMPLETED / DRY_RUN_DONE / FAILED / AMBIGUOUS."""
    key = state_key(date_slug, mode)
    verdict, detail = evaluate(store, key)
    if verdict == COMPLETED:
        _log(f"⏭️  {key}: 이미 완료 ({detail}) — 재발송하지 않음")
        return COMPLETED
    if verdict == AMBIGUOUS:
        report_ambiguous(store, key, detail)
        return AMBIGUOUS

    store.write_status(key, "pending", run_id=run_id)
    rc = runner(mode, {"EXECUTION_STATE_KEY": key, "EXECUTION_RUN_ID": run_id})

    verdict, detail = evaluate(store, key)
    if verdict == COMPLETED:
        if rc != 0:
            _err(f"⚠️ {key}: 발송·기록은 완료됐으나 프로세스가 rc={rc} 로 종료")
        _log(f"✅ {key}: completed")
        return COMPLETED
    if verdict == AMBIGUOUS:
        report_ambiguous(store, key, detail)
        return AMBIGUOUS
    # pending 그대로 — SMTP 단계에 도달하지 않음
    if rc == 0 and dry_run:
        _log(f"✅ {key}: DRY_RUN 완료 (발송·기록 없음, status=pending 유지)")
        return DRY_RUN_DONE
    if rc == 0:
        _err(f"⛔ {key}: rc=0 인데 completed 기록이 없음 — lifecycle 기록 누락 가능성")
        report_ambiguous(store, key, "pending (rc=0)")
        return AMBIGUOUS
    _err(f"❌ {key}: 실패 (rc={rc}, state=pending) — SMTP 이전 단계 실패, 재실행 가능")
    return FAILED


def run_friday(store, date_slug, run_id, now, dry_run, runner):
    weekly = run_mode(store, date_slug, MODE_WEEKLY, run_id, dry_run, runner)
    if weekly == AMBIGUOUS:
        _err("Weekly Bundle ambiguous → Standard 실행하지 않음")
        return EXIT_AMBIGUOUS
    if weekly == FAILED:
        _err("Weekly Bundle 실패 → Standard 실행하지 않음")
        return EXIT_FAILED

    force = biweekly.force_requested()
    week = biweekly.kst_iso_week(now)
    if not biweekly.should_run_standard(now, force=force):
        _log(f"⏭️  KST ISO W{week:02d} (짝수) — Standard skip")
        return EXIT_OK
    _log(f"▶ KST ISO W{week:02d}" + (" (FORCE_BIWEEKLY_RUN)" if force and week % 2 == 0 else " (홀수)")
         + " — Standard 진행")

    standard = run_mode(store, date_slug, MODE_STANDARD, run_id, dry_run, runner)
    if standard == AMBIGUOUS:
        return EXIT_AMBIGUOUS
    if standard == FAILED:
        return EXIT_FAILED
    return EXIT_OK


def main(now=None, runner=None, store=None):
    send_report._load_dotenv_if_present()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("timezone-aware datetime 이 필요합니다")
    runner = runner or subprocess_runner
    dry_run = _truthy(os.environ.get("DRY_RUN"))
    date_slug = date_slug_for(now)

    if store is None:
        try:
            store = storage.get_state_store()
        except (storage.UnsupportedStorageBackend, storage.StorageConfigError) as e:
            _err(f"❌ 실행 상태 저장소 설정 오류: {e}")
            return EXIT_FAILED

    run_id = new_run_id(now)
    _log("=" * 60)
    _log("friday orchestrator")
    _log(f"now(KST)={now.astimezone(KST):%Y-%m-%d %H:%M:%S}  date_slug={date_slug}  "
         f"namespace={store.namespace}  run_id={run_id}" + ("  DRY_RUN" if dry_run else ""))
    _log("=" * 60)

    try:
        lock = acquire_lock(store, date_slug, run_id, now)
    except Exception:
        # 생성은 서버에서 성공했는데 응답만 실패한 경우, 내 run_id lock 이 12시간 동안 재실행을 막지 않도록 정리
        try:
            release_lock(store, date_slug, run_id)
        except Exception as e:
            _err(f"⚠️ lock 정리 실패 ({e})")
        raise
    if lock == DUPLICATE:
        return EXIT_OK
    if lock == AMBIGUOUS:
        return EXIT_AMBIGUOUS
    try:
        return run_friday(store, date_slug, run_id, now, dry_run, runner)
    finally:
        try:
            release_lock(store, date_slug, run_id)
        except Exception as e:
            # 해제 실패가 실행 결과(exit code)를 덮지 않도록 로그만 남긴다. 남은 lock 은 LOCK_TTL_SEC 후 stale 회수 대상.
            _err(f"⚠️ lock 해제 실패 ({e}) — {store.uri(store.lock_key(date_slug))} 는 stale 회수 대상으로 남음")


if __name__ == "__main__":
    sys.exit(main())
