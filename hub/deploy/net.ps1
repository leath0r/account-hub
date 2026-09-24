# Общее для deploy.ps1 и hub.ps1: куда деплоить и как достучаться по ssh.

# Адрес сервера (user@host): переменная окружения HUB_SERVER или файл deploy\server.local (в git не попадает).
function Get-HubServer {
    if ($env:HUB_SERVER) { return $env:HUB_SERVER }
    $file = Join-Path $PSScriptRoot "server.local"
    if (Test-Path $file) { return (Get-Content $file -TotalCount 1).Trim() }
    throw "Не задан сервер. Создайте hub\deploy\server.local с одной строкой вида user@192.168.0.10 или задайте `$env:HUB_SERVER."
}

# Если VPN на ПК в режиме TUN заворачивает в туннель и локальную сеть, ssh рвётся на рукопожатии —
# тогда привязываем ssh к адресу реального сетевого интерфейса, и пакеты идут мимо туннеля.
function Get-SshOpts([string]$Server) {
    ssh -o BatchMode=yes -o ConnectTimeout=5 $Server "true" 2>$null
    if ($LASTEXITCODE -eq 0) { return @() }
    $cfg = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" -and $_.InterfaceAlias -notmatch "tun|Tailscale|vEthernet" } |
        Select-Object -First 1
    if (-not $cfg) { return @() }
    return @("-o", "BindAddress=$($cfg.IPv4Address.IPAddress)")
}
