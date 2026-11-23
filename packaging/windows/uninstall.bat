@echo off
rem VideoAutomation uninstall. Add -RemoveData to also delete projects and assets.
rem One line: this file is deleted by uninstall.ps1, so cmd must not read further lines.
cd /d "%~dp0" & powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %* & (if "%VA_NO_PAUSE%"=="" pause) & exit /b
