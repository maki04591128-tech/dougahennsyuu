@echo off
rem VideoAutomation uninstall. Add -RemoveData to also delete projects and assets.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
if "%VA_NO_PAUSE%"=="" pause
rem Delete this file last; (goto) leaves the batch context so cmd does not read the deleted file.
(goto) 2>nul & cd /d "%~dp0.." & del "%~f0" & rd "%~dp0" 2>nul
