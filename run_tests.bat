@echo off
setlocal
cd /d "%~dp0"
title InvoiceWarden - tests

echo.
echo   ##### #   # #   #  ###  ##### #### ##### #   #  ###
echo     #   ##  # #   # #   #   #   #    #    ## ## #   #
echo     #   # # # #   # #   #   #   #    ###  # # #  ###
echo     #   #  ## #   # #   #   #   #    #    #   # #   #
echo   ##### #   #   #    ###  ##### #### ##### #   #  ###
echo   test suite   github.com/Mikeyau-ai/InvoiceWarden
echo.

python -m unittest discover -s tests -v
if errorlevel 1 (
  echo.
  echo  TESTS FAILED - do not release this build.
  pause
  exit /b 1
)

echo.
echo  All tests passed.
echo.
pause
