param([string] $InputDir = '.', [string] $Output = 'merged.json')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$files = @(Get-ChildItem -Path $InputDir -Filter 's*.json' -File | ForEach-Object { $_.FullName })
if ($files.Count -eq 0) { throw 'No shard result JSON files found.' }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -B merge_shards.py @files --output $Output
exit $LASTEXITCODE
