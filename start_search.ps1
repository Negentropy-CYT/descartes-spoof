param(
    [string] $Bound = '100000000000000000000000000000000000',
    [string] $PrimeCap = 'auto',
    [string] $NodeCap = 'none',
    [int] $Workers = 12,
    [int] $SplitDepth = 4,
    [int] $ChunkSize = 16,
    [int] $Shards = 1,
    [string] $Owned = '',
    [string] $DeviceId = '',
    [string] $OutputDir = '',
    [switch] $Resume,
    [switch] $Fresh
)
$ErrorActionPreference = 'Stop'
if ($Resume -and $Fresh) { throw 'Use either -Resume or -Fresh, not both.' }
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Python environment missing: .venv' }
$arglist = @('-B', 'runner.py', '--bound', $Bound, '--prime-cap', $PrimeCap,
             '--node-cap', $NodeCap, '--workers', [string]$Workers,
             '--split-depth', [string]$SplitDepth, '--chunksize', [string]$ChunkSize,
             '--shards', [string]$Shards, '--checkpoint-seconds', '900',
             '--progress-seconds', '30',
             '--start-method', 'spawn')
if ($OutputDir) { $arglist += @('--output-dir', $OutputDir) }
if ($Owned) { $arglist += @('--owned', $Owned) }
if ($DeviceId) { $arglist += @('--device-id', $DeviceId) }
if ($Resume) { $arglist += '--resume' }
if ($Fresh) { $arglist += '--fresh' }
& $python @arglist
exit $LASTEXITCODE
