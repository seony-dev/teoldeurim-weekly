# -*- coding: utf-8 -*-
"""benchmark sent history 저장소 추상화.

SENT_HISTORY_BACKEND 환경변수로 backend 선택 (기본 "local").
현재는 local (filesystem) 만 구현. 미구현 backend 는 local 로 조용히 대체하지 않고
UnsupportedStorageBackend 를 발생시킨다.

local backend 의 경로·파일명·JSON 직렬화는 기존 코드와 동일:
  · namespaced : {sent_root}/{target_slug}/{name}
  · legacy flat: {sent_root}/{name}            (sent_root 바로 아래 *.json)
  · legacy weekly: {legacy_weekly_dir}/{name}   (history/*.json, 읽기 전용)

표준 라이브러리만 사용.
"""

import json
import os
from pathlib import Path

DEFAULT_BACKEND = "local"


class UnsupportedStorageBackend(RuntimeError):
    pass


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


def selected_backend():
    return (os.environ.get("SENT_HISTORY_BACKEND") or "").strip().lower() or DEFAULT_BACKEND


def get_store(sent_root, legacy_weekly_dir=None):
    backend = selected_backend()
    if backend == "local":
        return LocalSentHistoryStore(sent_root, legacy_weekly_dir)
    if backend == "gcs":
        raise UnsupportedStorageBackend(
            "SENT_HISTORY_BACKEND=gcs 는 아직 구현되지 않았습니다. "
            "local 로 대체하지 않습니다 — 환경변수를 확인하세요."
        )
    raise UnsupportedStorageBackend(
        f"알 수 없는 SENT_HISTORY_BACKEND={backend!r} (지원: 'local')"
    )
