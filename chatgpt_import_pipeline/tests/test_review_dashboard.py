"""Regression checks for the optional local review stage."""

import json
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import create_review_dashboard as dashboard
import review_bridge


class ReviewDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        (self.output / "conversation_inventory.json").write_text(json.dumps([
            {"conversation_id": "c1", "title": "知识整理示例", "date": "2026-08-24"},
            {"conversation_id": "c2", "title": "项目工具开发示例", "date": "2026-09-15"},
            {"conversation_id": "c3", "title": "户外活动记录示例", "date": "2026-08-17"},
        ], ensure_ascii=False), encoding="utf-8")
        knowledge = [
            {"candidate_id": "extract-test-01-knowledge-system", "title": "知识整理方法示例",
             "problem": "文件夹混乱，难以查找。", "method": "先备份，再整理并保护源库。",
             "claim_status": "CURRENT", "confidence": "NEEDS_REVIEW",
             "source": {"conversation_id": "c1"}},
            {"candidate_id": "extract-test-02-project-tool", "title": "把已验证功能模块化接入项目工具",
             "problem": "已有工具能力需要清楚分组。", "method": "复用已有工具能力。",
             "claim_status": "UNKNOWN", "confidence": "NEEDS_REVIEW",
             "source": {"conversation_id": "c2"}},
        ]
        memory = [{"candidate_id": "extract-test-03-outdoor-activity", "event": "一次带有疼痛记录的训练经历",
                   "time": None, "impact": None, "claim_status": "FACT", "confidence": "NEEDS_REVIEW",
                   "source_role": "USER_SELF", "source": {"conversation_id": "c3"}}]
        (self.output / "knowledge_candidates.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in knowledge) + "\n", encoding="utf-8")
        (self.output / "memory_candidates.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in memory) + "\n", encoding="utf-8")

    def test_three_case_regression_normalizes_to_user_view(self):
        records = dashboard.build_records(self.output)
        self.assertEqual([row["candidate_id"] for row in records], [
            "extract-test-01-knowledge-system", "extract-test-02-project-tool", "extract-test-03-outdoor-activity"
        ])
        self.assertEqual([row["category"] for row in records], ["知识资产", "知识资产", "个人记忆"])
        self.assertEqual([row["sources"][0]["title"] for row in records], [
            "知识整理示例", "项目工具开发示例", "户外活动记录示例"
        ])

    def test_generation_does_not_create_review_result(self):
        result = dashboard.run(self.output)
        self.assertEqual(result["candidates"], 3)
        self.assertTrue((self.output / "review_dashboard.html").is_file())
        self.assertTrue((self.output / "review_manifest.json").is_file())
        self.assertTrue((self.output / "review_manifest.json").is_file())
        self.assertTrue((self.output / "start_review_bridge.py").is_file())
        self.assertTrue((self.output / "start_review_bridge.bat").is_file())
        self.assertFalse((self.output / "review_result.json").exists())
        html = (self.output / "review_dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn("conversation_id", html)
        self.assertNotIn("content_hash", html)
        self.assertNotIn("source_path", html)

    def test_bridge_accepts_standard_review_result(self):
        payload = json.dumps([
            {"candidate_id": "extract-test-01-knowledge-system", "decision": "keep", "category": "知识资产",
             "target": "knowledge", "timestamp": "2026-09-22T12:33:42.695Z"},
            {"candidate_id": "extract-test-02-project-tool", "decision": "keep", "category": "知识资产",
             "target": "knowledge", "timestamp": "2026-09-22T12:33:42.695Z"},
            {"candidate_id": "extract-test-03-outdoor-activity", "decision": "keep", "category": "个人记忆",
             "target": "memory", "timestamp": "2026-09-22T12:33:42.695Z"},
        ], ensure_ascii=False).encode("utf-8")
        rows = review_bridge.normalize_result(payload)
        self.assertEqual(len(rows), 3)
        self.assertEqual({"candidate_id", "decision", "category", "target", "timestamp"}, set(rows[0]))
        self.assertEqual({row["decision"] for row in rows}, {"keep"})

    def test_bridge_binds_result_to_current_candidate_snapshot(self):
        dashboard.run(self.output)
        candidate = dashboard.build_records(self.output)[0]
        rows = [{"candidate_id": candidate["candidate_id"], "decision": "keep",
                 "category": candidate["category"], "target": candidate["target"],
                 "timestamp": "2026-09-22T12:33:42.695Z"}]
        with patch.multiple(review_bridge, ROOT=self.output, TARGET=self.output / "review_result.json",
                            PREVIOUS=self.output / "review_result.previous.json",
                            META=self.output / "review_result.meta.json",
                            PREVIOUS_META=self.output / "review_result.previous.meta.json",
                            MANIFEST=self.output / "review_manifest.json"):
            snapshot_id = review_bridge.validate_against_manifest(rows)
            review_bridge.write_result(rows)
            saved = json.loads((self.output / "review_result.json").read_text(encoding="utf-8"))
            self.assertEqual(set(saved[0]), {"candidate_id", "decision", "category", "target", "timestamp"})
            self.assertEqual(json.loads((self.output / "review_result.meta.json").read_text(encoding="utf-8"))["snapshot_id"], snapshot_id)
            self.assertEqual(review_bridge.load_saved_result(), (rows, False))
            with (self.output / "knowledge_candidates.jsonl").open("ab") as stream:
                stream.write(b" ")
            self.assertEqual(review_bridge.load_saved_result(), ([], True))
            with self.assertRaises(ValueError):
                review_bridge.validate_against_manifest(rows)

    def test_bridge_requires_exact_local_origin(self):
        handler = object.__new__(review_bridge.ReviewHandler)
        handler.server = SimpleNamespace(server_port=51234)
        handler.headers = {"Host": "127.0.0.1:51234", "Origin": "http://127.0.0.1:51234"}
        self.assertTrue(handler._same_host())
        self.assertTrue(handler._same_origin())
        handler.headers["Origin"] = "http://attacker.invalid"
        self.assertFalse(handler._same_origin())
        handler.headers = {"Host": "attacker.invalid", "Origin": "http://attacker.invalid"}
        self.assertFalse(handler._same_host())

    def test_bridge_http_routes_do_not_expose_result_file(self):
        dashboard.run(self.output)
        candidate = dashboard.build_records(self.output)[0]
        rows = [{"candidate_id": candidate["candidate_id"], "decision": "keep",
                 "category": candidate["category"], "target": candidate["target"],
                 "timestamp": "2026-09-22T12:33:42.695Z"}]
        with patch.multiple(review_bridge, ROOT=self.output, TARGET=self.output / "review_result.json",
                            PREVIOUS=self.output / "review_result.previous.json",
                            META=self.output / "review_result.meta.json",
                            PREVIOUS_META=self.output / "review_result.previous.meta.json",
                            MANIFEST=self.output / "review_manifest.json"):
            review_bridge.write_result(rows)
            server = review_bridge.ReviewServer(("127.0.0.1", 0), review_bridge.ReviewHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/review_dashboard.html") as response:
                    self.assertEqual(response.status, 200)
                with urlopen(base + "/load-review") as response:
                    self.assertEqual(json.load(response)["results"], rows)
                with self.assertRaises(HTTPError) as missing:
                    urlopen(base + "/review_result.json")
                self.assertEqual(missing.exception.code, 404)
                forged = Request(base + "/load-review", headers={"Host": "attacker.invalid"})
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(forged)
                self.assertEqual(rejected.exception.code, 403)
                post = Request(base + "/save-review", data=b"[]", headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as missing_origin:
                    urlopen(post)
                self.assertEqual(missing_origin.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
