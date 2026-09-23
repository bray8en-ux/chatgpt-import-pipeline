"""Serve a generated review page and write review_result.json beside it."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "review_result.json"
PREVIOUS = ROOT / "review_result.previous.json"
META = ROOT / "review_result.meta.json"
PREVIOUS_META = ROOT / "review_result.previous.meta.json"
MANIFEST = ROOT / "review_manifest.json"
MAX_BODY_BYTES = 1_000_000
ALLOWED_DECISIONS = {"keep", "delete", "hold"}
ALLOWED_TARGETS = {"knowledge", "memory"}
ALLOWED_CATEGORIES = {"知识资产", "个人记忆"}


def normalize_result(raw: bytes) -> list[dict[str, str]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("result must be a non-empty list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("each result must be an object")
        values = {key: row.get(key) for key in ("candidate_id", "decision", "category", "target", "timestamp")}
        if not all(isinstance(value, str) and value.strip() for value in values.values()):
            raise ValueError("result fields are incomplete")
        if values["decision"] not in ALLOWED_DECISIONS:
            raise ValueError("unsupported decision")
        if values["target"] not in ALLOWED_TARGETS or values["category"] not in ALLOWED_CATEGORIES:
            raise ValueError("unsupported candidate target or category")
        if values["candidate_id"] in seen:
            raise ValueError("duplicate candidate_id")
        seen.add(values["candidate_id"])
        result.append(values)
    return result


def validate_against_manifest(result: list[dict[str, str]]) -> str:
    if not MANIFEST.is_file():
        raise ValueError("review manifest is missing")
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("review manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("candidates"), list):
        raise ValueError("review manifest version is unsupported")
    files = manifest.get("candidate_files")
    if not isinstance(files, dict):
        raise ValueError("review manifest is malformed")
    for name in ("knowledge_candidates.jsonl", "memory_candidates.jsonl"):
        expected = files.get(name)
        path = ROOT / name
        if not isinstance(expected, str) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("candidate snapshot changed after review page generation")
    allowed = {}
    for row in manifest["candidates"]:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("candidate_id", "category", "target")):
            raise ValueError("review manifest is malformed")
        if row["candidate_id"] in allowed:
            raise ValueError("review manifest has duplicate candidate_id")
        allowed[row["candidate_id"]] = row
    for row in result:
        expected = allowed.get(row["candidate_id"])
        if expected is None or row["category"] != expected["category"] or row["target"] != expected["target"]:
            raise ValueError("review result does not match this candidate snapshot")
    snapshot_id = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if manifest.get("snapshot_id") != snapshot_id:
        raise ValueError("review manifest snapshot is invalid")
    return snapshot_id


def load_saved_result() -> tuple[list[dict[str, str]], bool]:
    if not TARGET.is_file():
        return [], False
    try:
        result = normalize_result(TARGET.read_bytes())
        snapshot_id = validate_against_manifest(result)
        metadata = json.loads(META.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("snapshot_id") != snapshot_id:
            return [], True
        return result, False
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return [], True


def write_result(result: list[dict[str, str]]) -> None:
    snapshot_id = validate_against_manifest(result)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".review_result_", suffix=".tmp", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        if TARGET.is_file():
            shutil.copy2(TARGET, PREVIOUS)
        if META.is_file():
            shutil.copy2(META, PREVIOUS_META)
        os.replace(temporary, TARGET)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    fd, temporary = tempfile.mkstemp(prefix=".review_meta_", suffix=".tmp", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"snapshot_id": snapshot_id}, handle)
            handle.write("\n")
        os.replace(temporary, META)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json_response(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _same_host(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def _same_origin(self) -> bool:
        host = f"127.0.0.1:{self.server.server_port}"
        return self.headers.get("Host") == host and self.headers.get("Origin") == f"http://{host}"

    def do_GET(self):
        if not self._same_host():
            self._json_response(403, {"ok": False, "error": "host rejected"})
            return
        path = urlsplit(self.path).path
        if path == "/load-review":
            result, stale = load_saved_result()
            self._json_response(200, {"results": result, "stale": stale})
        elif path == "/review_dashboard.html":
            super().do_GET()
        else:
            self.send_error(404)

    def do_HEAD(self):
        if not self._same_host():
            self.send_error(403)
        elif urlsplit(self.path).path == "/review_dashboard.html":
            super().do_HEAD()
        else:
            self.send_error(404)

    def do_POST(self):
        if urlsplit(self.path).path != "/save-review":
            self._json_response(404, {"ok": False, "error": "not found"})
            return
        if not self._same_origin():
            self._json_response(403, {"ok": False, "error": "origin rejected"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json_response(413, {"ok": False, "error": "payload too large"})
            return
        try:
            result = normalize_result(self.rfile.read(length))
            write_result(result)
        except (ValueError, OSError) as exc:
            print(f"save rejected: {exc}")
            self._json_response(400, {"ok": False, "error": "invalid review result"})
            return
        self._json_response(200, {"ok": True, "saved": "review_result.json"})

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


class ReviewServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    server = ReviewServer(("127.0.0.1", 0), ReviewHandler)
    url = f"http://127.0.0.1:{server.server_port}/review_dashboard.html"
    print(f"审核页面：{url}")
    print("审核结果只会写入当前目录的 review_result.json。按 Ctrl+C 停止服务。")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n桥接服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
