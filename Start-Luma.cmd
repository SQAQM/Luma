@echo off
if exist "%~dp0Luma.exe" (
  start "" "%~dp0Luma.exe" %*
) else (
  start "" "%~dp0release\Luma-native-1.0.0\Luma.exe" %*
)
