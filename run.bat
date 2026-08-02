@echo off
REM run.bat — headless-friendly launcher. Starts two random clients, the engine,
REM and opens the board. Press Start on the page to begin; watch the console for the
REM random-vs-random game. Swap a Player(base=...) to Sophia's port for a real run.
cd /d "%~dp0"

timeout /t 1 /nobreak >nul
start "engine" python server.py

timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5050/