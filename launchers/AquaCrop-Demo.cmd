@echo off
rem AquaCrop desktop demo (Windows). Double-click this file, or run it from a console with options for
rem `aquacrop launch`, for example:  AquaCrop-Demo.cmd --check-only   AquaCrop-Demo.cmd --port 8010
rem First run: prepares a private Python environment under %LOCALAPPDATA%\AquaCrop (offline when the
rem release ZIP carries a wheelhouse-win_amd64-* folder), then asks for the first administrator password.
rem The simulation is served on this PC only (127.0.0.1). Stop it with Ctrl+C or by closing the window.
setlocal EnableExtensions EnableDelayedExpansion
title AquaCrop demo
set "HERE=%~dp0"
set "LOCAL=%LOCALAPPDATA%\AquaCrop"

rem 1. Python 3.11 to 3.13 (the tested range); 3.12 first, the version of the bundled Windows wheelhouse
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)" >nul 2>&1 && set "PY=python"
)
for %%V in (3.13 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  echo AquaCrop needs Python 3.11, 3.12 or 3.13 ^(64-bit^), and none was found.
  echo Install it from https://www.python.org/downloads/windows/ ^(keep the "py launcher" option^),
  echo then run this file again.
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
set "VENV=%LOCAL%\venv-!TAG!"
if not exist "!VENV!\Scripts\python.exe" (
  echo Preparing AquaCrop in "!VENV!" ^(first run only^) ...
  %PY% -m venv "!VENV!" || goto :fail
  set "OFFLINE="
  for /f %%T in ('"!VENV!\Scripts\python.exe" -c "import sys; print('py%%d%%d' %% sys.version_info[:2])"') do set "PYTAG=%%T"
  if exist "%HERE%wheelhouse-win_amd64-!PYTAG!\" set "OFFLINE=--no-index --find-links "%HERE%wheelhouse-win_amd64-!PYTAG!""
  if defined OFFLINE (echo Installing from the bundled wheelhouse ^(no internet needed^).) else (echo Installing the locked packages from the internet.)
  "!VENV!\Scripts\python.exe" -m pip install --disable-pip-version-check -q !OFFLINE! --require-hashes -r "!LOCK!" || goto :failvenv
  "!VENV!\Scripts\python.exe" -m pip install --disable-pip-version-check -q --no-deps !EDITABLE! "!WHEEL!" || goto :failvenv
)

rem 3. Start (prerequisite checks, first administrator, browser) and wait until stopped
"!VENV!\Scripts\python.exe" -m aquacrop launch %*
if errorlevel 1 goto :fail
endlocal & exit /b 0

:failvenv
echo Installation failed; removing the incomplete environment "!VENV!".
rmdir /s /q "!VENV!" 2>nul
:fail
echo.
echo AquaCrop did not start. The lines above say why; the log is under %LOCAL%\demo\logs.
if not defined AQUACROP_NO_PAUSE pause
endlocal & exit /b 1
