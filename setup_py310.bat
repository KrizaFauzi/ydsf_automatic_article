@echo off
:: ============================================================
:: Setup Virtual Environment untuk Python 3.10.12
:: Jalankan: setup_py310.bat
:: ============================================================

echo [1/5] Mencari Python 3.10...
py -3.10 --version 2>nul
if errorlevel 1 (
    echo ERROR: Python 3.10 tidak ditemukan!
    echo Silakan download dari: https://www.python.org/downloads/release/python-31012/
    pause
    exit /b 1
)

echo [2/5] Membuat virtual environment baru (.venv310)...
if exist .venv310 (
    echo Virtual environment .venv310 sudah ada, skip...
) else (
    py -3.10 -m venv .venv310
)

echo [3/5] Mengaktifkan virtual environment...
call .venv310\Scripts\activate.bat

echo [4/5] Upgrade pip...
python -m pip install --upgrade pip

echo [5/5] Install dependencies dari requirements-py310.txt...
pip install -r requirements-py310.txt

echo.
echo ============================================================
echo  Setup selesai! Aktifkan environment dengan:
echo    .venv310\Scripts\activate
echo  Lalu jalankan app:
echo    uvicorn main:app --reload
echo ============================================================
pause
