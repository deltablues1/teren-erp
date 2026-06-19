@echo off
REM Registrira Telegram bota kao Windows servis pomoću NSSM.
REM Preduvjet: NSSM instaliran (https://nssm.cc/download), nssm.exe u PATH-u.
REM
REM Pokreni kao Administrator:  scripts\install_service.bat

setlocal

set SERVICE_NAME=TerenTelegramBot
set ROOT=%~dp0..
set PYTHON=python.exe
set SCRIPT=%ROOT%\bot.py

where nssm >nul 2>&1
if errorlevel 1 (
    echo NSSM nije pronađen u PATH-u.
    echo Preuzmi sa https://nssm.cc/download i stavi nssm.exe u PATH.
    exit /b 1
)

echo Instaliram servis %SERVICE_NAME%...
nssm install %SERVICE_NAME% "%PYTHON%" "%SCRIPT%"
nssm set %SERVICE_NAME% AppDirectory "%ROOT%"
nssm set %SERVICE_NAME% AppStdout "%ROOT%\bot.log"
nssm set %SERVICE_NAME% AppStderr "%ROOT%\bot.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
nssm set %SERVICE_NAME% AppExit Default Restart

echo Pokrećem servis...
nssm start %SERVICE_NAME%

echo Gotovo. Status:
nssm status %SERVICE_NAME%
echo.
echo Za uklanjanje: nssm remove %SERVICE_NAME% confirm
endlocal
