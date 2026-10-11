@echo off
python -B "%~dp0tools\pk\pk.py" %*
exit /b %errorlevel%
