param([string] $Python = '', [string] $OutputDir = '', [string] $WorkDir = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = if ($Python) { $Python } else { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $py)) { throw 'Missing .venv\Scripts\python.exe. Create a Windows Python 3.11 environment first.' }
& $py -c 'import sys; assert sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32'
if ($LASTEXITCODE -ne 0) { throw 'Build requires Windows x64 CPython 3.11.' }
& $py -m PyInstaller --version
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is not installed in .venv. Install it there with: .venv\Scripts\python.exe -m pip install pyinstaller' }
$pyd = Join-Path $PSScriptRoot '.runtime\compiled\descartes_search.cp311-win_amd64.pyd'
if (!(Test-Path -LiteralPath $pyd)) { throw "Missing Windows native kernel: $pyd" }
$nativeCheck = 'import sys; sys.path.insert(0, r"' + (Join-Path $PSScriptRoot '.runtime\compiled') + '"); import descartes_search as c; assert hasattr(c.Search(10**26,10000,10**26+1), "visit_continuation") and hasattr(c.Search(10**26,10000,10**26+1), "assist_check")'
& $py -B -c $nativeCheck
if ($LASTEXITCODE -ne 0) { throw 'Rebuild native kernel first: .\build.ps1; stale .pyd cannot release DFS continuations.' }

$dist = if ($OutputDir) { [IO.Path]::GetFullPath($OutputDir) } else { Join-Path $PSScriptRoot 'dist_gui' }
$work = if ($WorkDir) { [IO.Path]::GetFullPath($WorkDir) } else { Join-Path $env:TEMP 'spoof-build' }
$icon = Join-Path $PSScriptRoot 'assets\icon.ico'
if (!(Test-Path -LiteralPath $icon)) { throw 'Missing assets/icon.ico.' }
if ((Test-Path -LiteralPath (Join-Path $dist 'Spoof')) -or (Test-Path -LiteralPath (Join-Path $dist 'SpoofEngine'))) { throw 'Choose an empty output directory.' }
& $py -B -m PyInstaller --noconfirm --clean --windowed --onedir --name Spoof --icon $icon --add-data "$icon;assets" --distpath $dist --workpath (Join-Path $work 'gui') --specpath $work spoof_gui.py
if ($LASTEXITCODE -ne 0) { throw 'Failed to build GUI.' }
& $py -B -m PyInstaller --noconfirm --clean --windowed --onedir --name SpoofEngine --icon $icon --distpath $dist --workpath (Join-Path $work 'engine') --specpath $work --collect-all flint --exclude-module flint.test --add-binary "$pyd;.runtime\compiled" engine_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Failed to build engine.' }
$guiDir = Join-Path $dist 'Spoof'
$engineDir = [IO.Path]::GetFullPath((Join-Path $dist 'SpoofEngine'))
$destination = [IO.Path]::GetFullPath((Join-Path $guiDir 'SpoofEngine'))
$prefix = [IO.Path]::GetFullPath($dist).TrimEnd('\') + '\'
if (!$engineDir.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or !$destination.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build path escapes output directory.' }
Move-Item -LiteralPath $engineDir -Destination $destination
Write-Host "Built: $guiDir\Spoof.exe"
Write-Host 'Copy the WHOLE Spoof folder. Windows native multiprocessing and dependencies must be tested on the target computer.'
