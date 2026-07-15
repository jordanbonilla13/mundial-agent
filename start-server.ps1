$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$Python = Join-Path $ProjectDir "venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "No encuentro el entorno virtual en venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "Crea el entorno e instala dependencias antes de arrancar." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path (Join-Path $ProjectDir ".env"))) {
    Write-Host "Aviso: no encuentro archivo .env en el proyecto." -ForegroundColor Yellow
}

$Port = 8000
$Ips = @()

try {
    $Ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -ne "WellKnown"
        } |
        Select-Object -ExpandProperty IPAddress
} catch {
    Write-Host "Aviso: no pude obtener las IPs locales; usare solo la URL local." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Arrancando Mundial Agent..." -ForegroundColor Green
Write-Host "Local:  http://127.0.0.1:$Port/informe-hoy?perfil=alto_riesgo&modo=pinnacle&mercados=todo&partido=todos" -ForegroundColor Cyan

foreach ($Ip in $Ips) {
    Write-Host "Movil:  http://$Ip`:$Port/informe-hoy?perfil=alto_riesgo&modo=pinnacle&mercados=todo&partido=todos" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Pulsa Ctrl+C para parar el servidor." -ForegroundColor Yellow
Write-Host ""

& $Python -m uvicorn main:app --host 0.0.0.0 --port $Port --reload
