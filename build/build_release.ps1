param(
    [string]$Version = "",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repoRoot "release"

# ---------------------------------------------------------------------------
# Resolve product version
# ---------------------------------------------------------------------------

if (-not $Version) {
    $versionFile = Join-Path $repoRoot "version.py"
    $versionText = Get-Content -LiteralPath $versionFile -Raw

    if ($versionText -notmatch 'PRODUCT_VERSION\s*=\s*"([^"]+)"') {
        throw "Could not read PRODUCT_VERSION from version.py"
    }

    $Version = $Matches[1]
}

if ($Version -notmatch '^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$') {
    throw "Invalid product version: $Version"
}

# ---------------------------------------------------------------------------
# Release paths
# ---------------------------------------------------------------------------

$appStage = Join-Path $releaseRoot "Biometric Attendance Sync"
$versionReleaseDir = Join-Path $releaseRoot $Version

$installerName = "Biometric-Attendance-Sync-Setup-$Version.exe"
$installerBuildDir = Join-Path $releaseRoot "installer"
$installerBuildPath = Join-Path $installerBuildDir $installerName
$finalInstallerPath = Join-Path $versionReleaseDir $installerName

$checksumPath = Join-Path $versionReleaseDir "SHA256SUMS.txt"
$releaseNotesPath = Join-Path $versionReleaseDir "RELEASE_NOTES.md"

# ---------------------------------------------------------------------------
# Safe workspace cleanup
# ---------------------------------------------------------------------------

function Remove-WorkspacePath {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $resolved = (Resolve-Path -LiteralPath $Path).Path

    if (-not $resolved.StartsWith(
        $repoRoot + [System.IO.Path]::DirectorySeparatorChar
    )) {
        throw "Refusing to remove path outside workspace: $resolved"
    }

    Remove-Item -LiteralPath $resolved -Recurse -Force
}

# ---------------------------------------------------------------------------
# Resolve Inno Setup compiler
# ---------------------------------------------------------------------------

function Resolve-InnoCompiler {
    param([string]$RequestedCompiler)

    if ($RequestedCompiler) {
        $candidate = $RequestedCompiler

        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $candidate = Join-Path $repoRoot $candidate
        }

        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }

        throw "Specified Inno Setup compiler was not found: $RequestedCompiler"
    }

    # Prefer the copy already packaged with this repository/build workspace.
    $bundledCompiler = Join-Path `
        $repoRoot `
        "build\tools\innosetup\package\tools\ISCC.exe"

    $candidates = @(
        $bundledCompiler,
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw @"
Inno Setup compiler was not found.

Expected one of:
- build\tools\innosetup\package\tools\ISCC.exe
- Inno Setup 7 installed under Program Files
- Inno Setup 6 installed under Program Files

You may also specify:
-InnoCompiler <path-to-ISCC.exe>
"@
}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

function Assert-FileExists {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description was not produced: $Path"
    }
}

function Assert-DirectoryExists {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Description was not produced: $Path"
    }
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

Push-Location $repoRoot

try {
    Write-Host ""
    Write-Host "============================================="
    Write-Host " Biometric Attendance Sync Release Build"
    Write-Host " Version: $Version"
    Write-Host "============================================="
    Write-Host ""

    $InnoCompiler = Resolve-InnoCompiler $InnoCompiler
    Write-Host "Inno Setup compiler:"
    Write-Host "  $InnoCompiler"
    Write-Host ""

    # Clean generated build artifacts only.
    Remove-WorkspacePath (
        Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Service"
    )

    Remove-WorkspacePath (
        Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Manager"
    )

    Remove-WorkspacePath (
        Join-Path $repoRoot "dist\FPF-Biometric-Sync-Service"
    )

    Remove-WorkspacePath (
        Join-Path $repoRoot "dist\FPF-Biometric-Sync-Manager"
    )

    Remove-WorkspacePath (
        Join-Path $repoRoot "build\service"
    )

    Remove-WorkspacePath (
        Join-Path $repoRoot "build\manager"
    )

    Remove-WorkspacePath $appStage
    Remove-WorkspacePath $versionReleaseDir

    # Remove legacy/generated installer for this version.
    Remove-WorkspacePath $installerBuildPath

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $appStage |
        Out-Null

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $installerBuildDir |
        Out-Null

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $versionReleaseDir |
        Out-Null

    # -----------------------------------------------------------------------
    # Run tests before packaging
    # -----------------------------------------------------------------------

    Write-Host "Running automated tests..."

    & python -m unittest discover -s tests -v

    if ($LASTEXITCODE -ne 0) {
        throw "Automated tests failed. Release build aborted."
    }

    Write-Host ""
    Write-Host "Tests passed."
    Write-Host ""

    # -----------------------------------------------------------------------
    # Build packaged service
    # -----------------------------------------------------------------------

    Write-Host "Building Windows service..."

    & python -m PyInstaller `
        -y `
        --clean `
        "build\service.spec"

    if ($LASTEXITCODE -ne 0) {
        throw "Service PyInstaller build failed."
    }

    # -----------------------------------------------------------------------
    # Build packaged manager
    # -----------------------------------------------------------------------

    Write-Host ""
    Write-Host "Building Manager..."

    & python -m PyInstaller `
        -y `
        --clean `
        "build\manager.spec"

    if ($LASTEXITCODE -ne 0) {
        throw "Manager PyInstaller build failed."
    }

    $managerDist = Join-Path `
        $repoRoot `
        "dist\Biometric-Attendance-Sync-Manager"

    $serviceDist = Join-Path `
        $repoRoot `
        "dist\Biometric-Attendance-Sync-Service"

    $managerExe = Join-Path `
        $managerDist `
        "Biometric-Attendance-Sync-Manager.exe"

    $serviceExe = Join-Path `
        $serviceDist `
        "Biometric-Attendance-Sync-Service.exe"

    Assert-DirectoryExists `
        $managerDist `
        "Manager distribution"

    Assert-DirectoryExists `
        $serviceDist `
        "Service distribution"

    Assert-FileExists `
        $managerExe `
        "Manager executable"

    Assert-FileExists `
        $serviceExe `
        "Service executable"

    # -----------------------------------------------------------------------
    # Stage application
    # -----------------------------------------------------------------------

    Write-Host ""
    Write-Host "Staging application..."

    Copy-Item `
        -Path (Join-Path $managerDist "*") `
        -Destination $appStage `
        -Recurse `
        -Force

    $serviceStage = Join-Path $appStage "service"

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $serviceStage |
        Out-Null

    Copy-Item `
        -Path (Join-Path $serviceDist "*") `
        -Destination $serviceStage `
        -Recurse `
        -Force

    # -----------------------------------------------------------------------
    # Compile installer
    # -----------------------------------------------------------------------

    Write-Host ""
    Write-Host "Compiling installer..."

    & $InnoCompiler `
        "/DAppVersion=$Version" `
        "installer\Biometric-Attendance-Sync.iss"

    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed."
    }

    Assert-FileExists `
        $installerBuildPath `
        "Windows installer"

    # -----------------------------------------------------------------------
    # Assemble final versioned release package
    # -----------------------------------------------------------------------

    Copy-Item `
        -LiteralPath $installerBuildPath `
        -Destination $finalInstallerPath `
        -Force

    # Generate release notes if no manually maintained notes exist.
    $releaseNotes = @"
# Biometric Attendance Sync $Version

## Release

Version: $Version

## Package

- $installerName

## Installation

Run the installer as an administrator.

On a new installation, open Biometric Attendance Sync Manager and complete
the first-run setup wizard.

Customer configuration and encrypted credentials are stored under:

C:\ProgramData\BiometricAttendanceSync

and are preserved during application upgrades.

## Compatibility

- Windows x64
- ERPNext / Frappe HRMS
- ZKTeco attendance devices supported by the product integration layer

## Verification

This release package was generated only after the automated test suite
completed successfully.

See SHA256SUMS.txt to verify installer integrity.
"@

    Set-Content `
        -LiteralPath $releaseNotesPath `
        -Value $releaseNotes `
        -Encoding UTF8

    # -----------------------------------------------------------------------
    # Generate SHA256 checksum
    # -----------------------------------------------------------------------

    $hash = Get-FileHash `
        -LiteralPath $finalInstallerPath `
        -Algorithm SHA256

    "$($hash.Hash)  $installerName" |
        Set-Content `
            -LiteralPath $checksumPath `
            -Encoding ASCII

    Assert-FileExists `
        $releaseNotesPath `
        "Release notes"

    Assert-FileExists `
        $checksumPath `
        "SHA256 checksum file"

    # -----------------------------------------------------------------------
    # Final summary
    # -----------------------------------------------------------------------

    Write-Host ""
    Write-Host "============================================="
    Write-Host " RELEASE BUILD SUCCESSFUL"
    Write-Host "============================================="
    Write-Host ""
    Write-Host "Version:"
    Write-Host "  $Version"
    Write-Host ""
    Write-Host "Release directory:"
    Write-Host "  $versionReleaseDir"
    Write-Host ""
    Write-Host "Installer:"
    Write-Host "  $finalInstallerPath"
    Write-Host ""
    Write-Host "SHA256:"
    Write-Host "  $($hash.Hash)"
    Write-Host ""
}
finally {
    Pop-Location
}