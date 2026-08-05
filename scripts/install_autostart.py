"""注册开机自启（无需管理员）。

优先写 HKCU Run 注册表；失败则回退到启动文件夹快捷方式。
用法（在你的 PowerShell 里）: myenv\\Scripts\\python.exe scripts/install_autostart.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "InvestSystemService"
PYW = ROOT / "myenv" / "Scripts" / "pythonw.exe"
SERVICE = ROOT / "scripts" / "run_service.py"


def _cmd() -> str:
    return f'"{PYW}" "{SERVICE}"'


def _install_registry() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _cmd())
        print(f"[OK] 已写入 HKCU\\{RUN_KEY}\\{VALUE_NAME}")
        return True
    except PermissionError:
        return False


def _install_startup_shortcut() -> bool:
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    if not startup.exists():
        print("[!] 找不到启动文件夹:", startup)
        return False
    lnk = startup / f"{VALUE_NAME}.lnk"
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{PYW}'; "
        f"$s.Arguments = '\"{SERVICE}\"'; "
        "$s.WorkingDirectory = '"
        + str(ROOT).replace("'", "''")
        + "'; $s.Save()"
    )
    ret = subprocess.run(["powershell", "-NoProfile", "-Command", ps])
    if ret.returncode == 0 and lnk.exists():
        print(f"[OK] 已创建启动快捷方式: {lnk}")
        return True
    print("[!] 启动快捷方式创建失败")
    return False


def main() -> None:
    if _install_registry():
        print("验证: reg query \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\" /v InvestSystemService")
        return
    print("[!] 注册表写入被拒，尝试启动文件夹快捷方式…")
    if not _install_startup_shortcut():
        print("[!] 两种方式都失败。请以管理员身份运行 PowerShell 后重试，或手动把 "
              "pythonw.exe 的快捷方式放进启动文件夹。")


if __name__ == "__main__":
    main()