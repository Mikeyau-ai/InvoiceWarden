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
echo   build ^& release   sixthdaystudios.com
echo.

:: release.py is the whole pipeline: it runs the tests, then asks
::   1. build a fresh InvoiceWarden.exe?
::   2. publish dist\InvoiceWarden-<APP_VERSION>.exe to the update store?
:: Answer the prompts. Scripting overrides: --yes (yes to both),
:: --build-only (build, never publish), --skip-build (publish dist\ as-is),
:: --dry-run (sign + print, upload nothing).
:: --github-transition is for ONE final release only (see release.py).
python release.py %*
set RC=%errorlevel%
pause
exit /b %RC%
