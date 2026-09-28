@echo off
rem ============================================
rem  为 TS帧分析工具 创建桌面快捷方式（带 logo 图标）
rem  注意: exe 与 logo.ico 必须在同一个文件夹里
rem ============================================
chcp 65001 >nul
setlocal
set "DIR=%~dp0"

if not exist "%DIR%TS帧分析工具.exe" (
    echo 未找到 TS帧分析工具.exe，请勿单独移动 exe，请复制整个文件夹。
    pause
    exit /b 1
)
if not exist "%DIR%logo.ico" (
    echo 未找到 logo.ico，请复制完整的工具文件夹。
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=(Resolve-Path '%DIR%').Path;" ^
  "$desktop=[Environment]::GetFolderPath('Desktop');" ^
  "$lnk=Join-Path $desktop 'TS帧分析工具.lnk';" ^
  "if (Test-Path $lnk) { Remove-Item $lnk -Force };" ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath=Join-Path $d 'TS帧分析工具.exe';" ^
  "$s.WorkingDirectory=$d;" ^
  "$s.IconLocation=(Join-Path $d 'logo.ico')+',0';" ^
  "$s.Description='TS 帧分析工具';" ^
  "$s.Save();" ^
  "$code=@'
using System;
using System.Runtime.InteropServices;
public class ICM {
  [DllImport(\"shell32.dll\")] public static extern void SHChangeNotify(int wEventId, int uFlags, IntPtr dwItem1, IntPtr dwItem2);
}
'@;" ^
  "Add-Type -TypeDefinition $code;" ^
  "[ICM]::SHChangeNotify(0x8000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)"

echo 桌面快捷方式已创建（图标已刷新）。
pause
endlocal
