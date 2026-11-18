@echo off
REM ==========================================================================
REM  TSM Token Manager - LOCAL emergency copy (this PC only, no GitHub login).
REM  Uses sibling folders:
REM    ..\tsm-token-registry\                      (git clone of the registry)
REM    ..\tsm-auth-endpoint\public\tsm_manifest.json
REM    ..\tsm_dev_guidelines\keys\tsm_ed25519_seed.bin
REM  Pulls the latest registry + manifest first, then opens the browser.
REM ==========================================================================
setlocal
cd /d "%~dp0"
if exist "..\tsm-token-registry\.git" (
  echo Pulling latest registry...
  git -C "..\tsm-token-registry" pull --ff-only
) else (
  echo WARNING: ..\tsm-token-registry is not a git clone. Clone it with:
  echo   git clone https://github.com/TSMv6/tsm-token-registry.git ..\tsm-token-registry
)
if exist "..\tsm-auth-endpoint\.git" (
  echo Pulling latest manifest...
  git -C "..\tsm-auth-endpoint" pull --ff-only
)
python local_server.py
endlocal
