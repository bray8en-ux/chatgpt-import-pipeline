# chatgpt_import_pipeline · ChatGPT Export 候选整理 V1.3

本目录含 `SKILL.md`、解析脚本、分类规则、随附的纯内存解析器和测试。仅处理用户明确指定的 ChatGPT Export；不会自动触发、删除数据或写入知识库。人工审核页是导入后的可选阶段，不属于解析核心。

需要 Python 3.10+，只使用标准库。手动运行示例：

```powershell
python scripts/import_chatgpt.py "<ChatGPT Export 目录或 conversations.json 路径>" --output "<AI数据治理项目内尚不存在的输出子目录>"
```

脚本只在新输出目录生成 `conversation_inventory.json`、`classification_report.md`、`knowledge_candidates.jsonl`、`memory_candidates.jsonl`、`IMPORT_REPORT.md` 和 `import_history.json`；拒绝覆盖已有目录。候选全部为 `NEEDS_REVIEW`，带 `claim_status`、`source_role`、`candidate_id`、`content_hash`、`source_refs`。随附的 `vendor/chatgpt_source_parser.py` 只提供内存解析函数，不依赖个人知识库路径。

需要人工审核时，在导入输出目录运行：

```powershell
python scripts/create_review_dashboard.py "<导入输出目录>"
```

它会生成 `review_dashboard.html`、`review_manifest.json`、`start_review_bridge.py` 和 `start_review_bridge.bat`。双击输出目录内的 `start_review_bridge.bat`，审核结果会自动写入同目录 `review_result.json`，并生成用于绑定当前候选快照的 `review_result.meta.json`。桥接只允许页面和经过快照校验的审核接口通过本机回环访问，不直接提供原始审核 JSON 下载；只写审核结果文件及其快照标记，不修改候选、原始数据或正式知识库。已有审核结果会先保留为 `review_result.previous.json`。

下次增量处理使用上一份完整输出（连同候选文件保留）的历史：

```powershell
python -B scripts/import_chatgpt.py "<ChatGPT Export 路径>" --output "<项目内新目录>" --history "<上次输出目录>/import_history.json"
```

重复对话跳过提取，改变的对话重新处理，候选输出为累计快照；旧文件不改。未提供 `--history` 时全量读取。仅 USER_SELF 产生个人记忆，计划/假设/未知不能当事实。判定仍是保守规则，未标注的引用与同义重复不保证识别，具体边界见 `rules/classification_rules.md`。

使用 V1.2 的导入历史升级到 V1.3 时，本次输入中的对话会重新评估一次，补入更新规则发现的候选；原输出目录不会被修改。

测试：在本目录运行 `python -m unittest discover -s tests -v`。输出可能含用户对话标题、来源引用和哈希，默认只应在本地使用；不要把真实导入输出提交到公开仓库。完整边界见项目根目录的 `README.md` 与 `PRIVACY.md`。本期不删除、不修改原始数据、不写正式知识库、不支持其他来源。
