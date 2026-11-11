param(
    [string] $Toolchain = $env:SPOOF_TOOLCHAIN
)
$ErrorActionPreference = 'Stop'
if (!$Toolchain) {
    throw 'Toolchain path not set. Pass -Toolchain <dir> or set the SPOOF_TOOLCHAIN environment variable.'
}
$python = Join-Path $Toolchain 'build_env\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw "Build environment missing: $python" }
$previous = $env:SPOOF_TOOLCHAIN
try {
    $env:SPOOF_TOOLCHAIN = $Toolchain
    & $python -B (Join-Path $PSScriptRoot 'build_kernel.py')
    if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $LASTEXITCODE" }
} finally {
    $env:SPOOF_TOOLCHAIN = $previous
}
