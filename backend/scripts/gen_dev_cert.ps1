<#
.SYNOPSIS
    为局域网视频会议系统生成自签 HTTPS 证书（Windows / PowerShell）

.DESCRIPTION
    浏览器的安全上下文规则：只有在 HTTPS 或 localhost 下才暴露 navigator.mediaDevices。
    局域网里用 http://192.168.x.x 打开页面时 getUserMedia 直接是 undefined，
    摄像头/麦克风全部不可用 —— 这正是必须上 HTTPS 的原因。

    本脚本生成一张覆盖 localhost + 本机全部局域网 IPv4 的证书，输出到 <仓库根>/certs/：
        certs/dev-cert.pem   证书（含 SAN）
        certs/dev-key.pem    私钥（部署用，切勿提交到版本库）
    之后把 backend/.env 中 SSL_CERTFILE / SSL_KEYFILE 指向这两个文件，后端即以 https/wss 启动。

.PARAMETER Force
    证书已存在时强制重新生成（注意：指纹变化后浏览器需重新信任证书）。

.NOTES
    依赖 openssl（Git for Windows 自带）。证书为自签，浏览器首次访问需手动信任。
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Find-OpenSsl {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "C:\Program Files\Git\usr\bin\openssl.exe",
        "C:\Program Files\Git\mingw64\bin\openssl.exe",
        "C:\Program Files (x86)\Git\usr\bin\openssl.exe",
        "C:\ProgramData\chocolatey\bin\openssl.exe",
        "C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
        "C:\Program Files\OpenSSL\bin\openssl.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Get-LanIPv4 {
    # 排除回环与链路本地地址，只保留真正的局域网 IPv4
    $ips = @()
    try {
        $ips = @(
            Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -notlike "127.*" -and
                    $_.IPAddress -notlike "169.254.*"
                } |
                Select-Object -ExpandProperty IPAddress -Unique
        )
    } catch {
        $ips = @()
    }
    if ($ips.Count -eq 0) {
        # 退化路径：老系统上没有 Get-NetIPAddress，用 DNS 解析本机名兜底
        try {
            $ips = @(
                [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                    Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                    ForEach-Object { $_.IPAddressToString } |
                    Where-Object { $_ -notlike "127.*" -and $_ -notlike "169.254.*" } |
                    Select-Object -Unique
            )
        } catch {
            $ips = @()
        }
    }
    return $ips
}

$openssl = Find-OpenSsl
if (-not $openssl) {
    Write-Error @"
未找到 openssl，无法生成证书。请任选一种方式安装后重试：
  1) winget install --id Git.Git        （推荐，Git for Windows 自带 openssl）
  2) winget install --id ShiningLight.OpenSSL.Light
安装完成后重开一个终端再执行本脚本。
"@
    exit 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$certsDir = Join-Path $repoRoot "certs"
$certPath = Join-Path $certsDir "dev-cert.pem"
$keyPath = Join-Path $certsDir "dev-key.pem"

if ((Test-Path $certPath) -and (Test-Path $keyPath) -and -not $Force) {
    Write-Host "证书已存在，跳过生成：" -ForegroundColor Yellow
    Write-Host "  $certPath"
    Write-Host "  $keyPath"
    Write-Host "如需重新生成（浏览器需重新信任），请加 -Force 参数。" -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force -Path $certsDir | Out-Null

$lanIPs = Get-LanIPv4

# 组装 SAN：localhost + 回环 + 全部局域网 IPv4
$altLines = New-Object System.Collections.Generic.List[string]
$altLines.Add("DNS.1 = localhost")
$altLines.Add("IP.1 = 127.0.0.1")
$altLines.Add("IP.2 = ::1")
$idx = 3
foreach ($ip in $lanIPs) {
    $altLines.Add("IP.$idx = $ip")
    $idx++
}

$cnfPath = Join-Path $env:TEMP ("meeting-openssl-" + [Guid]::NewGuid().ToString("N") + ".cnf")
$cnf = @"
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
CN = meeting-lan-dev

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
$($altLines -join "`n")
"@
[System.IO.File]::WriteAllText($cnfPath, $cnf, (New-Object System.Text.UTF8Encoding $false))

try {
    Write-Host "使用 openssl：$openssl"
    Write-Host ("SAN 覆盖地址：localhost / 127.0.0.1 / ::1" + $(if ($lanIPs.Count -gt 0) { " / " + ($lanIPs -join " / ") } else { "" }))

    # openssl 会把密钥生成进度写到 stderr；在 2>&1 重定向下 PowerShell 会把它
    # 当成 NativeCommandError，若此时 ErrorActionPreference=Stop 就会中断脚本。
    # 因此这里临时放行错误，只按退出码判断成败。
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 `
            -keyout $keyPath -out $certPath -config $cnfPath 2>&1 | Out-Null
        $opensslExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($opensslExit -ne 0) {
        throw "openssl 执行失败（退出码 $opensslExit）"
    }
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $cnfPath
}

Write-Host ""
Write-Host "证书生成完成：" -ForegroundColor Green
Write-Host "  证书：$certPath"
Write-Host "  私钥：$keyPath"
Write-Host ""
Write-Host "下一步（让后端以 https / wss 启动）：" -ForegroundColor Cyan
Write-Host "  1) 打开（或复制 backend/.env.example 为）backend/.env，加入两行："
Write-Host "       SSL_CERTFILE=$($certPath -replace '\\', '/')"
Write-Host "       SSL_KEYFILE=$($keyPath -replace '\\', '/')"
Write-Host "  2) 重启后端：python run.py     （输出会显示 https:// / wss://）"
Write-Host "  3) 前端 npm run dev 会自动读取同一套证书，改用 HTTPS 监听 5173 端口"
Write-Host ""
Write-Host "注意：自签证书浏览器不认，首次访问请在提示页选择“继续访问”；" -ForegroundColor Yellow
Write-Host "      若需局域网内其他设备免警告，请把 certs/dev-cert.pem 安装为受信任根证书。" -ForegroundColor Yellow