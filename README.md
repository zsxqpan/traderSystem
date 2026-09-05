# traderSystem

A 股投资决策辅助系统（**只辅助决策，不自动交易**）。

2026-08-28 起产品收敛为：**规则负责数字与任务送达，AI 只整理带来源的事实，中期比价由人完成**。

## 文档入口

| 文档 | 内容 |
|---|---|
| **[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md)** | 系统介绍 + 日常使用（飞书 / 仪表盘 / 报告 / 任务） |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 运维手册（调度 / 补偿 / 备份 / 排查） |
| [docs/MANUAL_TASKS.md](docs/MANUAL_TASKS.md) | 手动执行项清单与进度 |
| [TODO.md](TODO.md) | 完成度追踪 |

## 快速启动

```bat
myenv\Scripts\python.exe scripts\run_service.py     :: 默认 ticker-only：10s 行情 + 1min 补偿 + 飞书
myenv\Scripts\python.exe scripts\run_dashboard.py   :: 仪表盘（含中期比价）→ http://localhost:8501
myenv\Scripts\python.exe scripts\run_api.py         :: API（按需）→ http://127.0.0.1:8000/docs
```

首次或升级 Schema 后先跑 `scripts\init_db.py`。例行任务需先注册：`scripts\install_os_tasks.ps1`（13 个，与 `JOB_FUNCS` 一致）。

环境：Windows + Python 3.14（`myenv` / `.venv`），数据库 `data/invest.db`（SQLite）。
