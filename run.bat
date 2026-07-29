@echo off
title SIM Pairing Dashboard
echo ===================================================
echo Starting SIM Pairing Dashboard...
echo ===================================================
echo.

:: Check if virtual environment folder exists
if not exist venv (
    echo [ERROR] venv virtual environment folder was not found!
    echo Please make sure you have created a virtual environment.
    echo To create one, run: python -m venv venv
    echo.
    pause
    exit /b 1
)

:: Activate the virtual environment
echo [1/3] Activating virtual environment...
call venv\Scripts\activate
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

:: Install/update dependencies
echo [2/3] Checking and installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Some dependencies failed to install. Attempting to start server anyway...
)

:: Run the FastAPI application
echo [3/3] Starting FastAPI server on http://127.0.0.1:8000 ...
echo.
uvicorn main:app --reload

pause
