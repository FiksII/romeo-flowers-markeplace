param(
    [ValidateRange(1, 52)][int]$Weeks = 6,
    [ValidateRange(1, 20)][int]$OrdersPerDay = 4,
    [string]$EndDate = ""
)

$ErrorActionPreference = "Stop"
$demoProjectRoot = Split-Path -Parent $PSScriptRoot
$demoPython = Join-Path $demoProjectRoot '.venv\Scripts\python.exe'
$demoPreviousUtf8 = $env:PYTHONUTF8
if (-not (Test-Path -LiteralPath $demoPython)) {
    throw "Сначала выполните uv sync в папке marketplace."
}
Push-Location -LiteralPath $demoProjectRoot
try {
    $env:PYTHONUTF8 = '1'
    & $demoPython manage.py migrate --settings=romeo_market.demo_settings --noinput
    if ($LASTEXITCODE -ne 0) { throw "Не удалось применить миграции демо-базы." }
    $demoArguments = @('manage.py', 'seed_demo_history', '--settings=romeo_market.demo_settings', '--weeks', $Weeks, '--orders-per-day', $OrdersPerDay)
    if ($EndDate) { $demoArguments += @('--end-date', $EndDate) }
    & $demoPython @demoArguments
    if ($LASTEXITCODE -ne 0) { throw "Не удалось заполнить демо-базу." }
    Write-Host ""
    Write-Host "Кабинеты: http://127.0.0.1:8010/partner/ и http://127.0.0.1:8010/operator/"
    Write-Host "Если сервер ещё не запущен:"
    Write-Host ".venv\Scripts\python.exe manage.py runserver 127.0.0.1:8010 --settings=romeo_market.demo_settings"
}
finally {
    $env:PYTHONUTF8 = $demoPreviousUtf8
    Pop-Location
}
