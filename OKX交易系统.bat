@echo off
set VENV=%~dp0.venv\Scripts
"%VENV%\python.exe" -m streamlit run "%~dp0app\Home.py" --server.port 8501 --server.headless false
pause
