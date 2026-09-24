# Как достучаться до сервера по ssh.
# Если VPN на ПК (Happ в режиме TUN) заворачивает в туннель и локалку, ssh рвётся на рукопожатии —
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
