# traderSystem

A 股投资决策辅助系统（**只辅助决策，不自动交易**）：数据采集 → 定量计算 → 观点推理 → 执行纪律 → 复盘闭环。

## 文档入口

| 文档 | 内容 |
|---|---|
| **[docs/SYSTEM_GUIDE.md](docs/SYSTEM_GUIDE.md)** | 系统现状 + 使用说明（推荐从这开始） |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 运维手册（调度/备份/排查） |
| [docs/MANUAL_TASKS.md](docs/MANUAL_TASKS.md) | 手动执行项清单与进度 |
| [TODO.md](TODO.md) | 完成度追踪（83 项，已完成 75） |

## 快速启动

```bat
myenv\Scripts\python.exe scripts\run_service.py     :: 常驻调度（盘前/盘后/盘中/复盘）
myenv\Scripts\python.exe scripts\run_dashboard.py   :: 仪表盘 → http://localhost:8501
myenv\Scripts\python.exe scripts\run_api.py         :: API（按需）→ http://127.0.0.1:8000/docs
```

环境：Windows + Python 3.14（`myenv` 虚拟环境），数据库 `data/invest.db`（SQLite）。
