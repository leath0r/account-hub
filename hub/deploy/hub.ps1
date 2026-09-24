# Управление ботом на сервере с ПК:  deploy\hub.ps1 <команда hubctl>
#   deploy\hub.ps1 status | restart | logs 100 | logs -f | claim | backup | selftest | proxy set
# Другой сервер — через переменную окружения HUB_SERVER.
$Server = if ($env:HUB_SERVER) { $env:HUB_SERVER } else { "qwe@192.168.0.14" }
$Cmd = if ($args.Count) { @($args) } else { @("help") }
. (Join-Path $PSScriptRoot "net.ps1")
$opts = Get-SshOpts $Server
# nano, tail -f и скрытый ввод ссылки в proxy set требуют терминал
$tty = if ($Cmd[0] -eq "env" -or $Cmd -contains "-f" -or ($Cmd[0] -eq "proxy" -and $Cmd -contains "set")) { @("-t") } else { @() }
ssh @opts @tty $Server "~/.local/bin/hubctl $($Cmd -join ' ')"
