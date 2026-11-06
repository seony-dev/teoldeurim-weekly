# -*- coding: utf-8 -*-
"""benchmark sent history 저장소 + 실행 상태(lock/status/marker) 추상화.

SENT_HISTORY_BACKEND 환경변수로 backend 선택 (기본 "local").
  · local : filesystem. 경로·파일명·JSON 직렬화는 기존 코드와 동일
      - namespaced   : {sent_root}/{target_slug}/{name}
      - legacy flat  : {sent_root}/{name}            (sent_root 바로 아래 *.json)
      - legacy weekly: {legacy_weekly_dir}/{name}   (history/*.json, 읽기 전용)
  · gcs   : Google Cloud Storage. SENT_HISTORY_GCS_BUCKET + STATE_NAMESPACE(prod|test) 필요
      - {ns}/sent/{target_slug}/{name}
      - {ns}/legacy_flat/{name}
      - {ns}/legacy_weekly/{name}
      - {ns}/locks/{date}.lock · {ns}/status/{key}.json · {ns}/markers/{key}.completed
알 수 없는 backend 는 local 로 조용히 대체하지 않고 오류를 발생시킨다.

TEST_MODE 가 켜져 있으면 STATE_NAMESPACE 값과 무관하게 test namespace 만 쓸 수 있다.
google SDK 는 gcs backend 를 실제로 만들 때만 import 한다 (local 경로는 표준 라이브러리만 사용).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BACKEND = "local"
STATE_NAMESPACES = ("prod", "test")
EXECUTION_STATES = ("pending", "sending", "sent", "completed")
JSON_CONTENT_TYPE = "application/json; charset=utf-8"

HTTP_NOT_FOUND = 404
HTTP_PRECONDITION_FAILED = 412


class UnsupportedStorageBackend(RuntimeError):
    pass


class StorageConfigError(RuntimeError):
    pass


def is_test_mode(environ=None):
    env = os.environ if environ is None else environ
    return (env.get("TEST_MODE") or "").strip().lower() in ("1", "true", "yes")


def resolve_namespace(environ=None):
    env = os.environ if environ is None else environ
    if is_test_mode(env):
        return "test"
    ns = (env.get("STATE_NAMESPACE") or "").strip().lower()
    if ns not in STATE_NAMESPACES:
        raise StorageConfigError(
            f"STATE_NAMESPACE={ns!r} — 'prod' 또는 'test' 를 명시해야 합니다"
        )
    return ns


class LocalSentHistoryStore:
    def __init__(self, sent_root, legacy_weekly_dir=None):
        self.sent_root = Path(sent_root)
        self.legacy_weekly_dir = Path(legacy_weekly_dir) if legacy_weekly_dir else None

    @staticmethod
    def _list_json(directory):
        if directory is None or not directory.exists():
            return []
        return [p.name for p in sorted(directory.glob("*.json"))]

    @staticmethod
    def _read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    # ── namespaced: {sent_root}/{target_slug}/*.json ──
    def list_namespaced(self, target_slug):
        return self._list_json(self.sent_root / target_slug)

    def read_namespaced(self, target_slug, name):
        return self._read_json(self.sent_root / target_slug / name)

    # ── legacy flat: {sent_root}/*.json (파일명 필터링은 호출자 책임) ──
    def list_legacy_flat(self):
        return self._list_json(self.sent_root)

    def read_legacy_flat(self, name):
        return self._read_json(self.sent_root / name)

    # ── legacy weekly: history/*.json (읽기 전용) ──
    def list_legacy_weekly(self):
        return self._list_json(self.legacy_weekly_dir)

    def read_legacy_weekly(self, name):
        if self.legacy_weekly_dir is None:
            raise FileNotFoundError("legacy_weekly_dir 미지정")
        return self._read_json(self.legacy_weekly_dir / name)

    # ── write: target_slug 가 비면 legacy flat 위치 (기존 _record_benchmark_sent 규칙) ──
    def write_sent(self, name, payload, target_slug=None):
        sent_dir = self.sent_root / target_slug if target_slug else self.sent_root
        sent_dir.mkdir(parents=True, exist_ok=True)
        p = sent_dir / name
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


def _http_code(exc):
    # google.api_core.exceptions.* 는 HTTP status 를 .code 로 가진다 (PreconditionFailed=412, NotFound=404)
    return getattr(exc, "code", None)


class GcsStore:
    """GCS backend. bucket 은 google.cloud.storage.Bucket (테스트에서는 같은 메서드를 가진 fake)."""

    def __init__(self, bucket, namespace):
        if namespace not in STATE_NAMESPACES:
            raise StorageConfigError(f"알 수 없는 namespace={namespace!r}")
        if is_test_mode() and namespace != "test":
            raise StorageConfigError("TEST_MODE 에서는 test namespace 만 사용할 수 있습니다")
        self.bucket = bucket
        self.namespace = namespace

    def key(self, *parts):
        return "/".join((self.namespace,) + parts)

    def uri(self, key):
        return f"gs://{self.bucket.name}/{key}"

    def _list_json(self, *parts):
        prefix = self.key(*parts) + "/"
        names = [b.name[len(prefix):] for b in self.bucket.list_blobs(prefix=prefix, delimiter="/")]
        return sorted(n for n in names if n.endswith(".json"))

    def _read_json(self, key):
        return json.loads(self.bucket.blob(key).download_as_text(encoding="utf-8"))

    def _write_json(self, key, payload, if_generation_match=None):
        self.bucket.blob(key).upload_from_string(
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type=JSON_CONTENT_TYPE,
            if_generation_match=if_generation_match,
        )

    def _create_if_absent(self, key, payload):
        """if_generation_match=0 — 객체가 없을 때만 생성. 이미 있으면 False (412). 그 외 오류는 그대로 raise."""
        try:
            self._write_json(key, payload, if_generation_match=0)
            return True
        except Exception as e:
            if _http_code(e) == HTTP_PRECONDITION_FAILED:
                return False
            raise

    # ── sent history (LocalSentHistoryStore 와 같은 의미) ──
    def list_namespaced(self, target_slug):
        return self._list_json("sent", target_slug)

    def read_namespaced(self, target_slug, name):
        return self._read_json(self.key("sent", target_slug, name))

    def list_legacy_flat(self):
        return self._list_json("legacy_flat")

    def read_legacy_flat(self, name):
        return self._read_json(self.key("legacy_flat", name))

    def list_legacy_weekly(self):
        return self._list_json("legacy_weekly")

    def read_legacy_weekly(self, name):
        return self._read_json(self.key("legacy_weekly", name))

    def write_sent(self, name, payload, target_slug=None):
        k = self.key("sent", target_slug, name) if target_slug else self.key("legacy_flat", name)
        self._write_json(k, payload)
        return self.uri(k)

    # ── execution lock: {ns}/locks/{date}.lock ──
    def lock_key(self, date_slug):
        return self.key("locks", f"{date_slug}.lock")

    def try_create_lock(self, date_slug, payload):
        return self._create_if_absent(self.lock_key(date_slug), payload)

    def read_lock(self, date_slug):
        """(payload, generation) 또는 lock 없으면 None. payload JSON 이 깨졌으면 ValueError."""
        k = self.lock_key(date_slug)
        blob = self.bucket.get_blob(k)
        if blob is None:
            return None
        try:
            text = blob.download_as_text(encoding="utf-8")
        except Exception as e:
            if _http_code(e) == HTTP_NOT_FOUND:
                return None
            raise
        return json.loads(text), blob.generation

    def replace_lock(self, date_slug, payload, expected_generation):
        """generation 이 그대로일 때만 덮어쓴다 (CAS). 바뀌었으면 False."""
        try:
            self._write_json(self.lock_key(date_slug), payload, if_generation_match=expected_generation)
            return True
        except Exception as e:
            if _http_code(e) == HTTP_PRECONDITION_FAILED:
                return False
            raise

    def delete_lock(self, date_slug, expected_generation):
        """generation 이 그대로일 때만 삭제. 이미 없으면 True, 다른 lock 으로 바뀌었으면 False."""
        try:
            self.bucket.blob(self.lock_key(date_slug)).delete(if_generation_match=expected_generation)
            return True
        except Exception as e:
            if _http_code(e) == HTTP_NOT_FOUND:
                return True
            if _http_code(e) == HTTP_PRECONDITION_FAILED:
                return False
            raise

    # ── status: {ns}/status/{key}.json ──
    def status_key(self, state_key):
        return self.key("status", f"{state_key}.json")

    def read_status(self, state_key):
        try:
            return self._read_json(self.status_key(state_key))
        except Exception as e:
            if _http_code(e) == HTTP_NOT_FOUND:
                return None
            raise

    def write_status(self, state_key, state, run_id=None, extra=None):
        if state not in EXECUTION_STATES:
            raise ValueError(f"알 수 없는 execution state={state!r}")
        payload = {
            "key": state_key,
            "state": state,
            "run_id": run_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload.update(extra)
        self._write_json(self.status_key(state_key), payload)

    # ── completed marker: {ns}/markers/{key}.completed ──
    def marker_key(self, state_key):
        return self.key("markers", f"{state_key}.completed")

    def marker_exists(self, state_key):
        return self.bucket.blob(self.marker_key(state_key)).exists()

    def create_marker(self, state_key, payload):
        return self._create_if_absent(self.marker_key(state_key), payload)


def _make_gcs_bucket(bucket_name):
    from google.cloud import storage as gcs  # gcs backend 에서만 로드
    return gcs.Client().bucket(bucket_name)


def _gcs_store_from_env():
    bucket_name = (os.environ.get("SENT_HISTORY_GCS_BUCKET") or "").strip()
    if not bucket_name:
        raise StorageConfigError("SENT_HISTORY_BACKEND=gcs 에는 SENT_HISTORY_GCS_BUCKET 이 필요합니다")
    return GcsStore(_make_gcs_bucket(bucket_name), resolve_namespace())


def selected_backend():
    return (os.environ.get("SENT_HISTORY_BACKEND") or "").strip().lower() or DEFAULT_BACKEND


def get_store(sent_root, legacy_weekly_dir=None):
    backend = selected_backend()
    if backend == "local":
        return LocalSentHistoryStore(sent_root, legacy_weekly_dir)
    if backend == "gcs":
        return _gcs_store_from_env()
    raise UnsupportedStorageBackend(
        f"알 수 없는 SENT_HISTORY_BACKEND={backend!r} (지원: 'local', 'gcs')"
    )


def get_state_store():
    """execution lock/status/marker 저장소. gcs backend 에서만 지원."""
    backend = selected_backend()
    if backend != "gcs":
        raise UnsupportedStorageBackend(
            f"execution state 는 SENT_HISTORY_BACKEND=gcs 에서만 지원합니다 (현재 {backend!r})"
        )
    return _gcs_store_from_env()
