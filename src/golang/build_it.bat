@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "ROOT_DIR=%%~fI"
if not exist "%ROOT_DIR%\build" mkdir "%ROOT_DIR%\build"
pushd "%SCRIPT_DIR%" || exit /b 1
set "CGO_ENABLED=0"

rem Windows
set "GOOS=windows"
set "GOARCH=amd64"
go build -o "%ROOT_DIR%\build\dapm.exe" . || exit /b 1

rem Intel Mac
set "GOOS=darwin"
set "GOARCH=amd64"
go build -o "%ROOT_DIR%\build\dapm-mac-intel" . || exit /b 1

rem Apple Silicon Mac (M1/M2/M3/M4)
set "GOARCH=arm64"
go build -o "%ROOT_DIR%\build\dapm-mac-arm64" . || exit /b 1

popd
endlocal
