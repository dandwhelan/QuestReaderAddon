@echo off
REM GPU-side half of the automation: pick up work whenever Speakstone has some.
REM The Pi wakes this machine; this is what it wakes it *for*.
REM Started by Task Scheduler at logon. Key is read from the user environment.
cd /d "%~dp0"
set QR_REFERENCE_AUDIO=C:\Users\danw\wow.export\sound
python run_batch.py --watch 30 --install --tell-speakstone >> "%~dp0watch.log" 2>&1
