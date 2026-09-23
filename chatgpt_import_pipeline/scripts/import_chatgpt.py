"""Read a ChatGPT Export and write review candidates, never the Source Layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.3"
SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT = SKILL_ROOT.parent
SOURCE_PARSER_DIR = SKILL_ROOT / "vendor"
CATEGORIES = {
    "A": ("工作项目", ("售前", "工作台", "标书", "投标", "配单", "客户", "产品", "开发", "展会")),
    "B": ("AI 学习", ("ai", "gpt", "模型", "agent", "skill", "提示词", "知识库", "codex")),
    "C": ("个人成长", ("职业", "跳槽", "成长", "人生", "认知", "复盘", "转型", "英语", "法语", "语言学习", "口语")),
    "D": ("运动生活", ("跑步", "马拉松", "越野", "徒步", "骑行", "运动", "跑鞋", "训练")),
    "E": ("一次性内容", ("图片", "头像", "翻译", "天气", "配眼镜")),
}
SENSITIVE = re.compile(r"password|api[_-]?key|secret|token\s*[:=]|密码|密钥|身份证|银行卡|\b\d{11}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
SELF_REPORT = re.compile(r"我(?:曾经|以前|现在|已经|开始|参加|决定|选择|发现|做了|用了|想要|会|把|的)")
METHOD = re.compile(r"我(?:会|用|把|先|通过|做了|尝试)|做法是|方法是|先.+再")
MEMORY = re.compile(r"我(?:以前|现在|已经|开始|参加|决定|选择|发现)|第一次")
IMPACT = re.compile(r"所以|因此|让我|影响|从此|之后")
DOMAIN = re.compile(r"职业|跳槽|工作|跑步|马拉松|越野|徒步|骑行|运动|学习|AI|ai|习惯|选择")
CAREER_CHANGE = re.compile(r"我(?:换了.{0,6}部门|转岗了|转到.{0,12}(?:岗位|部门|公司)|跳槽到.{0,12}公司|入职了.{0,12}公司|离职了|辞职了|.{0,20}跟着.{0,10}换了.{0,6}部门)")
LONG_PROJECT = re.compile(r"我(?:现在|目前|一直|已经)?(?:也)?在做.{0,25}(?:项目|知识库|工作台).{0,60}(?:长期|长时间|持续|一直|维护)")
NOT_COMPLETED = re.compile(r"如果|假如|要是|打算|想要|想|可能|计划|准备|希望|将来|以后")
MEMORY_TOPICS = {
    "career": re.compile(r"职业|跳槽|公司|工作|售前|岗位"),
    "ai": re.compile(r"AI|ai|模型|Agent|工具"),
    "sports": re.compile(r"跑步|马拉松|越野|徒步|骑行|运动"),
    "choice": re.compile(r"决定|选择|打算|方向"),
}
CLAIM_STATUSES = {"FACT", "CURRENT", "PLAN", "HYPOTHESIS", "QUOTE", "UNKNOWN"}
SOURCE_ROLES = {"USER_SELF", "USER_QUOTE", "ASSISTANT", "UNKNOWN"}
QUOTE_MARKERS = re.compile(r'[“”「」『』"]|^\s*>|(?:他说|她说|同事说|朋友说|助手|ChatGPT|GPT|Claude).{0,12}(?:说|总结|建议|回复)|他说|她说|同事说|朋友说|引用|转述|转贴|转发|粘贴|原文|对话记录|聊天记录|助手总结|助手观点|虚构|扮演|假装|测试材料|测试案例', re.I | re.M)
SELF_SUBJECT = re.compile(r"(?:^|[，。！？\n：])\s*(?:(?:对|其实|所以|因为|现在|目前|最近|以前|但是|而且|然后)[，、\s]*)*我(?!们|的(?:朋友|同事|客户|家人|领导))")


def source_role(text: str, role: str = "user") -> str:
    if role == "assistant":
        return "ASSISTANT"
    if role != "user":
        return "UNKNOWN"
    # ponytail: whole-message quarantine loses mixed quotes/self reports; no attribution model in V1.2.
    if QUOTE_MARKERS.search(text):
        return "USER_QUOTE"
    return "USER_SELF" if SELF_SUBJECT.search(text) else "UNKNOWN"


def claim_status(text: str, role: str, context: str = "") -> str:
    if role in {"USER_QUOTE", "ASSISTANT"}:
        return "QUOTE"
    if role != "USER_SELF":
        return "UNKNOWN"
    evidence = context or text
    # Conservative precedence: an uncertain message must never promote one clause to FACT.
    if re.search(r"如果|假如|假设|要是|万一|可能|也许|或许|猜测|是否|是不是|[?？]", evidence):
        return "HYPOTHESIS"
    if re.search(r"打算|计划|准备|想|希望|决定|将要|将会|即将|预计|要去|明年|明天|后天|下周|下月|下个月|下半年|未来|以后|应该", evidence):
        return "PLAN"
    if re.search(r"没有|没|未|不|取消|他|她|别人", text):
        return "UNKNOWN"
    if re.search(r"已经|曾经|以前|完成了|做了|用了|参加了|换了|入职了|离职了|辞职了|转岗了", text):
        return "FACT"
    if re.search(r"现在|目前|正在|一直|每周|每天|在做|开始", text):
        return "CURRENT"
    return "UNKNOWN"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def content_hash(row: dict, kind: str) -> str:
    fields = ("event", "time") if kind == "memory" else ("problem", "method")
    values = [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", row.get(f) or "")).strip() for f in fields]
    return digest([kind, *values])


def merge_candidates(rows: list[dict], kind: str) -> list[dict]:
    merged = {}
    fields = ("event", "time", "impact") if kind == "memory" else ("title", "problem", "method")
    for row in rows:
        key = content_hash(row, kind)
        refs = row.get("source_refs") or [{**row["source"], "source_role": row["source_role"],
                "claim_status": row["claim_status"], "evidence": {f: row.get(f) for f in fields}}]
        if key not in merged:
            merged[key] = {**row, "content_hash": key, "candidate_id": f"{kind}:{key}", "source_refs": []}
        target = merged[key]
        for ref in refs:
            if ref not in target["source_refs"]:
                target["source_refs"].append(ref)
        for field in ("claim_status", "source_role"):
            values = {ref[field] for ref in target["source_refs"]}
            target[field] = next(iter(values)) if len(values) == 1 else "UNKNOWN"
    return list(merged.values())


def load_history(path: Path | None) -> tuple[dict, list[dict], list[dict], bool]:
    if path is None:
        return {}, [], [], False
    path = path.resolve()
    if PROJECT not in path.parents or path.name != "import_history.json":
        raise ValueError("历史必须是项目内某次完整输出的 import_history.json")
    history = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(history, dict) or history.get("version") not in {"1.2", VERSION}
            or not isinstance(history.get("conversations"), dict)):
        raise ValueError("历史格式/规则版本不符，禁止静默重置")
    reprocess_all = history.get("version") != VERSION
    for cid, entry in history["conversations"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("message_hashes"), dict) or not entry.get("imported_at") or not re.fullmatch(r"[0-9a-f]{64}", entry.get("message_hash", "")):
            raise ValueError("历史对话记录损坏")
    outputs = []
    for kind in ("knowledge", "memory"):
        candidate_path = path.parent / f"{kind}_candidates.jsonl"
        raw = candidate_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != history.get("candidate_files", {}).get(candidate_path.name):
            raise ValueError("历史候选缺失或已被修改，不能跳过已处理数据")
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        for row in rows:
            if (row.get("candidate_id") != f"{kind}:{content_hash(row, kind)}" or row.get("content_hash") != content_hash(row, kind)
                    or row.get("claim_status") not in CLAIM_STATUSES or row.get("source_role") not in SOURCE_ROLES
                    or not isinstance(row.get("source_refs"), list) or not row["source_refs"]
                    or (kind == "memory" and row["source_role"] != "USER_SELF")):
                raise ValueError("历史候选结构损坏")
        outputs.append(rows)
    return history["conversations"], outputs[0], outputs[1], reprocess_all


def source_parser():
    """Reuse only the existing parser's pure in-memory conversation function."""
    parser_file = SOURCE_PARSER_DIR / "chatgpt_source_parser.py"
    if not parser_file.is_file():
        raise ValueError(f"缺少随 Skill 分发的 ChatGPT Source Parser: {parser_file}")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE_PARSER_DIR))
    import chatgpt_source_parser as parser  # type: ignore[import-not-found]
    return parser


def input_files(path: Path) -> list[Path]:
    if path.is_file() and re.fullmatch(r"conversations(?:-\d+)?\.json", path.name):
        return [path]
    if path.is_dir():
        shards = sorted(path.glob("conversations-*.json"))
        single = path / "conversations.json"
        files = ([single] if single.is_file() else []) + shards
        if files:
            return files
    raise ValueError("输入必须是含 conversations.json 或 conversations-*.json 的 ChatGPT Export 路径")


def knowledge_candidates(ir: dict, texts: list[tuple[str, str]], category: str | None) -> list[dict]:
    rows = [row for message in texts if (row := candidate(ir, [message], category, "knowledge"))]
    return merge_candidates(rows, "knowledge")


def validate_output(input_path: Path, output: Path) -> None:
    source_dir = input_path if input_path.is_dir() else input_path.parent
    if output.exists():
        raise ValueError(f"输出路径已存在，拒绝覆盖: {output}")
    if output == PROJECT or PROJECT not in output.parents:
        raise ValueError("输出必须是 AI数据治理 项目下一个新的子目录")
    if source_dir == output or source_dir in output.parents or output in source_dir.parents:
        raise ValueError("输出与原始导出路径重叠")


def user_texts(ir: dict) -> list[tuple[str, str]]:
    rows = []
    for m in ir["main_path"]:
        if m.get("role") != "user" or m.get("hidden"):
            continue
        content = m.get("content") or {}
        if content.get("content_type") not in {"text", "multimodal_text"}:
            continue
        text = " ".join(p for p in content.get("parts", []) if isinstance(p, str)).strip()
        if text:
            rows.append((m.get("message_id") or m["node_id"], text))
    return rows


def classify(title: str, texts: list[tuple[str, str]]) -> str | None:
    title = title.lower()
    body = " ".join(text[:1200] for _, text in texts[:3] if len(text) <= 2000).lower()
    if (any(cue in title for cue in CATEGORIES["E"][1]) or title.startswith(("生成", "查询"))) and body and not SELF_REPORT.search(body):
        return "E"
    title_scores = {key: sum(cue in title for cue in cues)
              for key, (_, cues) in CATEGORIES.items() if key != "E"}
    title_best = max(title_scores.values())
    if title_best:
        winners = [key for key, score in title_scores.items() if score == title_best]
        if len(winners) == 1 and any(cue in body for cue in CATEGORIES[winners[0]][1]):
            return winners[0]
        return None
    if "建议" in title or "怎么" in title:
        return "E" if body and not SELF_REPORT.search(body) else None
    scores = {key: sum(cue in body for cue in cues if cue not in {"ai", "产品", "训练"})
              for key, (_, cues) in CATEGORIES.items() if key != "E"}
    best = max(scores.values())
    winners = [key for key, score in scores.items() if score == best]
    if best >= 2 and len(winners) == 1:
        return winners[0]
    return "E" if best == 0 and 0 < len(texts) <= 2 and not SELF_REPORT.search(body) else None


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？\r\n]+", text) if s.strip()]


def safe_message(text: str) -> bool:
    return 20 <= len(text) <= 3000 and not SENSITIVE.search(text) and not re.search(r"```|<script|^#{1,3} |^\s*\{", text, re.M)


def candidate(ir: dict, texts: list[tuple[str, str]], category: str | None, kind: str) -> dict | None:
    if category not in {"A", "B", "C", "D"}:
        return None
    for message_id, text in texts:
        if not safe_message(text) or not SELF_REPORT.search(text):
            continue
        parts = sentences(text)
        source = {"conversation_id": ir["conversation_id"], "message_id": message_id,
                  "raw_ref": ir["raw_ref"], "message_hash": digest(text)}
        if kind == "knowledge" and category in {"A", "B", "C"}:
            method = next((s for s in parts if METHOD.search(s) and len(s) <= 240), None)
            problem = next((s for s in parts if re.search(r"问题|需求|困难|想要|不知道|怎么", s) and len(s) <= 240), None)
            if method and problem and method != problem:
                return {"title": ir.get("title_raw") or "未命名对话", "problem": problem,
                        "method": method, "source": source, "confidence": "NEEDS_REVIEW",
                        "source_role": source_role(text), "claim_status": claim_status(problem + "。" + method, source_role(text), text)}
    return None


def memory_candidates(ir: dict, texts: list[tuple[str, str]], category: str | None) -> list[dict]:
    if category == "E":
        return []
    rows = []
    for message_id, text in texts:
        if not safe_message(text) or source_role(text) != "USER_SELF":
            continue
        parts = sentences(text)
        for event in parts:
            if len(event) > 240 or not (MEMORY.search(event) or CAREER_CHANGE.search(event) or LONG_PROJECT.search(event)):
                continue
            if source_role(event) != "USER_SELF":
                continue
            topics = {topic for topic, pattern in {**MEMORY_TOPICS, "career_change": CAREER_CHANGE, "long_project": LONG_PROJECT}.items() if pattern.search(event)}
            if not topics:
                continue
            rows.append({"time": None, "event": event,
                             "impact": None,
                             "source": {"conversation_id": ir["conversation_id"],
                                        "message_id": message_id, "raw_ref": ir["raw_ref"], "message_hash": digest(text)},
                             "confidence": "NEEDS_REVIEW", "source_role": "USER_SELF",
                             "claim_status": claim_status(event, "USER_SELF", text)})
    return merge_candidates(rows, "memory")


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(input_path: Path, output: Path, history_path: Path | None = None) -> dict:
    input_path, output = input_path.resolve(), output.resolve()
    files = input_files(input_path)
    validate_output(input_path, output)
    history, knowledge, memories, reprocess_all = load_history(history_path)
    initial_ids = {row["candidate_id"] for row in knowledge + memories}
    imported_at = datetime.now(timezone.utc).isoformat()
    parser = source_parser()
    logger = logging.getLogger("chatgpt_import_pipeline")
    inventory, anomalies, hashes = [], [], []
    seen = set()
    message_count = 0
    processed = skipped = 0
    for path in files:
        raw = path.read_bytes()
        hashes.append({"file": path.name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"{path.name}: 顶层必须是对话数组")
        for index, conv in enumerate(data):
            try:
                ir = parser.chatgpt_parse_conversation(conv, path.name, index, logger)
                cid = ir["conversation_id"]
                if cid in seen:
                    raise ValueError("conversation_id 重复")
                seen.add(cid)
                texts = user_texts(ir)
                category = classify(ir.get("title_raw") or "", texts)
                message_count += ir["tree_meta"]["message_nodes"]
                inventory.append({"conversation_id": cid, "title": ir.get("title_raw"),
                                  "date": ir.get("create_time"),
                                  "message_count": ir["tree_meta"]["message_nodes"],
                                  "attachment_count": len(ir["asset_file_ids"]),
                                  "initial_classification": category,
                                  "source": ir["raw_ref"]})
                message_hash = digest(conv)
                if not reprocess_all and history.get(cid, {}).get("message_hash") == message_hash:
                    skipped += 1
                    continue
                local_knowledge = knowledge_candidates(ir, texts, category)
                local_memories = memory_candidates(ir, texts, category)
                history[cid] = {"message_hash": message_hash,
                                "message_hashes": {m.get("message_id") or m["node_id"]: digest(m) for m in ir["main_path"]},
                                "imported_at": imported_at}
                knowledge.extend(local_knowledge)
                memories.extend(local_memories)
                processed += 1
            except (parser.CSPError, ValueError, KeyError, TypeError) as error:
                anomalies.append(f"{path.name}[{index}]: {type(error).__name__}，NEEDS_REVIEW")
    knowledge = merge_candidates(knowledge, "knowledge")
    memories = merge_candidates(memories, "memory")
    new_count = len({row["candidate_id"] for row in knowledge + memories} - initial_ids)
    counts = Counter(row["initial_classification"] for row in inventory)
    labels = "\n".join(f"- {key} {name}：{counts[key]}" for key, (name, _) in CATEGORIES.items())
    report = ("# 分类报告\n\n" + labels + f"\n- NEEDS_REVIEW（未归类）：{counts[None]}\n\n"
              "分类依据标题和可见用户消息，仅是初分；候选不是正式知识或个人事实。\n")
    migration_note = "- 检测到 V1.2 历史，已重新评估本次输入内的全部对话。\n" if reprocess_all else ""
    audit = (
        "# IMPORT_REPORT\n\n"
        f"- 输入文件名：{input_path.name}\n- 分片：{len(files)}；对话：{len(inventory)}；消息节点：{message_count}\n"
        + "- 分类：" + ", ".join(f"{key}={counts[key]}" for key in CATEGORIES) + f"；NEEDS_REVIEW={counts[None]}\n"
        + f"- 知识候选：{len(knowledge)}；个人记忆候选：{len(memories)}；异常：{len(anomalies)}\n"
        + f"- V{VERSION}：处理对话 {processed}；未变化跳过 {skipped}；新增候选 {new_count}；输出候选为累计快照。\n"
        + migration_note
        + "- PLAN/HYPOTHESIS/UNKNOWN 不能作为个人事实；FACT/CURRENT 也只是用户陈述的规则判断，须人工审核。\n"
        + "- 原件、附件与 Source Layer：只读；正式知识库：未写入；删除/移动：无。\n"
        + "- 风险：关键词分类和片段抽取可能误判或遗漏；全部候选须人工核对来源。\n\n"
        + "## 输入文件核对\n\n" + "\n".join(f"- {h['file']}：{h['size_bytes']} bytes；SHA-256 {h['sha256']}" for h in hashes)
        + "\n\n## 异常\n\n" + ("\n".join(f"- {e}" for e in anomalies) if anomalies else "无") + "\n"
    )
    output.mkdir(parents=True)
    write_json(output / "conversation_inventory.json", inventory)
    (output / "classification_report.md").write_text(report, encoding="utf-8")
    write_jsonl(output / "knowledge_candidates.jsonl", knowledge)
    write_jsonl(output / "memory_candidates.jsonl", memories)
    (output / "IMPORT_REPORT.md").write_text(audit, encoding="utf-8")
    # History is the completion marker; failed output cannot be used as a successful import.
    write_json(output / "import_history.json", {"version": VERSION, "imported_at": imported_at,
               "previous_history": history_path.name if history_path else None,
               "conversations": history, "candidate_files": {
                   f"{kind}_candidates.jsonl": hashlib.sha256((output / f"{kind}_candidates.jsonl").read_bytes()).hexdigest()
                   for kind in ("knowledge", "memory")}})
    return {"conversations": len(inventory), "messages": message_count, "counts": dict(counts),
            "knowledge_candidates": len(knowledge), "memory_candidates": len(memories),
            "anomalies": len(anomalies), "processed": processed, "skipped": skipped,
            "new_candidates": new_count, "output": str(output)}


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("export", type=Path, help="ChatGPT Export 目录或 conversations*.json")
    args.add_argument("--output", required=True, type=Path, help="项目内新的输出子目录")
    args.add_argument("--history", type=Path, help="上次完整输出中的 import_history.json；不提供则全量读取")
    parsed = args.parse_args()
    try:
        result = run(parsed.export, parsed.output, parsed.history)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
