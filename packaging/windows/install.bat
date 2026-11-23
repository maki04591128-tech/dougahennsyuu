@echo off
rem VideoAutomation setup. Log: install_log.txt
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%
if "%VA_NO_PAUSE%"=="" pause
exit /b %RC%
