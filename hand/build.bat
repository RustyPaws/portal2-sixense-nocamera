@echo off
rem Builds the fake-hand proxy sixense.dll (32-bit) with Visual Studio 2022.
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul || exit /b 1
cd /d "%~dp0"
if not exist out mkdir out
cl /nologo /O2 /MT /W3 /LD /D_CRT_SECURE_NO_WARNINGS nocam_hand.c /Fo"out\\" /Fe"out\sixense.dll" /link /DEF:sixense.def user32.lib kernel32.lib || exit /b 1
echo BUILD OK: %~dp0out\sixense.dll
