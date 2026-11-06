# -*- coding: utf-8 -*-
"""GCS storage backend · namespace · atomic lock/status/marker primitive 회귀.

지키는 원칙:
  · SENT_HISTORY_BACKEND 기본값 local 유지. local 경로는 google SDK 를 import 하지 않음.
  · gcs 는 SENT_HISTORY_GCS_BUCKET + STATE_NAMESPACE(prod|test) 필수, 누락 시 오류.
  · TEST_MODE 이면 STATE_NAMESPACE 와 무관하게 test namespace 강제 (prod store 생성 자체가 오류).
  · sent history 경로·JSON 형식이 local 과 같은 의미. dedup ID 집합 local == gcs.
  · lock/marker 는 if_generation_match=0 create-if-absent. 412 만 "이미 있음", 그 외 오류는 전파.

실제 GCS 접속 없음 (tests/fake_gcs.FakeBucket). 외부 API·메일 없음.

실행:
  python tests/test_storage_gcs.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))

_ENV_KEYS = ("SENT_HISTORY_BACKEND", "SENT_HISTORY_GCS_BUCKET", "STATE_NAMESPACE", "TEST_MODE",
             "EXECUTION_STATE_KEY", "EXECUTION_RUN_ID")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import benchmark
import send_report
import benchmark_config
from targets import get_target
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


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


_buckets = {}
def _fake_factory(name):
    return _buckets.setdefault(name, FakeBucket(name))

_orig_factory = storage._make_gcs_bucket
storage._make_gcs_bucket = _fake_factory


# ═══════════════════════════════════════════════════════════════════
# 1) backend / namespace 선택
# ═══════════════════════════════════════════════════════════════════
clear_env()
chk("SEL-01: 기본값 local", isinstance(storage.get_store(sent_root=ROOT), storage.LocalSentHistoryStore))
chk("SEL-02: local 경로는 google SDK 미로드", "google.cloud.storage" not in sys.modules)
chk("SEL-03: local backend 에서 get_state_store → 오류",
    raises(storage.get_state_store, storage.UnsupportedStorageBackend))

set_env(SENT_HISTORY_BACKEND="gcs", STATE_NAMESPACE="prod")
chk("ENV-01: gcs + bucket 누락 → StorageConfigError",
    raises(lambda: storage.get_store(sent_root=ROOT), storage.StorageConfigError))
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1")
chk("ENV-02: gcs + STATE_NAMESPACE 누락 → StorageConfigError",
    raises(lambda: storage.get_store(sent_root=ROOT), storage.StorageConfigError))
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1", STATE_NAMESPACE="staging")
chk("ENV-03: STATE_NAMESPACE=staging → StorageConfigError",
    raises(lambda: storage.get_store(sent_root=ROOT), storage.StorageConfigError))
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1", STATE_NAMESPACE="PROD")
s = storage.get_store(sent_root=ROOT)
chk("NS-01: STATE_NAMESPACE=PROD → prod", isinstance(s, storage.GcsStore) and s.namespace == "prod")
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1", STATE_NAMESPACE="test")
chk("NS-02: STATE_NAMESPACE=test → test", storage.get_store(sent_root=ROOT).namespace == "test")
for tm in ("1", "true", "YES"):
    set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1", STATE_NAMESPACE="prod", TEST_MODE=tm)
    chk(f"NS-TEST[{tm}]: TEST_MODE → STATE_NAMESPACE=prod 여도 test 강제",
        storage.get_store(sent_root=ROOT).namespace == "test"
        and storage.get_state_store().namespace == "test")
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="b1", TEST_MODE="1")
chk("NS-TEST-NOENV: TEST_MODE 면 STATE_NAMESPACE 없어도 test", storage.get_state_store().namespace == "test")
set_env(TEST_MODE="1")
chk("NS-GUARD-01: TEST_MODE 에서 GcsStore(bucket, 'prod') 직접 생성 → 오류",
    raises(lambda: storage.GcsStore(FakeBucket(), "prod"), storage.StorageConfigError))
clear_env()
chk("NS-GUARD-02: 알 수 없는 namespace 직접 생성 → 오류",
    raises(lambda: storage.GcsStore(FakeBucket(), "staging"), storage.StorageConfigError))
chk("NS-GUARD-03: TEST_MODE 꺼져 있으면 prod 생성 가능",
    storage.GcsStore(FakeBucket(), "prod").namespace == "prod")


# ═══════════════════════════════════════════════════════════════════
# 2) sent history read/write (prod namespace)
# ═══════════════════════════════════════════════════════════════════
clear_env()
bk = FakeBucket("hist")
st = storage.GcsStore(bk, "prod")
payload = {"date_kst": "2099-01-02", "profile": "weekly", "target": "jjalduk",
           "generated_at_utc": "2099-01-02T00:00:00+00:00",
           "candidates_video_ids": ["한글id", "b"], "count": 2}
uri = st.write_sent("2099-01-02_weekly.json", payload, target_slug="jjalduk")
chk("GCS-W-01: write_sent 경로 = prod/sent/{target}/{name}",
    "prod/sent/jjalduk/2099-01-02_weekly.json" in bk.objects)
chk("GCS-W-02: 반환값 gs:// URI", uri == "gs://hist/prod/sent/jjalduk/2099-01-02_weekly.json")
chk("GCS-W-03: 직렬화 = json.dumps(ensure_ascii=False, indent=2) 와 동일",
    bk.text("prod/sent/jjalduk/2099-01-02_weekly.json")
    == json.dumps(payload, ensure_ascii=False, indent=2))
chk("GCS-W-04: content_type JSON utf-8",
    bk.objects["prod/sent/jjalduk/2099-01-02_weekly.json"]["content_type"] == storage.JSON_CONTENT_TYPE)
chk("GCS-R-01: read_namespaced JSON round-trip (한글 포함)",
    st.read_namespaced("jjalduk", "2099-01-02_weekly.json") == payload)
st.write_sent("2099-01-03_standard.json", {"x": 1})
st.write_sent("2099-01-04_recent.json", {"x": 2}, target_slug="")
chk("GCS-W-05: target_slug 없음/'' → prod/legacy_flat/ (local 의 flat 규칙과 같은 의미)",
    "prod/legacy_flat/2099-01-03_standard.json" in bk.objects
    and "prod/legacy_flat/2099-01-04_recent.json" in bk.objects)
bk.put("prod/sent/jjalduk/notes.txt", "x")
bk.put("prod/sent/jjalduk/sub/2099-01-09_weekly.json", "{}")
bk.put("prod/sent/jjalduk/2099-01-01_weekly.json", json.dumps({"candidates_video_ids": ["z"]}))
chk("GCS-L-01: list_namespaced = 직속 *.json 만, 이름순 (하위 경로·비 json 제외)",
    st.list_namespaced("jjalduk") == ["2099-01-01_weekly.json", "2099-01-02_weekly.json"])
chk("GCS-L-02: 없는 target → []", st.list_namespaced("myohanduk") == [])
chk("GCS-L-03: list_legacy_flat",
    st.list_legacy_flat() == ["2099-01-03_standard.json", "2099-01-04_recent.json"])
chk("GCS-L-04: legacy_weekly 비어 있음 → []", st.list_legacy_weekly() == [])
bk.put("prod/legacy_weekly/2026-05-01.json", json.dumps({"candidates": [{"video_id": "h1"}]}))
chk("GCS-L-05: list/read_legacy_weekly",
    st.list_legacy_weekly() == ["2026-05-01.json"]
    and st.read_legacy_weekly("2026-05-01.json") == {"candidates": [{"video_id": "h1"}]})
chk("GCS-R-02: 없는 객체 read → 오류 (호출자가 경고 후 skip)",
    raises(lambda: st.read_legacy_flat("nope.json"), FakeGcsError))
bk.put("prod/legacy_flat/broken.json", "{oops")
chk("GCS-R-03: 깨진 JSON read → ValueError", raises(lambda: st.read_legacy_flat("broken.json"), ValueError))
st_test = storage.GcsStore(bk, "test")
chk("GCS-NS-01: test namespace 는 prod 데이터를 보지 않음",
    st_test.list_namespaced("jjalduk") == [] and st_test.list_legacy_flat() == [])


# ═══════════════════════════════════════════════════════════════════
# 3) dedup parity — 실 repo sent history 를 fake bucket 에 실어 local 결과와 비교
# ═══════════════════════════════════════════════════════════════════
clear_env()
local_results = {s: benchmark.load_sent_video_ids_for_target(get_target(s))
                 for s in ("teoldeurim", "myohanduk", "jjalduk")}
seed = _fake_factory("parity")
sent_root = ROOT / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
for f in sorted(sent_root.glob("*.json")):
    seed.put(f"prod/legacy_flat/{f.name}", f.read_text(encoding="utf-8"))
for d in sorted(p for p in sent_root.iterdir() if p.is_dir()):
    for f in sorted(d.glob("*.json")):
        seed.put(f"prod/sent/{d.name}/{f.name}", f.read_text(encoding="utf-8"))
for f in sorted((ROOT / "history").glob("*.json")):
    seed.put(f"prod/legacy_weekly/{f.name}", f.read_text(encoding="utf-8"))
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="parity", STATE_NAMESPACE="prod")
for slug in ("teoldeurim", "myohanduk", "jjalduk"):
    got = benchmark.load_sent_video_ids_for_target(get_target(slug))
    chk(f"PARITY-GCS[{slug}]: (ns, flat, weekly, merged) local backend 결과와 동일 "
        f"(merged {len(local_results[slug][3])}개)", got == local_results[slug])
clear_env()


# ═══════════════════════════════════════════════════════════════════
# 4) send_report._record_benchmark_sent — gcs backend
# ═══════════════════════════════════════════════════════════════════
set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET="rec", STATE_NAMESPACE="prod")
rb = _fake_factory("rec")
with tempfile.TemporaryDirectory() as td:
    orig_root = send_report.ROOT
    send_report.ROOT = Path(td)
    try:
        r = send_report._record_benchmark_sent("weekly", "2099-02-06", ["v1", "v2"], target_slug="myohanduk")
        chk("REC-GCS-01: 반환값 gs:// URI · prod/sent/myohanduk/ 에 저장",
            r == "gs://rec/prod/sent/myohanduk/2099-02-06_weekly.json"
            and "prod/sent/myohanduk/2099-02-06_weekly.json" in rb.objects)
        d = json.loads(rb.text("prod/sent/myohanduk/2099-02-06_weekly.json"))
        chk("REC-GCS-02: payload 스키마 동일",
            list(d.keys()) == ["date_kst", "profile", "target", "generated_at_utc",
                               "candidates_video_ids", "count"]
            and d["candidates_video_ids"] == ["v1", "v2"] and d["target"] == "myohanduk")
        chk("REC-GCS-03: local 파일은 만들지 않음", not any(Path(td).rglob("*.json")))
        os.environ["TEST_MODE"] = "1"
        r = send_report._record_benchmark_sent("weekly", "2099-02-13", ["v1"], target_slug="myohanduk")
        chk("REC-GCS-04: TEST_MODE → None, prod/test 어디에도 sent history 없음",
            r is None and not [k for k in rb.keys() if "2099-02-13" in k])
    finally:
        send_report.ROOT = orig_root
clear_env()


# ═══════════════════════════════════════════════════════════════════
# 5) atomic lock primitive
# ═══════════════════════════════════════════════════════════════════
lb = FakeBucket("locks")
prod = storage.GcsStore(lb, "prod")
test = storage.GcsStore(lb, "test")
p1 = {"run_id": "A", "start_ts": "t", "ttl_sec": 1, "expected_completion_by": "t"}
p2 = {"run_id": "B", "start_ts": "t", "ttl_sec": 1, "expected_completion_by": "t"}
chk("KEY-01: lock_key = {ns}/locks/{date}.lock", prod.lock_key("2026-11-06") == "prod/locks/2026-11-06.lock")
chk("KEY-02: status_key = {ns}/status/{key}.json",
    prod.status_key("2026-11-06_weekly-bundle") == "prod/status/2026-11-06_weekly-bundle.json")
chk("KEY-03: marker_key = {ns}/markers/{key}.completed",
    prod.marker_key("2026-11-06_friday") == "prod/markers/2026-11-06_friday.completed")
chk("LOCK-01: 첫 생성 True", prod.try_create_lock("2026-11-06", p1) is True)
chk("LOCK-02: 같은 날짜 두 번째 생성 False (412 → 이미 있음)", prod.try_create_lock("2026-11-06", p2) is False)
got = prod.read_lock("2026-11-06")
chk("LOCK-03: 두 번째 시도는 기존 lock 을 덮지 않음 (payload = 첫 번째)", got is not None and got[0] == p1)
gen = got[1]
chk("LOCK-04: 다른 날짜 lock 은 독립", prod.try_create_lock("2026-11-13", p2) is True)
chk("LOCK-05: test namespace lock 은 prod 와 독립", test.try_create_lock("2026-11-06", p2) is True
    and prod.read_lock("2026-11-06")[0] == p1)
chk("LOCK-06: read_lock 없음 → None", prod.read_lock("2030-01-01") is None)
chk("LOCK-07: replace_lock 틀린 generation → False", prod.replace_lock("2026-11-06", p2, gen + 999) is False)
chk("LOCK-08: replace_lock 맞는 generation → True (CAS)", prod.replace_lock("2026-11-06", p2, gen) is True
    and prod.read_lock("2026-11-06")[0] == p2)
gen2 = prod.read_lock("2026-11-06")[1]
chk("LOCK-09: delete_lock 옛 generation → False (남의 lock 삭제 방지)", prod.delete_lock("2026-11-06", gen) is False
    and prod.read_lock("2026-11-06") is not None)
chk("LOCK-10: delete_lock 현재 generation → True", prod.delete_lock("2026-11-06", gen2) is True
    and prod.read_lock("2026-11-06") is None)
chk("LOCK-11: 이미 없는 lock delete → True", prod.delete_lock("2026-11-06", gen2) is True)
lb.put("prod/locks/2026-12-04.lock", "{corrupt")
chk("LOCK-12: 깨진 lock payload → ValueError", raises(lambda: prod.read_lock("2026-12-04"), ValueError))
lb.faults.append(("upload", "locks/2026-12-11", "before", FakeGcsError(503, "unavailable")))
chk("LOCK-13: 412 가 아닌 오류(503) 는 '이미 있음' 으로 처리하지 않고 전파",
    raises(lambda: prod.try_create_lock("2026-12-11", p1), FakeGcsError)
    and prod.read_lock("2026-12-11") is None)

# 동시 생성 경합 — 16 스레드 중 정확히 1개만 성공
rb2 = FakeBucket("race")
rs = storage.GcsStore(rb2, "prod")
barrier = threading.Barrier(16)
wins = []
def _race(i):
    barrier.wait()
    if rs.try_create_lock("2026-11-20", {"run_id": f"r{i}"}):
        wins.append(i)
ths = [threading.Thread(target=_race, args=(i,)) for i in range(16)]
[t.start() for t in ths]; [t.join() for t in ths]
chk("LOCK-RACE: 16 스레드 동시 create-if-absent → 정확히 1개 성공", len(wins) == 1, f"wins={wins}")


# ═══════════════════════════════════════════════════════════════════
# 6) status / marker primitive
# ═══════════════════════════════════════════════════════════════════
sb = FakeBucket("state")
ss = storage.GcsStore(sb, "prod")
k = "2026-11-06_weekly-bundle"
chk("STATUS-01: 없음 → None", ss.read_status(k) is None)
ss.write_status(k, "pending", run_id="R")
stt = ss.read_status(k)
chk("STATUS-02: write/read", stt["state"] == "pending" and stt["run_id"] == "R" and stt["key"] == k
    and "updated_at_utc" in stt)
ss.write_status(k, "sending", run_id="R", extra={"sent_history_plan": [{"target_slug": "jjalduk"}]})
chk("STATUS-03: extra 필드 보존", ss.read_status(k)["sent_history_plan"] == [{"target_slug": "jjalduk"}])
chk("STATUS-04: 알 수 없는 state → ValueError", raises(lambda: ss.write_status(k, "done"), ValueError))
sb.put(ss.status_key("bad"), "{nope")
chk("STATUS-05: 깨진 status JSON → ValueError", raises(lambda: ss.read_status("bad"), ValueError))
chk("MARKER-01: 없음 → False", ss.marker_exists(k) is False)
chk("MARKER-02: 첫 생성 True", ss.create_marker(k, {"key": k}) is True and ss.marker_exists(k))
chk("MARKER-03: 재생성 False (idempotent, 기존 marker 유지)",
    ss.create_marker(k, {"key": "other"}) is False and json.loads(sb.text(ss.marker_key(k)))["key"] == k)


storage._make_gcs_bucket = _orig_factory
clear_env()

# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" GCS storage backend · namespace · lock/status/marker 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
