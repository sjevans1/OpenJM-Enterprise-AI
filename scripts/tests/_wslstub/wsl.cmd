@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wsl_stub.ps1" %*
