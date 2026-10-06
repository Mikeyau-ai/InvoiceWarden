@echo off
setlocal
cd /d "%~dp0"
title InvoiceWarden - build ^& release

echo.
echo   ##### #   # #   #  ###  ##### #### ##### #   #  ###
echo     #   ##  # #   # #   #   #   #    #    ## ## #   #
echo     #   # # # #   # #   #   #   #    ###  # # #  ###
echo     #   #  ## #   # #   #   #   #    #    #   # #   #
echo   ##### #   #   #    ###  ##### #### ##### #   #  ###
echo   build ^& release   github.com/Mikeyau-ai/InvoiceWarden
echo.

:: release.py is the whole pipeline: it runs the tests, then asks
::   1. build a fresh InvoiceWarden.exe?
::   2. publish it as GitHub release v<APP_VERSION>?
:: Answer the prompts. Scripting overrides: --yes (yes to both),
:: --build-only (build, never publish), --skip-build (publish dist\ as-is).
python release.py %*
set RC=%errorlevel%
pause
exit /b %RC%
