@echo off
rem Starts the webcam hand tracker (preview window). Extra options: run.bat --help
cd /d "%~dp0"
.venv\Scripts\python tracker.py %*
