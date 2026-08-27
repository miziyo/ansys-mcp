$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root "runtime"
$uvCache = Join-Path $root ".cache\uv"
$runtimeTemp = Join-Path $root ".tmp\runtime"
$pycache = Join-Path $root ".tmp\pycache"

foreach ($path in @($runtime, $uvCache, $runtimeTemp, $pycache)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$env:ANSYS_RESEARCH_ROOT = $root
$env:ANSYS_RESEARCH_RUNTIME = $runtime
$env:UV_CACHE_DIR = $uvCache
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
$env:PYTHONPYCACHEPREFIX = $pycache
$env:PYTHONUTF8 = "1"
$env:PYVISTA_OFF_SCREEN = "true"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"

$activate = Join-Path $root ".venv\Scripts\Activate.ps1"
if (Test-Path -LiteralPath $activate) {
    . $activate
}

Set-Location -LiteralPath $root
Write-Host "Ansys research runner: $root"
Write-Host "Runtime: $runtime"
