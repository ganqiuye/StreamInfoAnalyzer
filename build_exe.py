#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""编译 GUI 为独立 exe（无 Python 环境可直接运行）

用法:
    pip install customtkinter tkinterdnd2 pyinstaller
    python build_exe.py
产物: dist/TS帧分析工具/TS帧分析工具.exe
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "TS帧分析工具"
DIST = ROOT / "dist" / APP_NAME


def check_deps():
    try:
        import customtkinter, tkinterdnd2  # noqa
    except ImportError:
        print("[!] 缺少 GUI 依赖，请先执行:")
        print("    pip install customtkinter tkinterdnd2")
        sys.exit(1)
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[!] 缺少 pyinstaller，请执行: pip install pyinstaller")
        sys.exit(1)


def main():
    check_deps()
    entry = ROOT / "ts_frame_gui.pyw"
    if not entry.is_file():
        entry = ROOT / "ts_frame_gui.py"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", APP_NAME,
        "--windowed",               # 无控制台
        "--icon", str(ROOT / "assets" / "logo.ico"),  # exe 图标
        "--add-data", str(ROOT / "assets" / "logo.ico") + ";assets",  # 窗口图标随包分发
        "--collect-all", "customtkinter",
        "--collect-all", "tkinterdnd2",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build_pyinstaller"),
        "--specpath", str(ROOT / "build_pyinstaller"),
        str(entry),
    ]
    print(">>", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print("[!] PyInstaller 编译失败")
        sys.exit(rc)

    exe = DIST / (APP_NAME + ".exe")
    if exe.is_file():
        size_mb = exe.stat().st_size / 1024 / 1024
        print("\n[OK] 编译完成: %s (%.0f MB)" % (exe, size_mb))
        print("     分发时把整个文件夹 %s 打包成 zip 给用户即可" % DIST)
    else:
        print("[!] 未找到产物:", exe)
        sys.exit(1)


if __name__ == "__main__":
    main()
