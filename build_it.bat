@echo off
setlocal

rem Windows
go build -o dapm.exe . || exit /b 1

rem Intel Mac
set "GOOS=darwin"
set "GOARCH=amd64"
go build -o dapm-mac-intel . || exit /b 1

rem Apple Silicon Mac (M1/M2/M3/M4)
set "GOARCH=arm64"
go build -o dapm-mac-arm64 . || exit /b 1

endlocal
