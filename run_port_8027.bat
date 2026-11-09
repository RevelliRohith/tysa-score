@echo off
cd /d "C:\Users\My Files\Desktop\tysa-score"
echo Starting TYSA Score Server on Port 8027...
call venv\Scripts\python.exe manage.py runserver 0.0.0.0:8027
pause
