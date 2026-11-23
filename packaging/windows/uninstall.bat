@echo off
rem VideoAutomation uninstall. Add -RemoveData to also delete projects and assets.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
if "%VA_NO_PAUSE%"=="" pause
