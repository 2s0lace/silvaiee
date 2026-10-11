@echo off
python -B "%~dp0pk.py" %*
exit /b %errorlevel%
