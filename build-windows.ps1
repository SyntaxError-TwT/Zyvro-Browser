[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Dist = Join-Path $Root 'dist'
$Build = Join-Path $Root 'build\Zyvro'
$AppOutput = Join-Path $Dist 'Zyvro\Zyvro.exe'
$InstallerOutput = Join-Path $Dist 'ZyvroSetup.exe'
$Cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
$RustManifest = Join-Path $Root 'rust\zyvro-adblock\Cargo.toml'
$RustOutput = Join-Path $Root 'rust\zyvro-adblock\target\release\zyvro_adblock.dll'
$AdblockRuntime = Join-Path $Root 'runtime\adblock'

function Import-MsvcEnvironment {
    if (Get-Command link.exe -ErrorAction SilentlyContinue) {
        return
    }
    $vswhereCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft Visual Studio\Installer\vswhere.exe')
    )
    $vswhere = $vswhereCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $vswhere) {
        throw 'Visual Studio C++ Build Tools were not found. Install the VCTools workload before building Zyvro.'
    }
    $installation = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    $developerCommand = Join-Path $installation 'Common7\Tools\VsDevCmd.bat'
    if (-not (Test-Path -LiteralPath $developerCommand -PathType Leaf)) {
        throw "Visual Studio developer environment was not found at $developerCommand"
    }
    $environment = & cmd.exe /d /s /c "`"$developerCommand`" -no_logo -arch=amd64 -host_arch=amd64 && set"
    foreach ($line in $environment) {
        if ($line -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
    if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
        throw 'Visual Studio initialized, but link.exe is still unavailable.'
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python environment was not found at $Python"
}
if (-not (Test-Path -LiteralPath $Cargo -PathType Leaf)) {
    throw "Rust Cargo was not found at $Cargo"
}

$versionMatch = Select-String -LiteralPath (Join-Path $Root 'browser_maintenance.py') -Pattern '^APP_VERSION\s*=\s*"([^"]+)"'
if (-not $versionMatch) {
    throw 'Could not read APP_VERSION from browser_maintenance.py.'
}
$Version = $versionMatch.Matches[0].Groups[1].Value

if (-not $SkipDependencyInstall) {
    & $Python -m pip install -r (Join-Path $Root 'requirements.txt')
    & $Python -m pip install 'pyinstaller>=6.15,<7' 'pillow>=11,<12'
}

& $Python (Join-Path $Root 'packaging\generate_assets.py') --version $Version

# Build the native adblock-rust bridge before PyInstaller packages the runtime.
# Filter lists remain normal data files so they can be updated independently of
# the pinned Rust engine.
Import-MsvcEnvironment
& $Cargo build --release --locked --manifest-path $RustManifest
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $RustOutput -PathType Leaf)) {
    throw 'Cargo did not produce the native Zyvro ad-block DLL.'
}
New-Item -ItemType Directory -Path $AdblockRuntime -Force | Out-Null
Copy-Item -LiteralPath $RustOutput -Destination (Join-Path $AdblockRuntime 'zyvro_adblock.dll') -Force
Copy-Item -LiteralPath (Join-Path $Root 'rust\zyvro-adblock\LICENSE.txt') -Destination (Join-Path $AdblockRuntime 'LICENSE.txt') -Force

# Developer shells can add unrelated native tools (for example Poppler) to
# PATH. PyInstaller follows PATH while resolving DLLs and can otherwise bundle
# their incompatible ICU/UCRT files as Qt dependencies. Build from a clean
# Windows/Python path so Zyvro contains only its actual runtime.
$PythonHome = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$env:PATH = @(
    (Join-Path $Root '.venv\Scripts'),
    $PythonHome,
    (Join-Path $env:SystemRoot 'System32'),
    $env:SystemRoot
) -join ';'

# Keep source and output cleanup narrowly scoped to Zyvro build artifacts.
if (Test-Path -LiteralPath $Build) {
    Remove-Item -LiteralPath $Build -Recurse -Force
}
foreach ($artifact in @((Join-Path $Dist 'Zyvro'), $InstallerOutput)) {
    if (Test-Path -LiteralPath $artifact) {
        $item = Get-Item -LiteralPath $artifact
        Remove-Item -LiteralPath $artifact -Force -Recurse:$item.PSIsContainer
    }
}
New-Item -ItemType Directory -Path $Dist -Force | Out-Null

Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean (Join-Path $Root 'Zyvro.spec')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $AppOutput)) {
        throw 'PyInstaller did not produce dist\Zyvro\Zyvro.exe.'
    }

    if (-not $SkipInstaller) {
        $innoCandidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
            'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
            'C:\Program Files\Inno Setup 6\ISCC.exe'
        )
        $Iscc = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $Iscc) {
            throw 'Inno Setup 6 was not found. Install JRSoftware.InnoSetup with winget.'
        }
        & $Iscc "/DMyAppVersion=$Version" (Join-Path $Root 'installer\Zyvro.iss')
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $InstallerOutput)) {
            throw 'Inno Setup did not produce dist\ZyvroSetup.exe.'
        }
    }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Build complete.'
Write-Host ''
Write-Host 'Application:'
Write-Host 'dist/Zyvro/Zyvro.exe'
if (-not $SkipInstaller) {
    Write-Host ''
    Write-Host 'Installer:'
    Write-Host 'dist/ZyvroSetup.exe'
}
