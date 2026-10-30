@echo off
rem Entry point for Windows Task Scheduler: runs `python -m src.run <args>` in the project.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
"%~dp0..\.venv\Scripts\python.exe" -m src.run %*
