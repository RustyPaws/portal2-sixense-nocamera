@echo off
rem Builds the fake-hand proxy sixense.dll (32-bit) with Visual Studio 2019/2022
rem (any edition, also Build Tools). Used locally and by GitHub Actions.
setlocal
cd /d "%~dp0"

rem Already in an x86 developer prompt? Otherwise find VS with vswhere.
if /i "%VSCMD_ARG_TGT_ARCH%"=="x86" goto build
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
    echo Visual Studio not found: install VS 2022 with "Desktop development with C++"
    exit /b 1
)
set "VSDIR="
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
    echo Visual Studio C++ tools not found: add "Desktop development with C++"
    exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul || exit /b 1

:build
if not exist out mkdir out
cl /nologo /O2 /MT /W3 /LD /D_CRT_SECURE_NO_WARNINGS nocam_hand.c /Fo"out\\" /Fe"out\sixense.dll" /link /DEF:sixense.def user32.lib kernel32.lib || exit /b 1
echo BUILD OK: %~dp0out\sixense.dll
