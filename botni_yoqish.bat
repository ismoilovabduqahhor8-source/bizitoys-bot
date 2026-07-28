@echo off
chcp 65001 >nul
title BiziToys bot

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo [XATO] .venv papkasi topilmadi!
    echo Bu faylni bizitoys_bot papkasining ichiga qo'ying.
    echo.
    pause
    exit /b
)

call .venv\Scripts\activate.bat

:loop
echo.
echo ============================================
echo   Bot ishga tushmoqda...  %date% %time%
echo   To'xtatish uchun: Ctrl+C, keyin Y
echo ============================================
echo.

python main.py

echo.
echo [!] Bot to'xtadi. 10 soniyadan keyin qayta urinaman...
timeout /t 10 /nobreak >nul
goto loop
