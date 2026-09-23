"""Small deterministic checks without writing or importing any user data."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import import_chatgpt as target


class PipelineTests(unittest.TestCase):
    def test_categories_and_review(self):
        self.assertEqual(target.classify("运动训练", [("1", "我参加运动训练并记录恢复")]), "D")
        self.assertEqual(target.classify("生成图片", [("1", "画一张猫图")]), "E")
        self.assertEqual(target.classify("生成职业头像", [("1", "用 AI 生成头像")]), "E")
        self.assertEqual(target.classify("配眼镜建议", [("1", "我平时运动")]), "E")
        self.assertEqual(target.classify("英语口语练习", [("1", "我练习口语")]), "C")
        self.assertIsNone(target.classify("客户方案工具", [("1", "今天吃什么")]))
        self.assertIsNone(target.classify("未知", []))

    def test_candidates_need_source_and_review(self):
        ir = {"conversation_id": "c1", "title_raw": "项目工具", "raw_ref": {"shard_file": "conversations.json", "index_in_shard": 0}}
        texts = [("m1", "我想要解决需求不清的问题。所以我先问用户，再做一个小工具验证。")]
        row = target.candidate(ir, texts, "A", "knowledge")
        self.assertEqual(row["source"]["message_id"], "m1")
        self.assertEqual(row["confidence"], "NEEDS_REVIEW")
        self.assertIsNone(target.candidate(ir, [("m2", "我的密码是 abc123，先测试。")], "A", "knowledge"))
        memory = target.memory_candidates(ir, [("m3", "我决定调整工作方向。我现在开始学习新技术，所以要重新规划发展。")], None)
        self.assertTrue(any("工作" in row["event"] for row in memory))
        self.assertTrue(all(row["confidence"] == "NEEDS_REVIEW" for row in memory))

    def test_output_must_be_new_and_inside_project(self):
        with self.assertRaises(ValueError):
            target.validate_output(Path("C:/tmp/export"), target.PROJECT)

    def test_input_files_keeps_base_export_with_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            export = Path(temp)
            (export / "conversations.json").write_text("[]", encoding="utf-8")
            (export / "conversations-1.json").write_text("[]", encoding="utf-8")
            self.assertEqual([path.name for path in target.input_files(export)],
                             ["conversations.json", "conversations-1.json"])

    def test_career_change_and_long_project_rules(self):
        ir = {"conversation_id": "c2", "raw_ref": {"shard_file": "conversations.json", "index_in_shard": 0}}
        texts = [
            ("m1", "我转岗了，之前在三个部门之间轮换，并记录了每次职责变化。"),
            ("m2", "我现在也在做一个长期项目，但是这个需要长时间地维护。因此团队可能会调整分工。"),
            ("m3", "如果我离职了，可能换一家公司继续做。"),
        ]
        rows = target.memory_candidates(ir, texts, None)
        self.assertTrue(any("转岗了" in row["event"] for row in rows))
        self.assertTrue(any("长期项目" in row["event"] for row in rows))
        self.assertIsNone(next(row["impact"] for row in rows if "长期项目" in row["event"]))
        self.assertTrue(all(row["claim_status"] == "HYPOTHESIS" for row in rows if "如果我离职" in row["event"]))
        self.assertTrue(all(row["confidence"] == "NEEDS_REVIEW" and row["source"]["message_id"] for row in rows))


if __name__ == "__main__":
    unittest.main()
