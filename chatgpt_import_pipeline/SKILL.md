---
name: chatgpt_import_pipeline
description: 仅在用户明确指定 ChatGPT Export 与候选输出位置时，生成带陈述状态、来源和合并记录的本地待审候选；可选生成同目录人工审核页与本地桥接，不删除或写正式知识库。
---

# ChatGPT Export 候选整理

本 Skill 是一个可独立复制运行的 V1.3 目录，不代表安装或自动触发。它只依赖本目录 `vendor/chatgpt_source_parser.py` 的标准库解析副本，不依赖个人知识库或其他本地绝对路径。

只接受用户明确指定的 ChatGPT Export 路径与新的输出目录。先确认目录和文件范围，再运行 `scripts/import_chatgpt.py`。仅生成清单、五类初分、知识与个人记忆候选、执行报告；不把候选当事实或正式资产。对无法确认的条目标 `NEEDS_REVIEW`，最后交用户审核并停止。

禁止调用 Source Layer 的写入命令、修改原件、移动或删除文件、写入正式知识库、扩展到其他平台。分类口径见 `rules/classification_rules.md`。

V1.3：仅 `USER_SELF` 能进入个人记忆候选；`PLAN/HYPOTHESIS/UNKNOWN` 不能当成个人事实，`FACT/CURRENT` 也需人工核对。相同事件会合并并保留所有来源，不同事件不会因为主题相同而互相覆盖。V1.2 历史会在首次升级时重评本次输入中的全部对话。

首次运行输出六个文件（原五份文件加 `import_history.json`）。增量运行在原命令中加 `--history <上次完整输出/import_history.json>`，继续使用新输出目录；累计候选和历史只写新快照，旧输出保留。未提供历史则全量读取，不能声称已做跨批次去重。历史损坏或配套候选缺失时停止并报告，不自动重置历史。

## 可选人工审核阶段

导入完成后，若用户要求查看并审核候选，在同一个输出目录运行：

```powershell
python scripts/create_review_dashboard.py "<上次导入输出目录>"
```

该命令只读取 `conversation_inventory.json`、`knowledge_candidates.jsonl` 和 `memory_candidates.jsonl`，并在输出目录生成 `review_dashboard.html`、`review_manifest.json`、`start_review_bridge.py` 和 `start_review_bridge.bat`。用户双击 `start_review_bridge.bat` 后，审核结果才会写入或覆盖同目录的 `review_result.json`；覆盖前保留 `review_result.previous.json`，并用 `review_result.meta.json` 绑定当前候选快照。候选改变后，旧结果不会自动套用。

审核页不展示 hash、UUID、message_id、原始路径等技术字段。桥接服务只提供审核页与受限审核接口，不提供整个输出目录或原始审核 JSON 下载；它要求审核结果中的候选 ID、类别、目标与当前 `review_manifest.json` 一致，同时校验候选文件未被改动；不修改候选、原始导出或正式知识库。审核结果只是人工意见，没有新的明确授权，不得据此自动删除、移动、覆盖或正式入库。直接双击 HTML 时只能使用下载备用方式。
