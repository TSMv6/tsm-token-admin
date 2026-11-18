@echo off
REM Copies the signing seed (base64) to the clipboard for the Vercel env var
REM TSM_SEED_B64, without printing it on screen. Paste it into Vercel, then
REM copy something else to clear the clipboard.
setlocal
set "SEED=%~dp0..\..\tsm_dev_guidelines\keys\tsm_ed25519_seed.bin"
if not "%~1"=="" set "SEED=%~1"
if not exist "%SEED%" ( echo Seed not found: %SEED% & exit /b 1 )
python -c "import base64,sys;sys.stdout.write(base64.b64encode(open(sys.argv[1],'rb').read()).decode())" "%SEED%" | clip
echo Seed copied to the clipboard (base64). Paste it as TSM_SEED_B64 in Vercel.
endlocal
