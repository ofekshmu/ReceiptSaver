@echo off
:: Run ONCE as Administrator
set SCRIPT_DIR=%~dp0
echo [1/3] Installing dependencies...
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests plyer
if errorlevel 1 ( echo pip failed & pause & exit /b 1 )

echo [2/3] Checking credentials.json...
if not exist "%SCRIPT_DIR%credentials.json" (
    echo credentials.json NOT FOUND — see README Step 2 & pause & exit /b 1
)

echo [3/3] Registering Task Scheduler...
schtasks /create /tn "ReceiptSaver" /tr "python \"%SCRIPT_DIR%receipt_saver.py\"" /sc ONLOGON /rl HIGHEST /f
if errorlevel 1 ( echo Failed — run as Administrator & pause & exit /b 1 )

echo.
echo Done! Next:
echo   1. Open receipt_saver.py and set ANTHROPIC_API_KEY
echo   2. python ticktick_auth.py
echo   3. Restart PC — browser opens for Gmail auth on first run
pause
