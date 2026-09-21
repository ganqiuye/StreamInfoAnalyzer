@echo off
rem ============================================
rem  一键编译 TS帧分析工具 exe
rem  双击运行即可; 产物在 dist\TS帧分析工具\
rem ============================================
chcp 65001 >nul
cd /d "%~dp0"

set PY=C:\Users\qiuye.gan\AppData\Local\Programs\Python\Python38\python.exe
if not exist "%PY%" set PY=python

echo [1/3] 安装/检查依赖...
%PY% -m pip install --quiet customtkinter tkinterdnd2 pyinstaller
if errorlevel 1 (
    echo [!] 依赖安装失败，请检查网络或 Python 环境
    pause
    exit /b 1
)

echo [2/3] 开始编译...
%PY% build_exe.py
if errorlevel 1 (
    echo [!] 编译失败
    pause
    exit /b 1
)

echo [3/3] 完成! 产物: %~dp0dist\TS帧分析工具\TS帧分析工具.exe
pause
