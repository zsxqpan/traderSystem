# 大V画像库 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.  
> 设计：`docs/plans/2026-09-09-大V画像库-design.md`

**Goal:** 精选雪球大V 全文语料 + FTS + 人格卡，仪表盘选人对话，飞书 `/大V` 必进、口语点名也能进。

**Architecture:** `invest/bigv/` 一块引擎（watch / harvest / search / persona / ask / route）。采集复用 Playwright，抓取函数可注入。问答默认有据、可切推断。调度 `big_v_harvest` 17:10，周末任务顺带回灌。

**Tech Stack:** Python 3.10+ / SQLite FTS5 / Playwright / Streamlit / 飞书 WS / pytest（全 mock）

---

### Task 1: Schema + FTS + 名单

**Files:** `invest/db.py`（SCHEMA_VERSION=21，`big_v_profile`/`big_v_opinion` 新列）、`invest/bigv/schema.py`、`invest/bigv/watch.py`、`invest/bigv/persist.py`、`tests/test_bigv.py`

- 新列：`watched`、人格卡与采集状态、`backfill_*`、`body`
- FTS5 `big_v_opinion_fts` + INSERT/UPDATE/DELETE 触发器
- `register(name, xueqiu_id|URL)` → `xq_{uid}`、`watched=1`
- 同 URL 不重复写；`view` 短摘要，`body` 全文

### Task 2: 采集 / 检索 / 人格卡 / 问答 / 飞书分流

**Files:** `invest/bigv/harvest.py` `search.py` `persona.py` `ask.py` `route.py` `__init__.py`

- harvest：只采 `watched=1`；注入 fetch；时间预算到点停
- 一期 N=15；`backfill=True` 时 N=30，条数不足标 `backfill_done`
- FTS 检索 Top-8；有据无命中不编造；推断标「推断」
- `/大V` 必进；口语未命中名单返回 None（普通对话）

### Task 3: 调度 / 仪表盘 / 飞书 / 工具

**Files:** `invest/scheduler.py`、`scripts/run_job.py`、`scripts/install_os_tasks.ps1`（保 UTF-8 BOM）、`dashboard/nav.py` `queries.py` `app.py`、`invest/push/feishu_ws.py`、`invest/agent/tools.py` `agents.py`、`AGENTS.md`、相关测试

- JOB 17:10，补偿到 17:29:59；周末 `_weekend` 末尾回灌
- 页「大V画像库」在观点库后
- `_agent_reply` 在意图分流前拦截
- 工具 `ask_big_v` 只读补救，口令不依赖它

### Task 4: 验证

```
myenv/bin/python -m pytest tests/test_bigv.py tests/test_pipeline.py::test_scheduler_jobs tests/test_pipeline.py::test_ticker_only_and_job_funcs tests/test_dashboard_signals.py::test_d3_nav_inserts_signals_page tests/test_freeze_assist.py::test_dashboard_pages_only_add_trade_signals tests/test_reliable_jobs.py::test_os_task_manifest_matches_job_funcs_and_required_times tests/test_agent.py::test_big_v_tables_created tests/test_agent.py::test_big_v_tools -q
ruff check invest/bigv invest/db.py invest/scheduler.py dashboard tests/test_bigv.py
```
