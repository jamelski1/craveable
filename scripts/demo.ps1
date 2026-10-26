<#
.SYNOPSIS
    Run the Craveable demonstration: the reference scenario, then an infeasible
    variant that forces re-planning and constraint relaxation.
    Uses dist\craveable.exe when present, otherwise the source package.
#>
param([double]$Delay = 0.04)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force out | Out-Null

if (Test-Path dist\craveable.exe) { $run = @(".\dist\craveable.exe") }
else { $run = @("python", "-m", "craveable") }

$scenario = "Find a Turkish restaurant in Downtown Baltimore, MD for two people to have dinner under `$65 on Thursday night at 7:30 pm with a table for two near a window with a view of the garden or the street."
$infeasible = $scenario -replace '\$65', '$45'

Write-Host "`n===== 1/3  version =====" -ForegroundColor Cyan
& $run[0] $run[1..($run.Length)] --version

Write-Host "`n===== 2/3  reference scenario =====" -ForegroundColor Cyan
& $run[0] $run[1..($run.Length)] $scenario --delay $Delay --json out\report.json --log-json out\events.json

Write-Host "`n===== 3/3  infeasible variant (budget `$45) -> re-planning + relaxation =====" -ForegroundColor Cyan
& $run[0] $run[1..($run.Length)] $infeasible --delay $Delay --json out\report_relaxed.json

if ($run[0] -eq "python") {
    python -m craveable $scenario --quiet --chart out\utility.png | Out-Null
    Write-Host "chart: out\utility.png"
}
