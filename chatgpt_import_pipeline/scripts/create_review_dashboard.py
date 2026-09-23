"""Create a local human-review page beside one import output directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


STATUS_LABELS = {
    "FACT": "已发生",
    "CURRENT": "当前状态",
    "PLAN": "计划",
    "HYPOTHESIS": "假设",
    "QUOTE": "引用",
    "UNKNOWN": "待核对",
}
TARGET_LABELS = {"knowledge": "知识资产", "memory": "个人记忆"}


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 数据治理审核工作台</title>
<style>
:root{--paper:#f4f5f2;--card:#fff;--ink:#263d38;--muted:#68756e;--line:#dbe3dc;--green:#24634f;--pale:#e8f1eb;--red:#a14f46;--amber:#896a28}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.75 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}button{font:inherit;cursor:pointer}button:focus-visible{outline:3px solid #d3a13d;outline-offset:3px}.shell{max-width:1080px;margin:auto;padding:32px 24px 56px}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:24px}.eyebrow{font-size:12px;letter-spacing:.12em;color:var(--green);font-weight:700}.top h1{font-size:clamp(28px,5vw,42px);line-height:1.3;letter-spacing:-.04em;margin:12px 0}.intro{max-width:680px;color:var(--muted)}.status{padding:14px 17px;border:1px solid #b9d2c0;background:var(--pale);border-radius:12px;color:var(--green);white-space:nowrap}.status strong{display:block;font-size:18px;margin-top:4px}.metrics{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.metric{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 17px;min-width:150px}.metric b{display:block;color:var(--green);font-size:25px}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:20px 0 8px}.toolbar button{border-radius:9px;padding:9px 14px}.save{border:0;background:var(--green);color:#fff}.download{border:1px solid #78a98a;background:#fff;color:var(--green)}button:disabled{background:#cbd4cd;color:#667169;border-color:#cbd4cd;cursor:not-allowed}.count{font-size:13px;color:var(--muted)}.note{font-size:13px;color:var(--muted);margin:0 0 24px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:23px;display:flex;flex-direction:column}.card[data-choice=keep]{border-color:#86b399}.card[data-choice=delete]{border-color:#d1a49d}.tags{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.tag,.state{font-size:11px;padding:3px 9px;border-radius:5px;background:#edf1eb;color:#42604f}.state{background:#f5efdd;color:var(--amber)}.card h2{font-size:20px;line-height:1.45;margin:15px 0 19px}.block{margin-bottom:16px}.label{font-size:11px;color:var(--muted);font-weight:700;margin:0 0 4px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.method{border-left:2px solid #cfdbd1;padding-left:11px;color:#53665b;white-space:pre-wrap}.source{font-size:12px;color:#5e7067}.source time{color:#8c968e;margin-left:7px}.reason{color:#65736a;font-size:13px}.review{border-top:1px solid var(--line);padding-top:14px;margin-top:auto}.choices{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.choice{border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--muted);padding:8px 5px}.choice[aria-pressed=true]{background:var(--pale);border-color:#79a98b;color:var(--green);font-weight:650}.choice[data-value=delete][aria-pressed=true]{background:#f8ebe8;border-color:#c69289;color:var(--red)}.choice[data-value=hold][aria-pressed=true]{background:#f7f0dc;border-color:#c7b37b;color:var(--amber)}.footer{font-size:12px;color:var(--muted);border-top:1px solid var(--line);margin-top:28px;padding-top:17px}@media(max-width:760px){.shell{padding:22px 15px 42px}.top{display:block}.status{display:inline-block;margin-top:17px}.grid{grid-template-columns:1fr}.card{padding:20px}}
</style>
</head>
<body><main class="shell">
<header class="top"><div><div class="eyebrow">CHATGPT EXPORT · 人工审核</div><h1>把值得留下的，交给自己决定。</h1><p class="intro">候选只是一条待核对线索。请确认它是否准确、是否值得保留；审核选择不会写入正式知识库。</p></div><div class="status"><span>状态</span><strong>等待人工审核</strong></div></header>
<section class="metrics" aria-label="本次导入概览"><div class="metric"><span>分析的对话</span><b id="conversation-count">__CONVERSATIONS__</b></div><div class="metric"><span>发现的候选</span><b id="candidate-count">0</b></div><div class="metric"><span>等待审核</span><b id="pending-count">0</b></div></section>
<div class="toolbar"><button id="save" class="save" type="button" disabled>保存到同目录</button><button id="download" class="download" type="button" disabled>下载审核结果</button><span id="count" class="count">尚未选择审核结果</span></div>
<p class="note">用输出目录里的启动脚本打开时，审核结果会自动保存为同目录的 review_result.json；直接双击 HTML 时使用下载备用方式。</p>
<section id="cards" class="grid" aria-label="候选列表"></section>
<footer class="footer">保留＝进入后续人工加工；删除＝本次不采纳；待定＝暂不决定。任何选择都不会删除文件、修改原始导出或写入正式知识库。</footer>
</main>
<script id="review-data" type="application/json">__REVIEW_DATA__</script>
<script>
"use strict";
const records=JSON.parse(document.getElementById("review-data").textContent),storageKey="ai-data-governance-review-v1:"+"__SNAPSHOT_ID__";
const choices={},choiceTimes={};
let staleReview=false;
try{const saved=JSON.parse(localStorage.getItem(storageKey)||"{}");if(saved&&typeof saved==="object")for(const [id,value] of Object.entries(saved))if(["keep","delete","hold"].includes(value))choices[id]=value;const times=JSON.parse(localStorage.getItem(storageKey+"-times")||"{}");if(times&&typeof times==="object")Object.assign(choiceTimes,times)}catch(e){}
const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node};
function chosen(){return records.filter(r=>choices[r.candidate_id])}
function render(){
 const selected=chosen();document.getElementById("candidate-count").textContent=records.length;document.getElementById("pending-count").textContent=records.length-selected.length;document.getElementById("count").textContent=staleReview?"发现旧版审核结果，已隔离；请重新核对本次候选。":selected.length?"已选择 "+selected.length+" 条，可继续调整。":"尚未选择审核结果";document.getElementById("save").disabled=!selected.length;document.getElementById("download").disabled=!selected.length;
 const grid=document.getElementById("cards");grid.replaceChildren();
 for(const r of records){const card=el("article","card");card.dataset.choice=choices[r.candidate_id]||"";const tags=el("div","tags");tags.append(el("span","tag",r.category),el("span","state","状态 · "+r.status));card.append(tags,el("h2","",r.title));
  const what=el("div","block");what.append(el("p","label","是什么"),el("p","text",r.what));if(r.method)what.append(el("p","method","讨论中的做法："+r.method));card.append(what);
  const reason=el("div","block");reason.append(el("p","label","为什么推荐"),el("p","reason",r.reason));card.append(reason);
  const source=el("div","block");source.append(el("p","label","来源"));for(const item of r.sources){const line=el("p","source",item.title);if(item.date)line.append(el("time","",item.date));source.append(line)}card.append(source);
  const review=el("div","review");review.append(el("p","label","人工操作"));const buttons=el("div","choices");for(const [value,label] of [["keep","保留"],["delete","删除"],["hold","待定"]]){const button=el("button","choice",label);button.type="button";button.dataset.value=value;button.setAttribute("aria-pressed",String(choices[r.candidate_id]===value));button.addEventListener("click",()=>{if(choices[r.candidate_id]===value){delete choices[r.candidate_id];delete choiceTimes[r.candidate_id]}else{choices[r.candidate_id]=value;choiceTimes[r.candidate_id]=new Date().toISOString()}try{localStorage.setItem(storageKey,JSON.stringify(choices));localStorage.setItem(storageKey+"-times",JSON.stringify(choiceTimes))}catch(e){}render()});buttons.append(button)}review.append(buttons);card.append(review);grid.append(card)
 }
}
function result(){return chosen().map(r=>({candidate_id:r.candidate_id,decision:choices[r.candidate_id],category:r.category,target:r.target,timestamp:choiceTimes[r.candidate_id]||new Date().toISOString()}))}
function text(){return JSON.stringify(result(),null,2)}
async function save(){const status=document.getElementById("count"),payload=text();try{if(location.protocol==="http:"&&location.hostname==="127.0.0.1"){const response=await fetch("/save-review",{method:"POST",headers:{"Content-Type":"application/json"},body:payload});if(!response.ok)throw new Error("bridge save failed");status.textContent="已自动保存到本页所在目录，共 "+result().length+" 条。";return}download();status.textContent="已下载审核结果，请将文件放回本目录。"}catch(e){status.textContent="保存未完成，请确认桥接服务正在运行。"}}
function download(){const url=URL.createObjectURL(new Blob([text()],{type:"application/json;charset=utf-8"})),link=document.createElement("a");link.href=url;link.download="review_result.json";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function init(){if(location.protocol==="http:"&&location.hostname==="127.0.0.1"){try{const response=await fetch("/load-review",{cache:"no-store"});if(response.ok){const saved=await response.json();if(saved.stale)staleReview=true;else for(const row of saved.results)if(records.some(r=>r.candidate_id===row.candidate_id)&&["keep","delete","hold"].includes(row.decision)){choices[row.candidate_id]=row.decision;choiceTimes[row.candidate_id]=row.timestamp||new Date().toISOString()}}}catch(e){}}render()}
document.getElementById("save").addEventListener("click",save);document.getElementById("download").addEventListener("click",()=>{download();document.getElementById("count").textContent="已下载审核结果，共 "+result().length+" 条。"});init();
</script></body></html>'''


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} 不是 JSON 对象")
        rows.append(value)
    return rows


def read_inventory(output: Path) -> dict[str, dict]:
    path = output / "conversation_inventory.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("conversation_id")): row for row in data if isinstance(row, dict) and row.get("conversation_id")}


def source_items(row: dict, inventory: dict[str, dict]) -> list[dict[str, str]]:
    refs = row.get("source_refs") or [row.get("source") or {}]
    result = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        info = inventory.get(str(ref.get("conversation_id")), {})
        item = {"title": str(info.get("title") or "原始 ChatGPT 对话")}
        if info.get("date"):
            item["date"] = str(info["date"])
        if item not in result:
            result.append(item)
    return result or [{"title": "原始 ChatGPT 对话"}]


def normalize(row: dict, target: str, inventory: dict[str, dict]) -> dict:
    candidate_id = row.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("候选缺少 candidate_id")
    status = row.get("claim_status") if row.get("claim_status") in STATUS_LABELS else "UNKNOWN"
    if target == "knowledge":
        title = str(row.get("title") or "未命名知识候选")
        what = str(row.get("problem") or "未提供问题描述")
        method = str(row.get("method") or "")
    else:
        title = str(row.get("event") or "未命名个人记忆候选")
        what = str(row.get("event") or "未提供事件描述")
        method = ""
    reason = "来自 ChatGPT Export 的候选线索，需结合原文确认是否值得保留。"
    if status in {"PLAN", "HYPOTHESIS", "QUOTE", "UNKNOWN"}:
        reason = "这条候选含有计划、假设、引用或待核对信息，不能直接当作已确认事实。"
    return {"candidate_id": candidate_id, "title": title, "what": what, "method": method,
            "reason": reason, "sources": source_items(row, inventory),
            "category": TARGET_LABELS[target], "target": target,
            "status": STATUS_LABELS[status], "confidence": str(row.get("confidence") or "NEEDS_REVIEW")}


def build_records(output: Path) -> list[dict]:
    inventory = read_inventory(output)
    rows = []
    seen = set()
    for target in ("knowledge", "memory"):
        path = output / f"{target}_candidates.jsonl"
        if not path.is_file():
            raise ValueError(f"缺少 {path.name}")
        for row in read_jsonl(path):
            item = normalize(row, target, inventory)
            if item["candidate_id"] in seen:
                raise ValueError(f"candidate_id 重复: {item['candidate_id']}")
            seen.add(item["candidate_id"])
            rows.append(item)
    return rows


def write_text(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"输出已存在，拒绝覆盖: {path.name}；如确认重建请加 --force")
    path.write_text(text, encoding="utf-8", newline="\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(output: Path, records: list[dict], force: bool) -> None:
    candidate_files = {
        name: file_sha256(output / name)
        for name in ("knowledge_candidates.jsonl", "memory_candidates.jsonl")
    }
    snapshot_id = hashlib.sha256(json.dumps(candidate_files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest = {
        "version": 1,
        "candidate_files": candidate_files,
        "snapshot_id": snapshot_id,
        "candidates": [
            {key: row[key] for key in ("candidate_id", "category", "target")}
            for row in records
        ],
    }
    write_text(output / "review_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", force)


def run(output: Path, force: bool = False) -> dict:
    output = output.resolve()
    if not output.is_dir():
        raise ValueError(f"输出目录不存在: {output}")
    records = build_records(output)
    write_manifest(output, records, force)
    manifest = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
    data = json.dumps(records, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    inventory = json.loads((output / "conversation_inventory.json").read_text(encoding="utf-8")) if (output / "conversation_inventory.json").is_file() else []
    html = (HTML_TEMPLATE.replace("__REVIEW_DATA__", data)
            .replace("__CONVERSATIONS__", str(len(inventory)))
            .replace("__SNAPSHOT_ID__", manifest["snapshot_id"]))
    write_text(output / "review_dashboard.html", html, force)
    bridge = Path(__file__).with_name("review_bridge.py")
    launcher = Path(__file__).with_name("start_review_bridge.bat")
    write_text(output / "start_review_bridge.py", bridge.read_text(encoding="utf-8"), force)
    write_text(output / "start_review_bridge.bat", launcher.read_text(encoding="utf-8"), force)
    return {"output": str(output), "conversations": len(inventory), "candidates": len(records),
            "review_dashboard": str(output / "review_dashboard.html"),
            "bridge": str(output / "start_review_bridge.py"),
            "manifest": str(output / "review_manifest.json")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="一次 ChatGPT 导入输出目录")
    parser.add_argument("--force", action="store_true", help="只覆盖审核页和桥接脚本，不覆盖 review_result.json")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output, args.force), ensure_ascii=False))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
