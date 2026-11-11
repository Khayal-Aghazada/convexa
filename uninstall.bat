@echo off
rem Removes autostart, the right-click menu entry and the Start-menu shortcut.
cd /d "%~dp0"
".venv\Scripts\python.exe" convexa.pyw --uninstall
echo Convexa integration removed. You can now delete this folder.
pause
