@echo off
chcp 65001 >nul
"D:\3D\YYS2\OnmyojiAutoScript-easy-install-1\toolkit\python.exe" "%~dp0tools\emulator_pc.py" stop
if errorlevel 1 pause
