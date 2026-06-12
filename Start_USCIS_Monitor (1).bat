@echo off
title USCIS Policy Monitor
color 0A

echo ========================================
echo   USCIS Policy Monitor
echo ========================================
echo.

set TELEGRAM_TOKEN=8342768529:AAHpAegbSD7C_mq8hHN9u4oDutNUufsOMAA
set TELEGRAM_CHAT_ID=682870834
set CHECK_INTERVAL_MINUTES=5

cd /d "C:\Users\Sasan\OneDrive\Desktop\memo"

echo Sending Telegram notification...
curl -s -X POST "https://api.telegram.org/bot%TELEGRAM_TOKEN%/sendMessage" -d "chat_id=%TELEGRAM_CHAT_ID%&text=USCIS Monitor started on your desktop. Checking every 5 minutes." > nul

echo Starting monitor... Check your Telegram!
echo Press Ctrl+C to stop.
echo.

python monitor.py

echo.
echo Monitor stopped.
pause
