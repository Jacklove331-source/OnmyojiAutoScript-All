@echo off
rem OAS dual-agent (Claude commander + DeepSeek executor): resume after logon.
rem Copy into the Startup folder. ASCII only on purpose: cmd.exe reads .cmd files
rem in the OEM code page, UTF-8 Chinese comments can corrupt the next line.
rem Stop: dev_tools\dual_agent\control.py pause   (or delete this file)
setlocal
set REPO=D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
set PY=%REPO%\toolkit\python.exe
set DA=%REPO%\dev_tools\dual_agent

if not exist "%PY%" goto :eof
if exist "%DA%\state\PAUSE" goto :eof

rem do not start a second watchdog
powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*dual_agent*watchdog.py*' }).Count -gt 0) { exit 1 }"
if errorlevel 1 goto :eof

set PYTHONIOENCODING=utf-8
start "" /b "%PY%" "%DA%\watchdog.py" --hours 8 >> "%DA%\logs\autostart.log" 2>&1
