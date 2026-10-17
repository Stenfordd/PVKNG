@echo off
cd /d "%~dp0"
echo Rezim dump: zapisuje radky do dump.jsonl. Odehraj souboj, pak okno zavri.
python relay.py --dump
pause
