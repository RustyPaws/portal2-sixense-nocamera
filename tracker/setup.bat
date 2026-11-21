@echo off
rem One-time setup for the webcam hand tracker.
cd /d "%~dp0"
if not exist .venv python -m venv .venv || exit /b 1
.venv\Scripts\python -m pip install -q mediapipe opencv-python numpy || exit /b 1
if not exist models mkdir models
if not exist models\hand_landmarker.task curl -sSfL -o models\hand_landmarker.task "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task" || exit /b 1
echo SETUP OK - start the tracker with run.bat
