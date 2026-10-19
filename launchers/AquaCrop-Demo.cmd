@echo off
rem AquaCrop desktop demo (Windows). Double-click this file, or run it from a console with options for
rem `aquacrop launch`, for example:  AquaCrop-Demo.cmd --check-only   AquaCrop-Demo.cmd --port 8010
rem A release folder carries its own Python in runtime\ (CPython for 64-bit Windows from python.org, with
rem AquaCrop already installed): nothing is installed or downloaded and no Python is needed on the PC.
rem Without runtime\ (a source checkout) a 64-bit Python 3.11 to 3.14 on the PC is used, with a private
rem environment under %LOCALAPPDATA%\AquaCrop named after the wheel AND its SHA-256.
rem Data stays in %LOCALAPPDATA%\AquaCrop\demo, never in this folder. The simulation is served on this PC
rem only (127.0.0.1). Stop it with Ctrl+C or by closing the window.
setlocal EnableExtensions EnableDelayedExpansion
title AquaCrop demo
set "HERE=%~dp0"
set "LOCAL=%LOCALAPPDATA%\AquaCrop"

rem 0. The Python bundled with this release. No parenthesised blocks here: a folder name such as
rem "aquacrop (1)" would end them early.
if exist "%HERE%runtime\python.exe" goto :bundled

rem 1. 64-bit Python 3.11 to 3.14 on the PC (the tested range), for a source checkout
set "PY="
py -3.12 -c "import sys; raise SystemExit(sys.maxsize < 2**32)" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13),(3,14)) and sys.maxsize > 2**32 else 1)" >nul 2>&1 && set "PY=python"
)
for %%V in (3.13 3.14 3.11) do (
  if not defined PY (
    py -%%V -c "import sys; raise SystemExit(sys.maxsize < 2**32)" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  echo This folder has no bundled Python ^(runtime^) and no 64-bit Python 3.11 to 3.14 was found.
  echo Download the release ZIP again and extract the whole ZIP: the release carries its own Python.
  goto :fail
)

rem 2. What to install: the wheel shipped next to this file (release ZIP) or the source checkout above it
set "WHEEL=" & set "EDITABLE="
for %%W in ("%HERE%wheels\aquacrop_greenhouse-*.whl") do set "WHEEL=%%~fW"
if defined WHEEL (
  for %%W in ("!WHEEL!") do set "TAG=%%~nW"
) else if exist "%HERE%..\pyproject.toml" (
  for %%D in ("%HERE%..") do set "WHEEL=%%~fD"
  set "TAG=source" & set "EDITABLE=-e"
) else (
  echo Cannot find the AquaCrop wheel in "%HERE%wheels" or a source checkout. Unpack the whole release ZIP.
  goto :fail
)
set "LOCK="
if exist "%HERE%requirements.lock" set "LOCK=%HERE%requirements.lock"
if not defined LOCK if exist "%HERE%..\requirements.lock" set "LOCK=%HERE%..\requirements.lock"
if not defined LOCK (
  echo requirements.lock is missing next to this file. Unpack the whole release ZIP.
  goto :fail
)
rem The environment is keyed by the wheel's SHA-256 (first 12 hex digits in the folder name, all 64 in a
rem marker written only after a complete install), never by the file name alone.
set "WSHA="
if not defined EDITABLE (
  for /f %%H in ('!PY! -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "!WHEEL!"') do set "WSHA=%%H"
  if not defined WSHA (
    echo Cannot read the AquaCrop wheel "!WHEEL!". Unpack the whole release ZIP again.
    goto :fail
  )
  set "VENV=%LOCAL%\venv-!TAG!-!WSHA:~0,12!"
) else (
  set "VENV=%LOCAL%\venv-!TAG!"
)
set "MARKER=!VENV!\aquacrop-wheel.sha256"
if defined WSHA if exist "!VENV!\Scripts\python.exe" (
  set "HAVE="
  if exist "!MARKER!" set /p HAVE=<"!MARKER!"
  if not "!HAVE!"=="!WSHA!" (
    rem only an install that never finished, without its marker, gets here: it holds no data
    echo The environment "!VENV!" is incomplete; preparing it again.
    rmdir /s /q "!VENV!" 2>nul
  )
)
if not exist "!VENV!\Scripts\python.exe" (
  echo Preparing AquaCrop in "!VENV!" ^(first run only^) ...
  %PY% -m venv "!VENV!" || goto :fail
  set "OFFLINE="
  rem the version of the new environment, from the interpreter that made it: a for /f command that starts
  rem with a quoted path loses its first and last quote to cmd /c
  for /f %%T in ('!PY! -c "import sys; print('py%%d%%d' %% sys.version_info[:2])"') do set "PYTAG=%%T"
  if exist "%HERE%wheelhouse-win_amd64-!PYTAG!\" set "OFFLINE=--no-index --find-links "%HERE%wheelhouse-win_amd64-!PYTAG!""
  if defined OFFLINE (echo Installing from the bundled wheelhouse ^(no internet needed^).) else (echo Installing the locked packages from the internet.)
  "!VENV!\Scripts\python.exe" -m pip install --disable-pip-version-check -q !OFFLINE! --require-hashes -r "!LOCK!" || goto :failvenv
  "!VENV!\Scripts\python.exe" -m pip install --disable-pip-version-check -q --no-deps !EDITABLE! "!WHEEL!" || goto :failvenv
  if defined WSHA (>"!MARKER!" echo !WSHA!)
)
set "AQUACROP_WHEEL_SHA256=!WSHA!"
rem a release wheel is always checked against RELEASE.json (missing counts as a failed check)
set "AQUACROP_RELEASE_INFO="
if defined WSHA set "AQUACROP_RELEASE_INFO=%HERE%RELEASE.json"

rem 3. Start (prerequisite checks, first administrator, browser) and wait until stopped
"!VENV!\Scripts\python.exe" -m aquacrop launch %*
if errorlevel 1 goto :fail
endlocal & exit /b 0

:bundled
rem The release check compares RELEASE.json with the wheel installed in runtime\ when the release was built.
set "PY=%HERE%runtime\python.exe"
set "WSHA="
if exist "%HERE%runtime\aquacrop-wheel.sha256" set /p WSHA=<"%HERE%runtime\aquacrop-wheel.sha256"
set "AQUACROP_WHEEL_SHA256=%WSHA%"
set "AQUACROP_RELEASE_INFO=%HERE%RELEASE.json"
echo Using the Python bundled with this release: "%PY%"
rem -I: no user packages, PYTHON* variables or current folder on the path; -X utf8 for names such as Jose
"%PY%" -I -X utf8 -m aquacrop launch %*
if errorlevel 1 goto :fail
endlocal & exit /b 0

:failvenv
echo Installation failed; removing the incomplete environment "!VENV!".
rmdir /s /q "!VENV!" 2>nul
:fail
echo.
echo AquaCrop did not start. The lines above say why, with the data folder and log file in use.
if not defined AQUACROP_NO_PAUSE pause
endlocal & exit /b 1
