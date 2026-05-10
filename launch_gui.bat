@echo off
cd /d "%~dp0"
title Artemisia v9 -- GUI

echo.
echo  ============================================
echo   Artemisia v9 -- Dashboard
echo  ============================================
echo.
echo  [1] Demarrer normalement (garder les donnees)
echo  [2] Demarrer propre  (effacer toutes les donnees)
echo.
set /p CHOICE=Choix (1 ou 2) : 

if "%CHOICE%"=="2" (
    echo  Nettoyage des donnees...
    python -m gui.app --fresh
) else (
    python -m gui.app
)

pause
