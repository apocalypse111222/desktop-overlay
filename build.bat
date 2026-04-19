@echo off
echo Building Desktop Overlay...
pyinstaller --clean --noconfirm desktop_overlay.spec
if %ERRORLEVEL% == 0 (
    echo.
    echo Build successful!
    echo Output: dist\Desktop Overlay\Desktop Overlay.exe
) else (
    echo.
    echo Build failed. Check errors above.
)
pause
