param([string] $Python = '', [string] $OutputDir = '', [string] $WorkDir = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Assert-NonEmptyFile([string] $Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing required file: $Path"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "Empty required file: $Path"
    }
}

$py = if ($Python) { $Python } else { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $py)) { throw 'Missing .venv\Scripts\python.exe. Create a Windows Python 3.11 environment first.' }
& $py -c 'import sys; assert sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32'
if ($LASTEXITCODE -ne 0) { throw 'Build requires Windows x64 CPython 3.11.' }
& $py -m PyInstaller --version
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is not installed in the selected Python environment. Install it there with: <python> -m pip install pyinstaller' }

$licenseRaw = (& $py -B -c 'import importlib.metadata as m, json; print(json.dumps(str(m.distribution(''python-flint'')._path / ''licenses'')))' | Out-String).Trim()
$licenseExit = $LASTEXITCODE
$licenseRoot = if ($licenseRaw) { $licenseRaw | ConvertFrom-Json } else { '' }
if ($licenseExit -ne 0 -or !$licenseRoot) { throw 'Could not locate python-flint license files in the selected Python environment.' }
$licenseRoot = [IO.Path]::GetFullPath($licenseRoot)
if (!(Test-Path -LiteralPath $licenseRoot -PathType Container)) { throw "Missing python-flint license directory: $licenseRoot" }

Assert-NonEmptyFile (Join-Path $PSScriptRoot 'LICENSE')
Assert-NonEmptyFile (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md')
Assert-NonEmptyFile (Join-Path $licenseRoot 'LICENSE')

$licenseFiles = @(Get-ChildItem -LiteralPath $licenseRoot -Recurse -File)
if ($licenseFiles.Count -eq 0) { throw "No license files found under: $licenseRoot" }
foreach ($file in $licenseFiles) {
    if ($file.Length -le 0) { throw "Empty license file: $($file.FullName)" }
}

function Assert-LicenseMatch([string] $Pattern) {
    $match = $licenseFiles | Where-Object { $_.FullName -like $Pattern } | Select-Object -First 1
    if (!$match) { throw "Missing license file matching: $Pattern (under $licenseRoot)" }
}

Assert-LicenseMatch '*\flint-*\COPYING'
Assert-LicenseMatch '*\flint-*\COPYING.LESSER'
Assert-LicenseMatch '*\gmp-*\COPYING'
Assert-LicenseMatch '*\gmp-*\COPYING.LESSERv3'
Assert-LicenseMatch '*\mpfr-*\COPYING'
Assert-LicenseMatch '*\mpfr-*\COPYING.LESSER'
Assert-LicenseMatch '*\winpthreads\COPYING'
Assert-LicenseMatch '*\gcc\COPYING3'
Assert-LicenseMatch '*\gcc\COPYING.LIB'
Assert-LicenseMatch '*\gcc\COPYING.RUNTIME'

$pyd = Join-Path $PSScriptRoot '.runtime\compiled\descartes_search.cp311-win_amd64.pyd'
if (!(Test-Path -LiteralPath $pyd)) { throw "Missing Windows native kernel: $pyd" }
$nativeCheck = "import sys; sys.path.insert(0, r'" + (Join-Path $PSScriptRoot '.runtime\compiled') + "'); import descartes_search as c; assert hasattr(c.Search(10**26,10000,10**26+1), 'visit_continuation') and hasattr(c.Search(10**26,10000,10**26+1), 'assist_check')"
& $py -B -c $nativeCheck
if ($LASTEXITCODE -ne 0) { throw 'Rebuild native kernel first: .\build.ps1; stale .pyd cannot release DFS continuations.' }

$dist = if ($OutputDir) { [IO.Path]::GetFullPath($OutputDir) } else { Join-Path $PSScriptRoot 'dist_gui' }
$work = if ($WorkDir) { [IO.Path]::GetFullPath($WorkDir) } else { Join-Path $env:TEMP 'spoof-build' }
$icon = Join-Path $PSScriptRoot 'assets\icon.ico'
if (!(Test-Path -LiteralPath $icon)) { throw 'Missing assets/icon.ico.' }
if ((Test-Path -LiteralPath (Join-Path $dist 'Spoof')) -or (Test-Path -LiteralPath (Join-Path $dist 'SpoofEngine'))) { throw 'Choose an empty output directory.' }
New-Item -ItemType Directory -Force -Path $work | Out-Null
$guiLog = Join-Path $work 'pyinstaller-gui.log'
$engineLog = Join-Path $work 'pyinstaller-engine.log'
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $py -B -m PyInstaller --noconfirm --clean --windowed --onedir --name Spoof --icon $icon --add-data "$icon;assets" --distpath $dist --workpath (Join-Path $work 'gui') --specpath $work spoof_gui.py *> $guiLog
    $guiExit = $LASTEXITCODE
    if ($guiExit -ne 0) {
        Get-Content -LiteralPath $guiLog -Tail 30
        throw "Failed to build GUI (exit $guiExit); see $guiLog"
    }

    & $py -B -m PyInstaller --noconfirm --clean --windowed --onedir --name SpoofEngine --icon $icon --distpath $dist --workpath (Join-Path $work 'engine') --specpath $work --collect-all flint --exclude-module flint.test --add-binary "$pyd;.runtime\compiled" engine_entry.py *> $engineLog
    $engineExit = $LASTEXITCODE
    if ($engineExit -ne 0) {
        Get-Content -LiteralPath $engineLog -Tail 30
        throw "Failed to build engine (exit $engineExit); see $engineLog"
    }
} finally {
    $ErrorActionPreference = $previousErrorAction
}
$guiDir = Join-Path $dist 'Spoof'
$engineDir = [IO.Path]::GetFullPath((Join-Path $dist 'SpoofEngine'))
$destination = [IO.Path]::GetFullPath((Join-Path $guiDir 'SpoofEngine'))
$prefix = [IO.Path]::GetFullPath($dist).TrimEnd('\') + '\'
if (!$engineDir.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or !$destination.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build path escapes output directory.' }
Move-Item -LiteralPath $engineDir -Destination $destination

Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination $guiDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination $guiDir -Force
Copy-Item -LiteralPath $licenseRoot -Destination (Join-Path $guiDir 'licenses') -Recurse -Force

Assert-NonEmptyFile (Join-Path $guiDir 'LICENSE')
Assert-NonEmptyFile (Join-Path $guiDir 'THIRD_PARTY_NOTICES.md')
$outLicenseDir = Join-Path $guiDir 'licenses'
if (!(Test-Path -LiteralPath $outLicenseDir -PathType Container)) { throw "Missing licenses directory in Windows x64 build: $outLicenseDir" }
foreach ($file in $licenseFiles) {
    $relative = $file.FullName.Substring($licenseRoot.Length).TrimStart('\')
    Assert-NonEmptyFile (Join-Path $outLicenseDir $relative)
}

Write-Host "Built: $guiDir\Spoof.exe"
Write-Host 'Included LICENSE, THIRD_PARTY_NOTICES.md and licenses/ in the Windows x64 build.'
Write-Host 'Copy the WHOLE Spoof folder. Windows native multiprocessing and dependencies must be tested on the target computer.'
