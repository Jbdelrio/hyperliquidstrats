@echo off
REM ═══════════════════════════════════════════════════════════════════
REM  Artemisia v9  —  GUI launcher
REM  Accès local : http://artemisia.local:8050
REM
REM  PREMIÈRE UTILISATION : lancer en tant qu'Administrateur une fois
REM  pour ajouter artemisia.local dans le fichier hosts Windows.
REM  Les fois suivantes : double-clic normal suffit.
REM ═══════════════════════════════════════════════════════════════════

setlocal
set PORT=8050
set HOSTS_FILE=C:\Windows\System32\drivers\etc\hosts
set HOST_ENTRY=127.0.0.1 artemisia.local

echo.
echo  ┌─────────────────────────────────────────────┐
echo  │         ARTEMISIA v9  —  Dashboard          │
echo  └─────────────────────────────────────────────┘
echo.

REM ── Vérifier si artemisia.local est déjà dans hosts ────────────────
findstr /C:"%HOST_ENTRY%" "%HOSTS_FILE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo  [OK] artemisia.local est configuré.
    goto :start_artemisia
)

REM ── artemisia.local absent → essayer d'ajouter (besoin d'admin) ────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [WARN] Pas de droits Administrateur.
    echo.
    echo  Pour utiliser http://artemisia.local:%PORT% :
    echo    1. Clic droit sur ce fichier → "Exécuter en tant qu'admin"
    echo    2. Ou ajouter manuellement dans %HOSTS_FILE% :
    echo         %HOST_ENTRY%
    echo.
    echo  Démarrage sur http://127.0.0.1:%PORT% à la place...
    echo.
    goto :start_plain
)

echo %HOST_ENTRY%>> "%HOSTS_FILE%"
echo  [OK] artemisia.local ajouté au fichier hosts.

:start_artemisia
echo.
echo  ► Ouvrir dans le navigateur : http://artemisia.local:%PORT%
echo.
python -m gui.app --host 0.0.0.0 --port %PORT%
goto :eof

:start_plain
echo  ► Ouvrir dans le navigateur : http://127.0.0.1:%PORT%
echo.
python -m gui.app --host 127.0.0.1 --port %PORT%
