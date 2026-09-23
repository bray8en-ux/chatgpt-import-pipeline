"""Small, read-only ChatGPT Export conversation parser.

This module intentionally exposes only the in-memory parser needed by the
pipeline. It does not resolve paths, inspect a knowledge base, or write files.
"""

from __future__ import annotations

import logging


class CSPError(Exception):
    """Raised when a ChatGPT conversation cannot be parsed safely."""


CHATGPT_POINTER_PREFIXES = ("sediment://file_", "file-service://file_")


def chatgpt_extract_file_ids(obj, out: list[str] | None = None) -> list[str]:
    """Collect attachment pointers from a message content tree."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for value in obj.values():
            chatgpt_extract_file_ids(value, out)
    elif isinstance(obj, list):
        for value in obj:
            chatgpt_extract_file_ids(value, out)
    elif isinstance(obj, str) and obj.startswith(CHATGPT_POINTER_PREFIXES):
        file_id = obj.split("://", 1)[1]
        if file_id not in out:
            out.append(file_id)
    return out


def chatgpt_node_time(node: dict):
    value = (node.get("message") or {}).get("create_time")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def chatgpt_message_ir(node_id: str, parent_id, msg: dict) -> dict:
    author = msg.get("author") or {}
    metadata = msg.get("metadata") or {}
    content = msg.get("content") or {}
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "message_id": msg.get("id"),
        "role": author.get("role"),
        "create_time": msg.get("create_time"),
        "content_type": content.get("content_type") if isinstance(content, dict) else None,
        "content": content,
        "asset_file_ids": chatgpt_extract_file_ids(content),
        "model_slug": metadata.get("model_slug"),
        "hidden": bool(metadata.get("is_visually_hidden_from_conversation")),
    }


def chatgpt_build_tree(mapping: dict) -> dict:
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    broken: list[str] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            raise CSPError(f"mapping 节点 {node_id!r} 不是对象")
        parent = node.get("parent")
        if parent is None:
            roots.append(node_id)
        elif parent not in mapping:
            broken.append(node_id)
        else:
            children.setdefault(parent, []).append(node_id)
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, ()))
    return {
        "children": children,
        "roots": roots,
        "broken_parent_nodes": broken,
        "branch_points": sum(1 for siblings in children.values() if len(siblings) >= 2),
        "unreachable_nodes": [node_id for node_id in mapping if node_id not in seen],
    }


def chatgpt_backtrack(node_id: str, mapping: dict) -> tuple[list[str], bool, bool]:
    chain: list[str] = []
    seen: set[str] = set()
    current = node_id
    hit_cycle = False
    while current is not None and current in mapping:
        if current in seen:
            hit_cycle = True
            break
        seen.add(current)
        chain.append(current)
        current = mapping[current].get("parent")
    chain.reverse()
    return chain, current is None, hit_cycle


def chatgpt_recover_main_path(mapping: dict, tree: dict, current_node,
                              conversation_id: str, logger: logging.Logger) -> dict:
    result = {"path": [], "method": None, "current_node_state": None, "notes": []}
    if current_node is None:
        state = "missing"
    elif current_node not in mapping:
        state = "dangling"
    else:
        chain, complete, hit_cycle = chatgpt_backtrack(current_node, mapping)
        if hit_cycle:
            state = "cycle"
        else:
            result.update({"path": chain, "method": "current_node", "current_node_state": "ok"})
            if not complete:
                note = "current_node 回溯在断 parent 处截断,主路径不完整"
                result["notes"].append(note)
                logger.warning("[%s] %s(保留 %d 节点)", conversation_id, note, len(chain))
            return result
    result["current_node_state"] = state
    children = tree["children"]

    def leaf_key(node_id: str):
        value = chatgpt_node_time(mapping[node_id])
        return (-(value if value is not None else float("-inf")), node_id)

    leaves = sorted((node_id for node_id in mapping if not children.get(node_id)), key=leaf_key)
    for node_id in leaves:
        chain, complete, hit_cycle = chatgpt_backtrack(node_id, mapping)
        if complete and not hit_cycle:
            result["path"] = chain
            result["method"] = "fallback_deepest_leaf"
            result["notes"].append(f"current_node {state},兜底改取最深叶子 {node_id} 回溯")
            logger.warning("[%s] %s", conversation_id, result["notes"][-1])
            return result
    if not any(node.get("message") is not None for node in mapping.values()):
        result["method"] = "empty"
        result["notes"].append("无任何消息节点,主路径为空")
        logger.warning("[%s] 空对话:无消息节点", conversation_id)
        return result
    raise CSPError(f"[{conversation_id}] 主路径不可恢复: current_node {state}")


def chatgpt_classify_reason(node_id: str, mapping: dict, children: dict) -> str:
    node = mapping[node_id]
    role = ((node.get("message") or {}).get("author") or {}).get("role")
    parent = node.get("parent")
    siblings = children.get(parent, []) if parent in mapping else []
    same_role = sum(
        1 for sibling in siblings
        if (((mapping[sibling].get("message") or {}).get("author") or {}).get("role")) == role
    )
    if role == "assistant" and same_role >= 2:
        return "regenerate"
    if role == "user" and same_role >= 2:
        return "edit"
    return "fork"


def chatgpt_parse_conversation(conv: dict, shard_file: str, index_in_shard: int,
                               logger: logging.Logger) -> dict:
    """Parse one export conversation into an in-memory IR without writing files."""
    if not isinstance(conv, dict):
        raise CSPError(f"{shard_file}[{index_in_shard}] conversation 不是对象")
    conversation_id = conv.get("conversation_id") or conv.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise CSPError(f"{shard_file}[{index_in_shard}] conversation 缺少 conversation_id/id")
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict):
        raise CSPError(f"[{conversation_id}] mapping 缺失或不是对象")
    tree = chatgpt_build_tree(mapping)
    recovered = chatgpt_recover_main_path(mapping, tree, conv.get("current_node"), conversation_id, logger)
    path_set = set(recovered["path"])
    main_path = [
        chatgpt_message_ir(node_id, mapping[node_id].get("parent"), mapping[node_id]["message"])
        for node_id in recovered["path"]
        if mapping[node_id].get("message") is not None
    ]

    def alternative_key(node_id: str):
        value = chatgpt_node_time(mapping[node_id])
        return ((value if value is not None else float("-inf")), node_id)

    alternatives = []
    for node_id in sorted(
        (node_id for node_id in mapping
         if node_id not in path_set and mapping[node_id].get("message") is not None),
        key=alternative_key,
    ):
        node = mapping[node_id]
        alternatives.append({
            "node_id": node_id,
            "parent_id": node.get("parent"),
            "reason": chatgpt_classify_reason(node_id, mapping, tree["children"]),
            "message": chatgpt_message_ir(node_id, node.get("parent"), node["message"]),
        })
    message_nodes = sum(1 for node in mapping.values() if node.get("message") is not None)
    if len(main_path) + len(alternatives) != message_nodes:
        raise CSPError(f"[{conversation_id}] 守恒校验失败")
    asset_file_ids: list[str] = []
    for message in (*main_path, *(item["message"] for item in alternatives)):
        for file_id in message["asset_file_ids"]:
            if file_id not in asset_file_ids:
                asset_file_ids.append(file_id)
    return {
        "conversation_id": conversation_id,
        "title_raw": conv.get("title") if isinstance(conv.get("title"), str) else None,
        "create_time": conv.get("create_time"),
        "update_time": conv.get("update_time"),
        "default_model_slug": conv.get("default_model_slug"),
        "flags": {
            "archived": bool(conv.get("is_archived")),
            "starred": bool(conv.get("is_starred")),
            "do_not_remember": bool(conv.get("is_do_not_remember")),
            "study_mode": bool(conv.get("is_study_mode")),
            "read_only": bool(conv.get("is_read_only")),
        },
        "tree_meta": {
            "node_count": len(mapping),
            "message_nodes": message_nodes,
            "scaffold_nodes": len(mapping) - message_nodes,
            "current_node": conv.get("current_node"),
            "current_node_state": recovered["current_node_state"],
            "recovery": recovered["method"],
            "branch_points": tree["branch_points"],
            "roots": len(tree["roots"]),
            "broken_parent_nodes": len(tree["broken_parent_nodes"]),
            "unreachable_nodes": len(tree["unreachable_nodes"]),
            "conservation_ok": True,
            "notes": recovered["notes"],
        },
        "main_path": main_path,
        "alternatives": alternatives,
        "asset_file_ids": asset_file_ids,
        "raw_ref": {"shard_file": shard_file, "index_in_shard": index_in_shard},
    }
