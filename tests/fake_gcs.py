# -*- coding: utf-8 -*-
"""테스트 전용 in-memory GCS bucket.

storage.GcsStore 가 쓰는 google.cloud.storage Bucket/Blob 메서드만 흉내낸다:
  bucket.blob · bucket.get_blob · bucket.list_blobs(prefix, delimiter)
  blob.upload_from_string(if_generation_match) · download_as_text · exists · delete(if_generation_match)
generation precondition 의미 (0 = 없을 때만 생성, N = 현재 generation 이 N 일 때만)를
threading.Lock 으로 원자적으로 처리한다. 오류는 실제 SDK 처럼 .code (412/404) 를 가진다.

장애 주입: bucket.faults.append((op, name_substring, phase, exc))
  op    : "upload" | "download" | "delete"
  phase : "before" (적용 전 raise) | "after" (적용 후 raise — 응답 유실/재시도 상황)
"""

import threading


class FakeGcsError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code} {message}".strip())
        self.code = code


class FakeBucket:
    def __init__(self, name="fake-bucket"):
        self.name = name
        self.objects = {}          # name -> {"data", "generation", "content_type"}
        self.faults = []
        self.events = None         # list 를 넣으면 upload/delete 순서를 기록
        self._next_gen = 1000
        self._lock = threading.Lock()

    def blob(self, name):
        return FakeBlob(self, name)

    def get_blob(self, name):
        with self._lock:
            obj = self.objects.get(name)
            if obj is None:
                return None
            b = FakeBlob(self, name)
            b.generation = obj["generation"]
            return b

    def list_blobs(self, prefix=None, delimiter=None):
        prefix = prefix or ""
        with self._lock:
            names = sorted(self.objects)
        out = []
        for n in names:
            if not n.startswith(prefix):
                continue
            if delimiter and delimiter in n[len(prefix):]:
                continue
            out.append(FakeBlob(self, n))
        return iter(out)

    # ── 테스트 helper ──
    def put(self, name, text):
        with self._lock:
            self._next_gen += 1
            self.objects[name] = {"data": text, "generation": self._next_gen, "content_type": None}

    def text(self, name):
        return self.objects[name]["data"]

    def keys(self, prefix=""):
        return sorted(n for n in self.objects if n.startswith(prefix))

    def _fault(self, op, name, phase):
        for i, (o, sub, ph, exc) in enumerate(self.faults):
            if o == op and sub in name and ph == phase:
                del self.faults[i]
                raise exc


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket = bucket
        self.name = name
        self.generation = None

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        b = self.bucket
        with b._lock:
            b._fault("upload", self.name, "before")
            cur = b.objects.get(self.name)
            if if_generation_match is not None:
                cur_gen = cur["generation"] if cur else 0
                if cur_gen != if_generation_match:
                    raise FakeGcsError(412, f"precondition failed: {self.name}")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            b._next_gen += 1
            b.objects[self.name] = {"data": data, "generation": b._next_gen,
                                    "content_type": content_type}
            self.generation = b._next_gen
            if b.events is not None:
                b.events.append(("upload", self.name))
            b._fault("upload", self.name, "after")

    def download_as_text(self, encoding=None):
        b = self.bucket
        with b._lock:
            b._fault("download", self.name, "before")
            cur = b.objects.get(self.name)
            if cur is None:
                raise FakeGcsError(404, f"not found: {self.name}")
            return cur["data"]

    def exists(self):
        with self.bucket._lock:
            return self.name in self.bucket.objects

    def delete(self, if_generation_match=None):
        b = self.bucket
        with b._lock:
            b._fault("delete", self.name, "before")
            cur = b.objects.get(self.name)
            if cur is None:
                raise FakeGcsError(404, f"not found: {self.name}")
            if if_generation_match is not None and cur["generation"] != if_generation_match:
                raise FakeGcsError(412, f"precondition failed: {self.name}")
            del b.objects[self.name]
            if b.events is not None:
                b.events.append(("delete", self.name))
