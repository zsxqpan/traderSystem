"""MCP stdio 握手冒烟测试：启动三个 server，走 initialize + tools/list JSON-RPC。

不依赖 mcp 库（纯 stdlib），可用 myenv 的 pytest 直接跑。
"""
import json
import os
import queue
import subprocess
import threading

import pytest

TRADER = r"C:\Users\狐狸怂\Documents\Codex\2026-08-01\la\traderSystem"
PY = r"C:\Users\狐狸怂\AppData\Roaming\uv\tools\mcp-notify\Scripts\python.exe"
UV = r"C:\Users\狐狸怂\AppData\Roaming\uv\tools"

SERVERS = [
    ("sqlite-invest", [PY, "-m", "tools.mcp.local_sqlite_mcp"], {"PYTHONPATH": TRADER}),
    ("mcp-notify", [f"{UV}\\mcp-notify\\Scripts\\mcp-notify.exe"], {}),
    ("akshare-tools", [f"{UV}\\akshare-tools\\Scripts\\akshare-tools.exe"], {}),
]


def _rpc(proc, q, msg: dict) -> dict:
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()
    line = q.get(timeout=45)
    assert line, f"server 无响应: {msg['method']}"
    return json.loads(line.decode("utf-8"))


def _handshake(name: str, cmd: list, extra_env: dict) -> list[str]:
    env = dict(os.environ)
    env.update(extra_env)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, cwd=TRADER,
    )
    q = queue.Queue()

    def reader():
        while True:
            line = proc.stdout.readline()
            if not line:
                q.put(None)
                return
            q.put(line)

    threading.Thread(target=reader, daemon=True).start()
    try:
        resp = _rpc(proc, q, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hermes-pytest", "version": "1.0"},
        }})
        assert resp.get("result", {}).get("protocolVersion"), f"{name} initialize 失败: {resp}"
        _rpc(proc, q, {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized", "params": {}})
        tools = _rpc(proc, q, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
        assert names, f"{name} 无工具"
        return names
    finally:
        proc.kill()


@pytest.mark.parametrize("name,cmd,env", SERVERS, ids=[s[0] for s in SERVERS])
def test_mcp_handshake(name, cmd, env):
    tools = _handshake(name, cmd, env)
    assert len(tools) >= 1
    print(f"{name}: {len(tools)} tools -> {tools[:4]}")
