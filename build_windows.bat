@echo off
setlocal
py -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if not exist "tesseract\tesseract.exe" (
  echo ERROR: tesseract\tesseract.exe was not found.
  echo Install Windows Tesseract and copy its entire folder here as .\tesseract
  exit /b 1
)
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "서류검증" ^
  --collect-all fitz --add-data "tesseract;tesseract" app.py
if errorlevel 1 exit /b 1
echo.
echo Build complete: dist\서류검증.exe
