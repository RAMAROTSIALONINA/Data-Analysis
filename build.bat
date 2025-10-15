@echo off
echo Construction de SWIS Madagascar...
echo.

:: Nettoyer les builds précédents
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "SWIS_Analyse.spec" del "SWIS_Analyse.spec"

:: Construire l'exécutable
pyinstaller run.py ^
--name "SWIS_Analyse" ^
--onefile ^
--add-data "static;static" ^
--hidden-import="google.genai" ^
--hidden-import="google.genai.errors" ^
--hidden-import="pandas" ^
--collect-all="google.genai" ^
--hidden-import="uvicorn.loops.asyncio" ^
--hidden-import="uvicorn.loops.auto" ^
--hidden-import="uvicorn.protocols.http.auto" ^
--hidden-import="uvicorn.protocols.websockets.auto" ^
--hidden-import="uvicorn.logging" ^
--hidden-import="asyncio.windows_events" ^
--clean

if errorlevel 1 (
    echo.
    echo ❌ Erreur lors de la construction!
    pause
    exit /b 1
)

echo.
echo ✅ Construction terminée avec succès!
echo.
echo 📁 L'exécutable se trouve dans: dist\SWIS_Analyse.exe
echo.
echo 🚀 Pour tester: dist\SWIS_Analyse.exe
echo.

pause