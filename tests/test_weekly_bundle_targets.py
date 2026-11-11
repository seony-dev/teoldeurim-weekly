# -*- coding: utf-8 -*-
"""Weekly Bundle 대상 구성 (털어드림·묘한덕질, 짤덕방 제외) 회귀.

(2026-11-11) 운영 요청으로 Weekly Bundle 에서 jjalduk(짤덕방) 제외.

지키는 원칙:
  · Weekly Bundle target = teoldeurim, myohanduk 2개 (순서 고정).
  · jjalduk benchmark subprocess 실행 0회 · 첨부 0개 · sent history 기록 0개 · 메일 제목/본문 표시 0.
  · 메일 1통 · 첨부 2개. subject 형식 · recipient 로직 무변화.
  · auto: 짝수 주 Weekly(2 target) 만 / 홀수 주 Weekly(2 target) → 털어드림 Standard.
  · --mode=friday: 털어드림 Standard 만 (기존과 동일).
  · jjalduk profile · registry · storage namespace 는 그대로 사용 가능 (기능 보존).

benchmark subprocess · SMTP · GCS 는 fake. 외부 API·메일 없음.

실행:
  python tests/test_weekly_bundle_targets.py
"""
import json
import os
import sys
import tempfile
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
             "REPORT_DATE", "REISSUE", "NOTICE")
for _k in _ENV_KEYS:
    os.environ.pop(_k, None)

import storage
import send_report
import friday_orchestrator as orch
from targets import get_target, resolve_mode, list_targets
from fake_gcs import FakeBucket

send_report._load_dotenv_if_present = lambda: None

checks = []
def chk(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


KST = timezone(timedelta(hours=9))
EVEN_FRI = datetime(2026, 11, 13, 9, 10, tzinfo=KST)   # W46
ODD_FRI = datetime(2026, 11, 20, 9, 10, tzinfo=KST)    # W47

_TMP = Path(tempfile.mkdtemp(prefix="bundle_targets_"))
send_report.ROOT = _TMP
send_report.LOCAL_OUTPUT_DIR = _TMP / "local_output"
send_report.LOCAL_OUTPUT_DIR.mkdir()
FRAG = _TMP / "frag"
FRAG.mkdir()
send_report._env_snapshot = lambda: {"GMAIL_ADDRESS": "bot@x.com", "GMAIL_APP_PASSWORD": "p",
                                     "RECIPIENT_EMAIL": "user@x.com"}

# benchmark.py subprocess 대역 — 호출된 TARGET/MODE 를 기록하고 fragment·filtered_raw 를 만든다
child_calls = []
def _fake_run_child(script, extra_env, timeout=1200):
    target, mode = extra_env.get("TARGET"), extra_env.get("MODE")
    child_calls.append((target, mode))
    frag = Path(extra_env["REPORT_FRAGMENT_PATH"])
    frag.write_text(f"<html>{target}/{mode}</html>", encoding="utf-8")
    date = frag.name[:10]
    if mode == "standard":
        raw_name = f"{date}_standard_filtered_raw.json"
    elif target == "teoldeurim":
        raw_name = f"{date}_recent_filtered_raw.json"
    else:
        raw_name = f"{date}_{target}_weekly_filtered_raw.json"
    raw = send_report.ROOT / "benchmark" / raw_name
    raw.parent.mkdir(exist_ok=True)
    raw.write_text(json.dumps({"stages": {"direct_final": [{"video_id": f"{target}-{mode}-1"}],
                                          "benchmark_final": []}}), encoding="utf-8")
    return 0
send_report._run_child = _fake_run_child

mails = []
def _fake_send(env, **kw):
    mails.append({"recipient": env["RECIPIENT_EMAIL"], **kw})
send_report.send_email = _fake_send


def set_env(**kv):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(kv)


def attachments(m):
    names = [m["attachment_filename"]] + [fn for fn, _ in (m.get("extra_attachments") or [])]
    return [n for n in names if n]


# ═══════════════════════════════════════════════════════════════════
# 1) target 목록
# ═══════════════════════════════════════════════════════════════════
chk("TARGETS-01: Weekly Bundle target = [teoldeurim, myohanduk] (순서 포함)",
    [s for s, _ in send_report._WEEKLY_BUNDLE_TARGETS] == ["teoldeurim", "myohanduk"],
    f"{send_report._WEEKLY_BUNDLE_TARGETS}")
chk("TARGETS-02: 표시명 = 털어드림, 묘한덕질",
    [n for _, n in send_report._WEEKLY_BUNDLE_TARGETS] == ["털어드림", "묘한덕질"])
chk("TARGETS-03: jjalduk 미포함",
    "jjalduk" not in [s for s, _ in send_report._WEEKLY_BUNDLE_TARGETS])


# ═══════════════════════════════════════════════════════════════════
# 2~4) run_weekly_bundle 직접 실행 (GitHub/local 경로)
# ═══════════════════════════════════════════════════════════════════
set_env()
child_calls.clear(); mails.clear()
send_report.run_weekly_bundle("2099-12-04", FRAG, dry_run=False)
chk("RUN-01: benchmark subprocess = teoldeurim/weekly, myohanduk/weekly 2회만",
    child_calls == [("teoldeurim", "weekly"), ("myohanduk", "weekly")], f"{child_calls}")
chk("RUN-02: jjalduk benchmark 실행 0회", all(t != "jjalduk" for t, _ in child_calls))
chk("MAIL-01: 메일 1통", len(mails) == 1)
m = mails[0]
chk("MAIL-02: 첨부 정확히 2개 (털어드림·묘한덕질)",
    attachments(m) == ["teoldeurim_weekly_2099-12-04.html", "myohanduk_weekly_2099-12-04.html"],
    f"{attachments(m)}")
chk("MAIL-03: 짤덕방 첨부 파일명 0개", not any("jjalduk" in n for n in attachments(m)))
chk("MAIL-04: subject = [털어드림/묘한덕질 Weekly] date · 1건 / 1건",
    m["subject"] == "[털어드림/묘한덕질 Weekly] 2099-12-04 · 1건 / 1건", m["subject"])
body = m["html_body"]
chk("MAIL-05: 본문에 짤덕방/jjalduk 없음", "짤덕방" not in body and "jjalduk" not in body)
chk("MAIL-06: 본문에 털어드림·묘한덕질 건수 표시",
    "<b>털어드림</b> · 이번 주 후보 <b>1건</b>" in body and "<b>묘한덕질</b> · 이번 주 후보 <b>1건</b>" in body)
chk("MAIL-07: 본문 채널 수 문구 = 2개 (3개 문구 없음)",
    "2개 채널분" in body and "첨부된 HTML 파일 2개" in body and "3개" not in body and "세 채널" not in body)
chk("MAIL-08: recipient 로직 무변화 (env RECIPIENT_EMAIL 그대로)", m["recipient"] == "user@x.com")
hist_root = _TMP / "benchmark/history/sent"
chk("HIST-01: sent history = teoldeurim·myohanduk 만 기록, jjalduk 디렉토리 없음",
    (hist_root / "teoldeurim/2099-12-04_weekly.json").exists()
    and (hist_root / "myohanduk/2099-12-04_weekly.json").exists()
    and not (hist_root / "jjalduk").exists())

# 0건 subject 도 2채널
child_calls.clear(); mails.clear()
_orig_load = send_report._load_benchmark_result_ids
send_report._load_benchmark_result_ids = lambda p: []
send_report.run_weekly_bundle("2099-12-11", FRAG, dry_run=False)
send_report._load_benchmark_result_ids = _orig_load
chk("MAIL-09: 0건 subject = [털어드림/묘한덕질 Weekly] date · 신규 후보 없음",
    mails[0]["subject"] == "[털어드림/묘한덕질 Weekly] 2099-12-11 · 신규 후보 없음", mails[0]["subject"])
chk("MAIL-10: 0건 본문 안내 = 2개 채널 모두 0건", "2개 채널 모두 이번 주 신규 후보 0건" in mails[0]["html_body"])

# TEST_MODE / REISSUE 접두어 정책 유지
set_env(TEST_MODE="1", REISSUE="1")
mails.clear()
send_report.run_weekly_bundle("2099-12-18", FRAG, dry_run=False)
chk("MAIL-11: [TEST] [재발송] 접두어 정책 유지",
    mails[0]["subject"].startswith("[TEST] [재발송] [털어드림/묘한덕질 Weekly] 2099-12-18"), mails[0]["subject"])
set_env()


# ═══════════════════════════════════════════════════════════════════
# 5~8) orchestrator mode 별 (in-process send_report, fake GCS)
# ═══════════════════════════════════════════════════════════════════
def inproc_runner(mode, extra_env):
    saved = {k: os.environ.get(k) for k in extra_env}
    os.environ.update(extra_env)
    try:
        date = extra_env["EXECUTION_STATE_KEY"][:10]
        if mode == orch.MODE_WEEKLY:
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


def run_orch(bucket_name, now, mode="auto"):
    bucket = FakeBucket(bucket_name)
    bucket.put("prod/sent/jjalduk/2026-10-30_weekly.json", json.dumps({"candidates_video_ids": ["old-jj"]}))
    storage._make_gcs_bucket = lambda name: bucket
    set_env(SENT_HISTORY_BACKEND="gcs", SENT_HISTORY_GCS_BUCKET=bucket_name, STATE_NAMESPACE="prod")
    child_calls.clear(); mails.clear()
    before = dict(bucket.objects["prod/sent/jjalduk/2026-10-30_weekly.json"])
    rc = orch.main(now=now, runner=inproc_runner, mode=mode)
    after = bucket.objects.get("prod/sent/jjalduk/2026-10-30_weekly.json")
    set_env()
    return rc, bucket, before == after


rc, bk, jj_kept = run_orch("wb", ODD_FRI, mode="weekly-bundle")
chk("MODE-WB-01: --mode=weekly-bundle (홀수 주) → Weekly 2 target, Standard 0, exit 0",
    rc == 0 and child_calls == [("teoldeurim", "weekly"), ("myohanduk", "weekly")], f"{child_calls}")
chk("MODE-WB-02: 메일 1통 · 첨부 2개 · jjalduk 0", len(mails) == 1 and len(attachments(mails[0])) == 2
    and not any("jjalduk" in n for n in attachments(mails[0])))
chk("MODE-WB-03: GCS prod/sent 신규 기록 = teoldeurim·myohanduk weekly 만",
    sorted(k for k in bk.keys("prod/sent/") if "2026-11-20" in k)
    == ["prod/sent/myohanduk/2026-11-20_weekly.json", "prod/sent/teoldeurim/2026-11-20_weekly.json"])
chk("MODE-WB-04: 기존 prod/sent/jjalduk 객체 보존 (삭제·변경 없음)", jj_kept)
chk("MODE-WB-05: sent_history_plan 2 target",
    [p["target_slug"] for p in json.loads(bk.text("prod/status/2026-11-20_weekly-bundle.json"))["sent_history_plan"]]
    == ["teoldeurim", "myohanduk"])

rc, bk, jj_kept = run_orch("even", EVEN_FRI)
chk("AUTO-EVEN-01: auto 짝수 주(W46) → Weekly 2 target · Standard 0",
    rc == 0 and child_calls == [("teoldeurim", "weekly"), ("myohanduk", "weekly")], f"{child_calls}")
chk("AUTO-EVEN-02: 메일 1통 (Weekly) · 첨부 2개", len(mails) == 1 and len(attachments(mails[0])) == 2)
chk("AUTO-EVEN-03: jjalduk 기존 history 보존", jj_kept)

rc, bk, jj_kept = run_orch("odd", ODD_FRI)
chk("AUTO-ODD-01: auto 홀수 주(W47) → Weekly 2 target → Standard 1 (teoldeurim/standard)",
    rc == 0 and child_calls == [("teoldeurim", "weekly"), ("myohanduk", "weekly"), ("teoldeurim", "standard")],
    f"{child_calls}")
chk("AUTO-ODD-02: 메일 2통 — Weekly(첨부 2) → Standard(첨부 1, teoldeurim_standard)",
    len(mails) == 2 and len(attachments(mails[0])) == 2
    and attachments(mails[1]) == ["teoldeurim_standard_2026-11-20.html"]
    and mails[1]["subject"].startswith("[털어드림 격주 후보] 2026-11-20"))
chk("AUTO-ODD-03: jjalduk 실행 0 · 기존 history 보존",
    all(t != "jjalduk" for t, _ in child_calls) and jj_kept)

rc, bk, jj_kept = run_orch("fri", EVEN_FRI, mode="friday")
chk("MODE-FRI-01: --mode=friday → teoldeurim/standard 1회만 (Weekly 0)",
    rc == 0 and child_calls == [("teoldeurim", "standard")], f"{child_calls}")
chk("MODE-FRI-02: Standard 메일 1통 · 첨부 1개 · sent history teoldeurim/standard",
    len(mails) == 1 and attachments(mails[0]) == ["teoldeurim_standard_2026-11-13.html"]
    and "prod/sent/teoldeurim/2026-11-13_standard.json" in bk.objects)


# ═══════════════════════════════════════════════════════════════════
# 9) jjalduk 기능 보존 (profile · registry · storage)
# ═══════════════════════════════════════════════════════════════════
chk("KEEP-01: jjalduk 은 target registry 에 그대로 등록", "jjalduk" in list_targets())
jj = get_target("jjalduk")
chk("KEEP-02: jjalduk profile 로드 (slug·display_name·history_namespace)",
    jj["slug"] == "jjalduk" and jj["display_name"] == "짤덕방" and jj["history_namespace"] == "jjalduk")
cfg = resolve_mode("jjalduk", "weekly")
chk("KEEP-03: resolve_mode('jjalduk','weekly') 정상 (TARGET=jjalduk 단독 benchmark 실행 경로)",
    cfg["_TARGET"]["slug"] == "jjalduk" and cfg["MIN_VIEWS"] > 0)
with tempfile.TemporaryDirectory() as td:
    _orig_root = send_report.ROOT
    send_report.ROOT = Path(td)
    p = send_report._record_benchmark_sent("weekly", "2099-12-25", ["x"], target_slug="jjalduk")
    send_report.ROOT = _orig_root
    chk("KEEP-04: _record_benchmark_sent(target_slug='jjalduk') 범용 기록 경로 정상",
        p is not None and p.name == "2099-12-25_weekly.json" and p.parent.name == "jjalduk")
st = storage.GcsStore(FakeBucket("keep"), "prod")
st.bucket.put("prod/sent/jjalduk/2026-10-30_weekly.json", json.dumps({"candidates_video_ids": ["old-jj"]}))
chk("KEEP-05: storage 에서 jjalduk namespace 읽기 정상",
    st.list_namespaced("jjalduk") == ["2026-10-30_weekly.json"])


# ═══════════════════════════════════════════════════════════════════
# 결과
# ═══════════════════════════════════════════════════════════════════
print()
print("=" * 78)
print(" Weekly Bundle 대상 구성 (털어드림·묘한덕질, 짤덕방 제외) 회귀")
print("=" * 78)
p = f = 0
for name, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if ok: p += 1
    else: f += 1
print(f"\n총 {p+f}개: 통과 {p}, 실패 {f}")
if f: sys.exit(1)
