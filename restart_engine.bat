@echo off
cd /d "%~dp0"
title Artemisia v9 -- Restart moteur

echo.
echo  ============================================
echo   Artemisia v9 -- Restart propre du moteur
echo  ============================================
echo.
echo  [1] Normal     (garde les logs - cumule les sessions)
echo  [2] Propre     (archive les vieux logs - GUI fraiche)
echo  [3] Stop seul  (arrete sans relancer)
echo.
set /p CHOICE=Choix (1, 2 ou 3) :

if "%CHOICE%"=="3" (
    python scripts\restart_engine.py --stop-only
    goto end
)
if "%CHOICE%"=="2" (
    python scripts\restart_engine.py --archive
    goto end
)
python scripts\restart_engine.py

:end
echo.
echo  Pour surveiller :  type logs\engine_v9.log
echo  Pour la GUI    :  double-clic sur launch_gui.bat
echo.
pause
