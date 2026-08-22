"""移除开机自启（注册表项 + 启动快捷方式）。"""
from __future__ import annotations

import os
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "InvestSystemService"


def main() -> None:
    removed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        print(f"[OK] 已删除注册表项 {VALUE_NAME}")
        removed = True
    except FileNotFoundError:
        pass
    except PermissionError:
        print("[!] 注册表删除被拒")
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    lnk = startup / f"{VALUE_NAME}.lnk"
    if lnk.exists():
        try:
            lnk.unlink()
            print(f"[OK] 已删除启动快捷方式 {lnk}")
            removed = True
        except OSError as exc:
            print(f"[!] 快捷方式删除失败: {exc}")
    if not removed:
        print("未找到已注册的自启项")


if __name__ == "__main__":
    main()