# -*- coding: utf-8 -*-
"""sent history storage abstraction (local backend) 회귀.

지키는 원칙:
  · SENT_HISTORY_BACKEND 기본값 local. gcs / 알 수 없는 값은 local 로 대체하지 않고 오류.
  · local backend 경로·파일명·JSON 직렬화가 기존 코드와 동일.
  · benchmark sent dedup ID 집합이 리팩터링 전 filesystem 직접 읽기와 동일 (parity).
  · send_report._record_benchmark_sent 의 파일 위치·payload·TEST_MODE skip 동작 동일.

임시 디렉토리 + 실 repo 읽기 전용 · 외부 API/메일/실 history 쓰기 없음.

실행:
  python tests/test_storage_local.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config"))

for _k in ("SENT_HISTORY_BACKEND", "SENT_HISTORY_GCS_BUCKET", "STATE_NAMESPACE", "TEST_MODE"):
    os.environ.pop(_k, None)

import storage
import benchmark
import send_report
import benchmark_config
from targets import get_target

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
# 리팩터링 전 loader (commit 4e864ae 기준 benchmark.py 원본 로직 복사)
# ═══════════════════════════════════════════════════════════════════
def _ref_legacy_weekly(history_dir):
    result = set()
    if not history_dir.exists():
        return result
    for f in sorted(history_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in (d.get("candidates") or []):
            vid = it.get("video_id")
            if vid:
                result.add(vid)
    return result


def _ref_flat(sent_root):
    result = set()
    if not sent_root.exists():
        return result
    pat = re.compile(r"^\d{4}-\d{2}-\d{2}_(standard|recent)\.json$")
    for f in sorted(sent_root.glob("*.json")):
        if not pat.match(f.name):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for vid in (d.get("candidates_video_ids") or []):
            if vid:
                result.add(vid)
    return result


def _ref_namespaced(sent_root, slug):
    result = set()
    ns_dir = sent_root / slug
    if not ns_dir.exists():
        return result
    for f in sorted(ns_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for vid in (d.get("candidates_video_ids") or []):
            if vid:
                result.add(vid)
    return result


def _ref_for_target(sent_root, history_dir, target):
    ns = _ref_namespaced(sent_root, target["slug"])
    flat = _ref_flat(sent_root) if target.get("legacy_sent_history_flat") else set()
    wk = _ref_legacy_weekly(history_dir) if target.get("legacy_weekly_history") else set()
    return ns, flat, wk, ns | flat | wk


# ═══════════════════════════════════════════════════════════════════
# 1) backend 선택
# ═══════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for val, label in [(None, "미설정"), ("", "빈 문자열"), ("local", "local"),
                       ("LOCAL", "대문자"), ("  local ", "공백")]:
        if val is None:
            os.environ.pop("SENT_HISTORY_BACKEND", None)
        else:
            os.environ["SENT_HISTORY_BACKEND"] = val
        s = storage.get_store(sent_root=td)
        chk(f"BACKEND-{label}: LocalSentHistoryStore 반환",
            isinstance(s, storage.LocalSentHistoryStore))

    # gcs 는 Phase 2A 에서 구현됨 — bucket 미설정이면 설정 오류 (local 로 fallback 하지 않음)
    for val, exc in (("gcs", storage.StorageConfigError), ("GCS", storage.StorageConfigError),
                     ("s3", storage.UnsupportedStorageBackend),
                     ("firestore", storage.UnsupportedStorageBackend)):
        os.environ["SENT_HISTORY_BACKEND"] = val
        raised = False
        try:
            storage.get_store(sent_root=td)
        except exc:
            raised = True
        chk(f"BACKEND-UNSUPPORTED[{val}]: local fallback 없이 오류 ({exc.__name__})", raised)
    os.environ.pop("SENT_HISTORY_BACKEND", None)


# ═══════════════════════════════════════════════════════════════════
# 2) local backend 기본 동작 (임시 디렉토리)
# ═══════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    sent_root = td / "benchmark" / "history" / "sent"
    hist = td / "history"
    s = storage.LocalSentHistoryStore(sent_root, hist)

    chk("EMPTY-01: sent_root 없음 → list_namespaced []", s.list_namespaced("teoldeurim") == [])
    chk("EMPTY-02: sent_root 없음 → list_legacy_flat []", s.list_legacy_flat() == [])
    chk("EMPTY-03: history 없음 → list_legacy_weekly []", s.list_legacy_weekly() == [])
    chk("EMPTY-04: legacy_weekly_dir=None → []",
        storage.LocalSentHistoryStore(sent_root).list_legacy_weekly() == [])
    (sent_root / "jjalduk").mkdir(parents=True)
    hist.mkdir()
    chk("EMPTY-05: 빈 namespace 디렉토리 → []", s.list_namespaced("jjalduk") == [])
    chk("EMPTY-06: 빈 history 디렉토리 → []", s.list_legacy_weekly() == [])

    payload = {"date_kst": "2099-01-02", "profile": "weekly", "target": "jjalduk",
               "generated_at_utc": "2099-01-02T00:00:00+00:00",
               "candidates_video_ids": ["한글id", "b"], "count": 2}
    p = s.write_sent("2099-01-02_weekly.json", payload, target_slug="jjalduk")
    chk("WRITE-NS-01: 경로 = sent_root/{target}/{name}",
        p == sent_root / "jjalduk" / "2099-01-02_weekly.json")
    chk("WRITE-NS-02: 직렬화 = json.dumps(ensure_ascii=False, indent=2) 와 바이트 동일",
        p.read_text(encoding="utf-8") == _dump(payload))
    chk("WRITE-NS-03: read_namespaced 로 동일 payload 복원",
        s.read_namespaced("jjalduk", "2099-01-02_weekly.json") == payload)
    chk("WRITE-NS-04: list_namespaced 에 노출",
        s.list_namespaced("jjalduk") == ["2099-01-02_weekly.json"])

    p2 = s.write_sent("2099-01-03_standard.json", {"x": 1}, target_slug=None)
    chk("WRITE-FLAT-01: target_slug=None → sent_root 바로 아래 (기존 flat 규칙)",
        p2 == sent_root / "2099-01-03_standard.json")
    p3 = s.write_sent("2099-01-04_recent.json", {"x": 2}, target_slug="")
    chk("WRITE-FLAT-02: target_slug='' → flat (기존 falsy 규칙)",
        p3 == sent_root / "2099-01-04_recent.json")
    chk("FLAT-LIST-01: list_legacy_flat 는 root 직속 *.json 만 (namespace 하위 제외)",
        s.list_legacy_flat() == ["2099-01-03_standard.json", "2099-01-04_recent.json"])
    chk("FLAT-READ-01: read_legacy_flat", s.read_legacy_flat("2099-01-03_standard.json") == {"x": 1})

    s.write_sent("2099-01-02_weekly.json", {"overwritten": True}, target_slug="jjalduk")
    chk("WRITE-OVERWRITE: 같은 이름 재기록은 덮어쓰기 (기존 write_text 동작 유지)",
        s.read_namespaced("jjalduk", "2099-01-02_weekly.json") == {"overwritten": True})

    for n in ("2026-03-02.json", "2026-01-05.json", "2026-02-02_recent.json"):
        (hist / n).write_text(_dump({"candidates": [{"video_id": n[:10]}]}), encoding="utf-8")
    (hist / "notes.txt").write_text("x", encoding="utf-8")
    chk("WEEKLY-LIST-01: 이름순 정렬 + *.json 만",
        s.list_legacy_weekly() == ["2026-01-05.json", "2026-02-02_recent.json", "2026-03-02.json"])
    chk("WEEKLY-READ-01: read_legacy_weekly",
        s.read_legacy_weekly("2026-01-05.json") == {"candidates": [{"video_id": "2026-01-05"}]})

    (hist / "broken.json").write_text("{not json", encoding="utf-8")
    raised = False
    try:
        s.read_legacy_weekly("broken.json")
    except Exception:
        raised = True
    chk("READ-ERR-01: 깨진 JSON 은 read 에서 예외 (호출자가 경고 후 skip — 기존과 동일)", raised)


# ═══════════════════════════════════════════════════════════════════
# 3) dedup parity — 임시 fixture (깨진 파일·패턴 불일치·하위 디렉토리 포함)
# ═══════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    sent_root = td / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
    hist = td / "history"
    (sent_root / "teoldeurim").mkdir(parents=True)
    (sent_root / "myohanduk").mkdir(parents=True)
    hist.mkdir()

    def w(path, obj):
        path.write_text(_dump(obj) if not isinstance(obj, str) else obj, encoding="utf-8")

    w(sent_root / "teoldeurim" / "2026-10-23_weekly.json", {"candidates_video_ids": ["t1", "t2", ""]})
    w(sent_root / "teoldeurim" / "2026-10-23_standard.json", {"candidates_video_ids": ["t3"]})
    w(sent_root / "teoldeurim" / "broken.json", "{oops")
    w(sent_root / "myohanduk" / "2026-10-23_weekly.json", {"candidates_video_ids": ["m1"]})
    w(sent_root / "2026-07-17_standard.json", {"candidates_video_ids": ["f1", "t1"]})
    w(sent_root / "2026-07-20_recent.json", {"candidates_video_ids": ["f2"]})
    w(sent_root / "2026-07-20_weekly.json", {"candidates_video_ids": ["NOT_FLAT"]})   # 패턴 불일치
    w(sent_root / "notes.json", {"candidates_video_ids": ["NOT_FLAT2"]})             # 패턴 불일치
    w(sent_root / "2026-07-21_recent.json", "{broken")
    w(hist / "2026-05-01.json", {"candidates": [{"video_id": "h1"}, {"video_id": None}]})
    w(hist / "2026-05-08_recent.json", {"candidates": [{"video_id": "h2"}]})
    w(hist / "bad.json", "nope")

    orig_root, orig_hist = benchmark.ROOT, benchmark.HISTORY_DIR
    benchmark.ROOT, benchmark.HISTORY_DIR = td, hist
    try:
        for slug in ("teoldeurim", "myohanduk", "jjalduk"):
            t = get_target(slug)
            got = benchmark.load_sent_video_ids_for_target(t)
            ref = _ref_for_target(sent_root, hist, t)
            chk(f"PARITY-FIXTURE[{slug}]: (ns, flat, weekly, merged) 4개 집합 모두 원본과 동일",
                got == ref, f"got={got} ref={ref}")
        te = benchmark.load_sent_video_ids_for_target(get_target("teoldeurim"))
        chk("PARITY-FIXTURE-DETAIL: teoldeurim merged 기대값",
            te[3] == {"t1", "t2", "t3", "f1", "f2", "h1", "h2"}, f"got={te[3]}")
        chk("PARITY-FIXTURE-DETAIL: 패턴 불일치 flat 파일 무시",
            "NOT_FLAT" not in te[3] and "NOT_FLAT2" not in te[3])
    finally:
        benchmark.ROOT, benchmark.HISTORY_DIR = orig_root, orig_hist


# ═══════════════════════════════════════════════════════════════════
# 4) dedup parity — 실제 repo 데이터 (읽기 전용)
# ═══════════════════════════════════════════════════════════════════
real_sent_root = ROOT / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
real_hist = ROOT / "history"
for slug in ("teoldeurim", "myohanduk", "jjalduk"):
    t = get_target(slug)
    got = benchmark.load_sent_video_ids_for_target(t)
    ref = _ref_for_target(real_sent_root, real_hist, t)
    chk(f"PARITY-REPO[{slug}]: 실 repo sent history 4개 집합 원본과 동일 (merged {len(ref[3])}개)",
        got == ref)


# ═══════════════════════════════════════════════════════════════════
# 5) send_report._record_benchmark_sent — 경로·payload·TEST_MODE 동작
# ═══════════════════════════════════════════════════════════════════
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    orig_root = send_report.ROOT
    send_report.ROOT = td
    sent_root = td / benchmark_config.BENCHMARK_SENT_HISTORY_DIR
    try:
        p = send_report._record_benchmark_sent("weekly", "2099-01-02", ["a", "b"], target_slug="jjalduk")
        chk("REC-01: target namespace 경로", p == sent_root / "jjalduk" / "2099-01-02_weekly.json")
        d = json.loads(p.read_text(encoding="utf-8"))
        chk("REC-02: payload 키 구성 동일",
            list(d.keys()) == ["date_kst", "profile", "target", "generated_at_utc",
                               "candidates_video_ids", "count"])
        chk("REC-03: payload 값",
            d["date_kst"] == "2099-01-02" and d["profile"] == "weekly"
            and d["target"] == "jjalduk" and d["candidates_video_ids"] == ["a", "b"]
            and d["count"] == 2)
        chk("REC-04: 직렬화 형식 (ensure_ascii=False, indent=2)",
            p.read_text(encoding="utf-8") == _dump(d))

        p2 = send_report._record_benchmark_sent("standard", "2099-01-03", ["x"])
        chk("REC-05: target_slug 미지정 → legacy flat 위치 + target ''",
            p2 == sent_root / "2099-01-03_standard.json"
            and json.loads(p2.read_text(encoding="utf-8"))["target"] == "")

        r = send_report._record_benchmark_sent("weekly", "2099-01-04", [], target_slug="jjalduk")
        chk("REC-06: video_ids 빈 목록 → None, 파일 없음",
            r is None and not (sent_root / "jjalduk" / "2099-01-04_weekly.json").exists())

        os.environ["TEST_MODE"] = "1"
        r = send_report._record_benchmark_sent("weekly", "2099-01-05", ["a"], target_slug="jjalduk")
        chk("REC-07: TEST_MODE=1 → None, 파일 없음 (production 오염 없음)",
            r is None and not (sent_root / "jjalduk" / "2099-01-05_weekly.json").exists())
        os.environ["SENT_HISTORY_BACKEND"] = "gcs"
        r = send_report._record_benchmark_sent("weekly", "2099-01-06", ["a"], target_slug="jjalduk")
        chk("REC-08: TEST_MODE skip 은 backend 선택보다 먼저 (기존 순서 유지)", r is None)
        os.environ.pop("TEST_MODE", None)

        raised = False
        try:
            send_report._record_benchmark_sent("weekly", "2099-01-07", ["a"], target_slug="jjalduk")
        except storage.StorageConfigError:
            raised = True
        chk("REC-09: backend=gcs (bucket 미설정) → 오류 + 파일 미생성 (local 로 조용히 저장하지 않음)",
            raised and not (sent_root / "jjalduk" / "2099-01-07_weekly.json").exists())
    finally:
        os.environ.pop("SENT_HISTORY_BACKEND", None)
        os.environ.pop("TEST_MODE", None)
        send_report.ROOT = orig_root


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" sent history storage (local backend) 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    m = "OK" if ok else "FAIL"
    print(f"  [{m}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
