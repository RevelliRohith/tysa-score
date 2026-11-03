@echo off
title TYSA Score Local Server
echo ===================================================
echo Starting TYSA Score Server...
echo ===================================================
echo Main Laptop:  http://127.0.0.1:8027/
echo Localhost:    http://localhost:8027/
echo Local Network: http://192.168.29.153:8027/
echo ===================================================
cd /d "%~dp0"
venv\Scripts\python.exe manage.py runserver 0.0.0.0:8027
pause
