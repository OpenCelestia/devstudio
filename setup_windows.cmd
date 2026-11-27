@echo off
setlocal EnableExtensions
rem Quick CMD wrapper for setup_windows.ps1
rem Usage:
rem   setup_windows.cmd
rem   setup_windows.cmd --install-only
rem   setup_windows.cmd --product anypi_nova
rem   setup_windows.cmd --sdk sdk\AnyCloud39AV100_V2\V2.05

set "ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup_windows.ps1" %*
exit /b %ERRORLEVEL%
