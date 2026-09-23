"""V1.2 boundaries and real parser integration using isolated synthetic exports."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_chatgpt as target


class SafetyTests(unittest.TestCase):
    def test_all_statuses_and_precedence(self):
        cases = [
            ("我已经完成了工作项目的第一轮测试。", "FACT"),
            ("我现在每周运动三次，保持规律训练和恢复。", "CURRENT"),
            ("我现在打算明年更换工作方向，想转做新岗位。", "PLAN"),
            ("如果我已经离职了，可能转做新的工作方向。", "HYPOTHESIS"),
            ("我没有完成工作项目的第一轮测试。", "UNKNOWN"),
            ("我已经完成测试，明天准备交给客户。", "PLAN"),
            ("我现在想生成一张职业头像，不代表职业选择。", "PLAN"),
            ("我已经确定下周入职新公司工作。", "PLAN"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(target.claim_status(text, "USER_SELF"), expected)
        self.assertEqual(target.claim_status("我已经离职了", "USER_QUOTE"), "QUOTE")
        self.assertEqual(target.claim_status("我已经离职了", "UNKNOWN"), "UNKNOWN")

    def test_quote_and_native_assistant_excluded(self):
        ir = {"conversation_id": "c", "raw_ref": {"shard_file": "s", "index_in_shard": 0}}
        quotes = [
            "【助手总结】我现在应该调整工作方向，这是我的长期方向。",
            "我的同事说：我已经换了公司工作，现在学习AI应用。",
            "> 我现在每周运动三次，保持规律训练和恢复。",
            "小说角色独白，假装我是主角：我已经离职了，选择了新的公司。",
            "我已经入职了新公司。朋友说：我也准备去找新工作。",
        ]
        for text in quotes:
            with self.subTest(text=text):
                self.assertEqual(target.source_role(text), "USER_QUOTE")
                self.assertEqual(target.memory_candidates(ir, [("m", text)], "C"), [])
        self.assertEqual(target.source_role("我已经离职了", "assistant"), "ASSISTANT")
        ir["main_path"] = [{"role": "assistant", "hidden": False, "node_id": "m",
                            "content": {"content_type": "text", "parts": ["我已经离职了"]}}]
        self.assertEqual(target.user_texts(ir), [])
        self.assertEqual(target.source_role("同事正在学习AI，已经完成一个项目。"), "UNKNOWN")

    def test_every_memory_topic_uses_status_and_dedup(self):
        ir = {"conversation_id": "c", "raw_ref": {"shard_file": "s", "index_in_shard": 0}}
        text = "我现在打算明年更换工作方向，想转做新岗位。"
        rows = target.memory_candidates(ir, [("m1", text), ("m2", text)], "C")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim_status"], "PLAN")
        self.assertEqual(rows[0]["source_role"], "USER_SELF")
        self.assertEqual(len(rows[0]["source_refs"]), 2)
        self.assertEqual(rows[0]["candidate_id"], "memory:" + rows[0]["content_hash"])

    def test_merge_preserves_variants_and_never_promotes_conflict(self):
        base = {"event": "我已经完成工作项目", "time": None, "impact": None,
                "source": {"conversation_id": "a", "message_id": "m"},
                "claim_status": "FACT", "source_role": "USER_SELF", "confidence": "NEEDS_REVIEW"}
        other = {**base, "event": "我已经完成工作项目  ", "claim_status": "HYPOTHESIS",
                 "source": {"conversation_id": "b", "message_id": "m"}}
        rows = target.merge_candidates([base, other], "memory")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim_status"], "UNKNOWN")
        self.assertEqual(len(rows[0]["source_refs"]), 2)
        self.assertEqual(rows[0]["source_refs"][1]["evidence"]["event"], other["event"])
        self.assertEqual(target.merge_candidates(rows + [base], "memory"), rows)

    def test_distinct_same_topic_events_and_knowledge_candidates_are_retained(self):
        ir = {"conversation_id": "c", "title_raw": "工作项目", "raw_ref": {"shard_file": "s", "index_in_shard": 0}}
        memory_text = "我现在每周运动三次，保持规律训练和恢复。我已经参加过一次长距离越野比赛并完成了训练目标。"
        memories = target.memory_candidates(ir, [("m1", memory_text)], "D")
        self.assertEqual(len(memories), 2)
        knowledge_texts = [
            ("k1", "我发现项目需求难以排序和确认优先级。我先访谈使用者，再按影响排序并验证结果。"),
            ("k2", "我发现交付遗漏的问题很难检查。我先列出验收项，再逐项核对现场结果。"),
        ]
        knowledge = target.knowledge_candidates(ir, knowledge_texts, "A")
        self.assertEqual(len(knowledge), 2)


def conversation(cid, text):
    return {"id": cid, "title": "周期性训练", "current_node": "m1", "mapping": {
        "m1": {"parent": None, "children": [], "message": {
            "id": "m1", "author": {"role": "user"}, "create_time": 1,
            "content": {"content_type": "text", "parts": [text]}, "metadata": {}}}}}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.export = self.root / "export" / "conversations.json"
        self.export.parent.mkdir()
        self.data = [conversation("c1", "我现在每周运动三次，保持规律训练和恢复。")]
        self.export.write_text(json.dumps(self.data), encoding="utf-8")
        self.scope = patch.object(target, "PROJECT", self.project)
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def test_initial_repeated_updated_and_cross_conversation_merge(self):
        first = self.project / "first"
        result = target.run(self.export, first)
        self.assertEqual(result["processed"], 1)
        audit = (first / "IMPORT_REPORT.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.export.parent), audit)
        self.assertIsNone(json.loads((first / "import_history.json").read_text(encoding="utf-8"))["previous_history"])
        initial_bytes = {p.name: p.read_bytes() for p in first.iterdir()}
        initial = json.loads((first / "import_history.json").read_text(encoding="utf-8"))
        second = self.project / "second"
        repeated = target.run(self.export, second, first / "import_history.json")
        self.assertEqual((repeated["processed"], repeated["skipped"], repeated["new_candidates"]), (0, 1, 0))
        self.assertEqual((first / "memory_candidates.jsonl").read_bytes(), (second / "memory_candidates.jsonl").read_bytes())
        self.data.append(copy.deepcopy(self.data[0]))
        self.data[1]["id"] = "c2"
        self.data[0]["mapping"]["m1"]["message"]["content"]["parts"] = ["我现在每周运动四次，保持规律训练和恢复。"]
        self.export.write_text(json.dumps(self.data), encoding="utf-8")
        third = self.project / "third"
        updated = target.run(self.export, third, second / "import_history.json")
        self.assertEqual(updated["processed"], 2)
        rows = [json.loads(line) for line in (third / "memory_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(len(r["source_refs"]) for r in rows), [1, 2])
        history = json.loads((third / "import_history.json").read_text(encoding="utf-8"))
        self.assertNotEqual(initial["conversations"]["c1"]["message_hashes"], history["conversations"]["c1"]["message_hashes"])
        self.assertEqual(initial_bytes, {p.name: p.read_bytes() for p in first.iterdir()})

    def test_corrupt_history_and_partial_output_fail_closed(self):
        first = self.project / "first"
        target.run(self.export, first)
        (first / "memory_candidates.jsonl").write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            target.run(self.export, self.project / "bad", first / "import_history.json")
        self.assertFalse((self.project / "bad").exists())
        with patch.object(target, "write_jsonl", side_effect=OSError("test write failure")):
            with self.assertRaises(OSError):
                target.run(self.export, self.project / "partial")
        self.assertFalse((self.project / "partial" / "import_history.json").exists())

    def test_v1_2_history_is_reprocessed_once_on_upgrade(self):
        first = self.project / "first"
        target.run(self.export, first)
        history_path = first / "import_history.json"
        history = json.loads(history_path.read_text(encoding="utf-8"))
        history["version"] = "1.2"
        history_path.write_text(json.dumps(history), encoding="utf-8")
        upgraded = self.project / "upgraded"
        result = target.run(self.export, upgraded, history_path)
        self.assertEqual((result["processed"], result["skipped"]), (1, 0))
        self.assertIn("检测到 V1.2 历史", (upgraded / "IMPORT_REPORT.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
