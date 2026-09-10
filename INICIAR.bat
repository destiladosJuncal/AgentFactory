@echo off
rem ============================================================
rem   AgentFactory - arranque portable para Windows 10/11
rem
rem   Doble clic aca y listo. Si no hay un Python usable con tkinter,
rem   se baja uno (~30 MB) y queda DENTRO de esta carpeta. No instala
rem   nada en el sistema ni pide permisos de administrador.
rem
rem   Este archivo esta escrito en ASCII a proposito: un .bat con
rem   tildes se interpreta distinto segun el codepage de cada PC.
rem   Los acentos viven del lado de Python, que si los maneja bien.
rem ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

cd /d "%~dp0"
set "APP_DIR=%CD%"

rem --- Aislamiento del fork -----------------------------------------------
rem Datos propios (conversaciones, proyectos, tareas y el .env con las
rem claves) separados del codigo. El codigo soporta estos overrides.
set "AGENTE_APP=%APP_DIR%"
if not defined AGENTE_DATOS set "AGENTE_DATOS=%USERPROFILE%\tmp\agentfactory"

rem Que Python hable UTF-8 aunque la consola sea cp850.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "RUNTIME_DIR=%APP_DIR%\runtime"
set "PY_STANDALONE=%RUNTIME_DIR%\python\python.exe"

set "VERSION_PY=3.12.13"
set "RELEASE=20260807"

echo.
echo   AgentFactory
echo.

rem --- Buscar un Python usable --------------------------------------------
rem "Usable" = 3.12+ y con tkinter. Se comprueba EJECUTANDOLO, no mirando si
rem el archivo existe: el python.exe que Windows deja en el PATH suele ser el
rem alias de la Microsoft Store, que no es Python sino un instalador.
set "PYTHON="

call :probar "%PY_STANDALONE%"
if defined PYTHON goto :listo

for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :probar "%%p"
if defined PYTHON goto :listo

for /f "delims=" %%p in ('where python 2^>nul') do (
    echo %%p | find /i "\WindowsApps\" >nul
    if errorlevel 1 call :probar "%%p"
)
if defined PYTHON goto :listo

for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :probar "%%d\python.exe"
if defined PYTHON goto :listo

for /d %%d in ("%ProgramFiles%\Python3*") do call :probar "%%d\python.exe"
if defined PYTHON goto :listo

for /d %%d in ("C:\Python3*") do call :probar "%%d\python.exe"
if defined PYTHON goto :listo

goto :descargar


rem --- Descarga del runtime propio ----------------------------------------
:descargar
echo   No encontre un Python con tkinter en esta PC.
echo   Voy a bajar uno (~30 MB) y dejarlo en esta misma carpeta.
echo   No se instala nada en el sistema: si borras la carpeta, no queda rastro.
echo.

if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    set "ARCO=x86_64-pc-windows-msvc"
) else if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set "ARCO=aarch64-pc-windows-msvc"
) else if /i "%PROCESSOR_ARCHITECTURE%"=="x86" (
    echo   [ERROR] Windows de 32 bits no esta soportado.
    goto :fallo
) else (
    echo   [ERROR] Arquitectura no reconocida: %PROCESSOR_ARCHITECTURE%
    goto :fallo
)

set "URL=https://github.com/astral-sh/python-build-standalone/releases/download/%RELEASE%/cpython-%VERSION_PY%+%RELEASE%-%ARCO%-install_only.tar.gz"
set "TGZ=%RUNTIME_DIR%\python.tar.gz"

if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

rem tar.exe viene con Windows 10 desde la version 1803. Sin el no hay como
rem descomprimir un .tar.gz sin dependencias externas.
where tar.exe >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Esta version de Windows no trae tar.exe ^(hace falta Win10 1803+^).
    echo           Actualiza Windows, o instala Python 3.12 desde python.org
    echo           marcando "Add python.exe to PATH" y volve a ejecutar este archivo.
    goto :fallo
)

echo   Descargando Python %VERSION_PY%...
where curl.exe >nul 2>&1
if errorlevel 1 (
    rem Sin curl: PowerShell. Mas lento, pero esta en todas las Windows.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -Uri $env:URL -OutFile $env:TGZ -UseBasicParsing } catch { exit 1 }"
) else (
    curl.exe -fL --progress-bar "%URL%" -o "%TGZ%"
)
if errorlevel 1 (
    echo   [ERROR] No pude descargarlo. Hay conexion a internet?
    echo           URL: %URL%
    goto :fallo
)

echo   Descomprimiendo...
tar.exe -xzf "%TGZ%" -C "%RUNTIME_DIR%"
if errorlevel 1 (
    echo   [ERROR] No pude descomprimir el runtime.
    goto :fallo
)
del /q "%TGZ%" 2>nul

call :probar "%PY_STANDALONE%"
if not defined PYTHON (
    echo   [ERROR] El Python descargado no funciona como esperaba.
    echo           Esperaba encontrarlo en: %PY_STANDALONE%
    goto :fallo
)
echo   Python listo.
echo.


rem --- El resto ya es Python: entorno, dependencias y arranque ------------
:listo
"%PYTHON%" "%APP_DIR%\bootstrap.py" %*
if errorlevel 1 goto :fallo
exit /b 0


rem --- Subrutinas ---------------------------------------------------------

rem :probar <ruta>  ->  define PYTHON si ese interprete sirve
:probar
if defined PYTHON goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PYTHON=%~1"
goto :eof

:fallo
echo.
pause
exit /b 1
