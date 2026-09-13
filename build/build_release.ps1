param(
    [string]$Version = "",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repoRoot "release"
$appStage = Join-Path $releaseRoot "Biometric Attendance Sync"
$installerOut = Join-Path $releaseRoot "installer"

if (-not $Version) {
    $versionFile = Join-Path $repoRoot "version.py"
    $versionText = Get-Content -LiteralPath $versionFile -Raw
    if ($versionText -notmatch 'PRODUCT_VERSION\s*=\s*"([^"]+)"') {
        throw "Could not read PRODUCT_VERSION from version.py"
    }
    $Version = $Matches[1]
}

function Remove-WorkspacePath {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not $resolved.StartsWith($repoRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove path outside workspace: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

Push-Location $repoRoot
try {
    Remove-WorkspacePath (Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Service")
    Remove-WorkspacePath (Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Manager")
    Remove-WorkspacePath (Join-Path $repoRoot "dist\FPF-Biometric-Sync-Service")
    Remove-WorkspacePath (Join-Path $repoRoot "dist\FPF-Biometric-Sync-Manager")
    Remove-WorkspacePath (Join-Path $repoRoot "build\service")
    Remove-WorkspacePath (Join-Path $repoRoot "build\manager")
    Remove-WorkspacePath $appStage
    Remove-WorkspacePath (Join-Path $releaseRoot "FPF Biometric Sync")
    Remove-WorkspacePath (Join-Path $installerOut "FPF-Biometric-Sync-Setup-$Version.exe")
    Remove-WorkspacePath (Join-Path $installerOut "FPF-Biometric-Sync-Setup-$Version.rar")
    New-Item -ItemType Directory -Force -Path $appStage | Out-Null
    New-Item -ItemType Directory -Force -Path $installerOut | Out-Null

    python -m PyInstaller -y --clean "build\service.spec"
    python -m PyInstaller -y --clean "build\manager.spec"

    $managerDist = Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Manager"
    $serviceDist = Join-Path $repoRoot "dist\Biometric-Attendance-Sync-Service"
    if (-not (Test-Path -LiteralPath (Join-Path $managerDist "Biometric-Attendance-Sync-Manager.exe"))) {
        throw "Manager executable was not produced."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $serviceDist "Biometric-Attendance-Sync-Service.exe"))) {
        throw "Service executable was not produced."
    }

    Copy-Item -Path (Join-Path $managerDist "*") -Destination $appStage -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $appStage "service") | Out-Null
    Copy-Item -Path (Join-Path $serviceDist "*") -Destination (Join-Path $appStage "service") -Recurse -Force

    if (-not $InnoCompiler) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        )
        foreach ($candidate in $candidates) {
            if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                $InnoCompiler = $candidate
                break
            }
        }
    }

    if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
        Write-Warning "Inno Setup compiler not found. Staging was created, but Setup.exe was not built."
        exit 2
    }

    & $InnoCompiler "/DAppVersion=$Version" "installer\Biometric-Attendance-Sync.iss"
}
finally {
    Pop-Location
}
