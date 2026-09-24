# Выкладка Account Hub на домашний сервер.
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1          — код (без .env, базы и логов)
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -WithEnv — первый раз: вместе с .env
param([string]$Server = "qwe@192.168.0.14", [switch]$WithEnv)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "net.ps1")
$opts = Get-SshOpts $Server

$exclude = @("--exclude=./.env", "--exclude=*.db", "--exclude=*.db-*", "--exclude=*.log*", "--exclude=__pycache__",
             "--exclude=./venv", "--exclude=./backups")
if ($WithEnv) { $exclude = $exclude | Where-Object { $_ -ne "--exclude=./.env" } }
$archive = Join-Path $env:TEMP "accounthub.tgz"
# системный bsdtar: GNU tar из Git Bash принимает «C:» за удалённый хост
& "$env:WINDIR\System32\tar.exe" -czf $archive -C $root @exclude .
if ($LASTEXITCODE) { throw "tar failed" }

scp @opts $archive "${Server}:/tmp/accounthub.tgz"
if ($LASTEXITCODE) { throw "scp failed" }
Remove-Item $archive
ssh @opts $Server "mkdir -p ~/accounthub && tar -xzf /tmp/accounthub.tgz -C ~/accounthub && rm /tmp/accounthub.tgz && bash ~/accounthub/deploy/after-deploy.sh"
