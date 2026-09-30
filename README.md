# Memory Eval v0.1

## 第一版说明

本项目包含 AML 文本/代码赛道兼容接口、SQLite/BM25 临时记忆后端、本地模拟调用脚本。
不需要 embedding、GPU 或 Eval Key，不连接 AML，不产生官方成绩。
`datasets/locomo.py` 已支持公开原始 LoCoMo；本地接口验证另使用自造数据。尚不包含 Answer 或 Judge。
后续团队的记忆模块接入 `backends/`，日常调用与 AML 协议保持一致。
多模态输入暂不支持，会明确返回 422。

启动与验证命令见下方。服务启动后，可在服务器上浏览 `/docs` 查看自动生成的接口文档。
数据库位于 data，虚拟环境、服务日志和验证报告位于 runtime，代码位于 workspace。
正式向 AML 提交前，仍需完成公网 HTTPS、鉴权、容量验证、版本申报及官方 Smoke。

AML-compatible Textual/Coding Add/Search service and independent local contract validation.
Official contract: https://agentmemoryleaderboard.ai/api-guide
This is a development baseline, not an AML official score or a Full evaluation reproduction.
Multimodal content arrays are deliberately rejected (HTTP 422).

## Server paths

- Code: `/home/xhy/workspace/memory-eval`
- Database: `/home/xhy/data/memory-eval/memory.sqlite3`
- Environment, logs and reports: `/home/xhy/runtime/memory-eval`

## Install and start

```bash
cd /home/xhy/workspace/memory-eval
python3 -m venv /home/xhy/runtime/memory-eval/venv
/home/xhy/runtime/memory-eval/venv/bin/pip install -e .
export MEMORY_DB_PATH=/home/xhy/data/memory-eval/memory.sqlite3
# Optional local authentication. Set a secret externally; do not commit it.
# export MEMORY_API_KEY=...
/home/xhy/runtime/memory-eval/venv/bin/python -m uvicorn service.app:app --host 127.0.0.1 --port 8000
```

Run from the project directory. Local binding is the default operating recommendation.
External AML evaluation requires a stable publicly reachable HTTPS deployment and approved version/key.
If port 8000 is occupied, choose another port and pass that URL to the client.

## Verify

```bash
cd /home/xhy/workspace/memory-eval
/home/xhy/runtime/memory-eval/venv/bin/python -m unittest discover -s tests -v
/home/xhy/runtime/memory-eval/venv/bin/python -m evaluation.smoke_local --base-url http://127.0.0.1:8000
```

The local client writes synthetic sample memories under a fresh `local:<uuid>` user scope.
It never contacts AML. No Eval Key is required. MEMORY_API_KEY, when configured, is the
memory service credential, not the AML Eval Key. Reports contain check results, not benchmark scores.

## API

GET `/health` is unauthenticated. POST `/add` and `/search` accept Bearer, Token or X-Api-Key
when MEMORY_API_KEY is set. Without that setting the local service is unauthenticated.

Add requires request_id, user_id, session_id and ordered messages. Each message has
user/assistant role, nonblank string content, and optional Unix millisecond timestamp.
HTTP 200 follows a committed transaction and echoes all three IDs with success=true.
Retry identity is `(user_id, request_id)`: identical normalized requests are no-ops,
different payloads return 409. Original content and order are retained; IDs survive restart.

Search requires nonblank string query, user_id and positive integer top_k; options is an
optional top-level string array. Results are user-scoped, across sessions, ranked by descending
BM25 score, capped at top_k, and returned as `{"data": [...]}` with id/content/score/created_at.
No overlap returns `{"data": []}`. Options are accepted but not used by this baseline.
Unknown fields are rejected. Search returns evidence, never a generated answer.

## Baseline and extension

SQLite stores one source message per memory. Tokenization handles English words and CJK
characters/bigrams. BM25 statistics are computed per user; all that user's rows are read per
query. Suitable for a small baseline, not yet tuned or load-tested for AML Full scale.
No embedding model, external model calls or GPU is needed. Lexical retrieval misses synonyms
and semantic paraphrases. No context-window expansion or multimodal retrieval is provided.

Implement `backends.base.MemoryBackend` for the team's future memory module and replace
backend construction in `service/app.py`; preserve persistence, retry and isolation semantics.
`datasets/locomo.py` adapts original public LoCoMo. Answer generation and answer scoring
remain future work; retrieval metrics are available as described below.
Routine application logs contain method/path/status/duration, not payloads or credentials.

## LoCoMo 本地检索评测

先启动服务（容器终端，前台）：

```bash
cd /memory-eval
/runtime/venv/bin/python -m uvicorn service.app:app --host 127.0.0.1 --port 9001
```

另一终端执行（宿主机）：

```bash
docker exec -w /memory-eval xhy-memory-eval /runtime/venv/bin/python -m evaluation.locomo_retrieval \
  --dataset /data/locomo10/locomo10.json \
  --base-url http://127.0.0.1:9001 \
  --output-root /runtime/locomo-retrieval
```

每次使用独立 run/user 范围，不覆盖现有记忆，但会新增测试数据。通过真实 HTTP Add 写入
10 段对话全部原文，再对每道题调用 Search。只将对话写入记忆，金标答案、evidence、
生成 observation/summary 不写入。说话人名称加入文本，speaker_a 映射为 user，其他说话人
映射为 assistant；源会话时间按 UTC 解析（原始文件未提供时区），消息按会话编号和来源顺序处理。
每批最多 20 条消息，此处不是 AML 的完整词数分块复现。

输出子目录包含 summary.json（汇总）、questions.jsonl（逐题结果）、mapping.json（来源映射）。
Recall@K 为该题标注证据找回比例；Hit@K 为至少找到一条；All-evidence@K 为全部找回。
汇总对符合条件的题按题等权平均，另按 category 分组。类别 5（对抗题）、空证据和无法解析到
对话的证据不参与指标，但仍发送 Search，排除数量在报告里记录。

证据编号可能同一字符串含多个 ID，会拆分；ID 只在各样本范围匹配。当前返回记忆 ID 到
dia_id 的映射依赖 SQLiteBM25 的稳定 ID 规则：这是本 baseline 的评测实现，不是 AML 标准。
接入其他后端时需补其来源映射，不能直接复用该假设。报告中的数据 SHA256 用于记录数据版本。
本评测不是 AML locomo-refined、AML 官方成绩或端到端问答成绩；证据标注也未必穷尽所有可用证据。
