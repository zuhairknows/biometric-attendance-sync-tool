<#
.SYNOPSIS
  Read-only snapshot of the sync ledger plus a secret-free device configuration,
  for reviewing the manager dashboard on another PC.

.DESCRIPTION
  Copies the live SQLite ledger with SQLite's online backup API from a read-only
  connection (consistent even while the service runs; the service is not stopped).
  Writes local_config.monitor.py with the API key/secret and device passwords
  removed: it keeps only what the manager reads (devices, shift mapping and the
  SHIFT_SYNC_* settings). Nothing on this PC is changed apart from the new
  output folder on the Desktop.

  Run from an elevated Windows PowerShell 5.1 (the ledger is Administrators-only).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Export-MonitorSnapshot.ps1
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "$env:ProgramFiles\FPFBiometricSyncPhase2",
    [string]$OutputRoot = [Environment]::GetFolderPath('Desktop')
)
$ErrorActionPreference = 'Stop'

$config = Join-Path $AppRoot 'local_config.py'
$python = Join-Path $AppRoot '.venv\Scripts\python.exe'
foreach ($p in $config, $python) { if (-not (Test-Path -LiteralPath $p)) { throw "Not found: $p" } }
$out = Join-Path $OutputRoot ('monitor-snapshot-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $out | Out-Null

$helper = Join-Path $env:TEMP ('monitor_snapshot_' + [guid]::NewGuid().ToString('N') + '.py')
@'
import ast, json, sqlite3, sys, time
from pathlib import Path

config_path, out = Path(sys.argv[1]), Path(sys.argv[2])
tree = ast.parse(config_path.read_text(encoding='utf-8-sig'))
KEEP = {'devices', 'shift_type_device_mapping', 'DURABLE_DB_PATH', 'IMPORT_START_DATE', 'PULL_FREQUENCY',
        'SHIFT_SYNC_RETRY_GRACE_HOURS', 'SHIFT_SYNC_DEVICE_STALE_HOURS', 'SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES',
        'SHIFT_SYNC_IGNORED_DEVICES'}
values = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in KEEP:
            values[node.targets[0].id] = ast.literal_eval(node.value)
for device in values.get('devices', []):
    device.pop('password', None)   # secrets never leave this PC
lines = ['# Monitoring copy: secrets removed. Not usable by the sync service.']
lines += ['%s = %r' % (k, values[k]) for k in sorted(values)]
(out / 'local_config.monitor.py').write_text('\n'.join(lines) + '\n', encoding='utf-8')

db = Path(values.get('DURABLE_DB_PATH', r'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'))
source = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=30)
target = sqlite3.connect(str(out / 'sync.sqlite3'))
began = time.time()
source.backup(target, pages=4096, sleep=0.05)
check = target.execute('PRAGMA quick_check').fetchone()[0]
rows = target.execute('SELECT COUNT(*) FROM punches').fetchone()[0]
target.close(); source.close()
print(json.dumps(dict(ledger=str(db), punches=rows, quick_check=check, seconds=round(time.time() - began, 1))))
'@ | Set-Content -Path $helper -Encoding UTF8
try {
    Write-Host 'Copying ledger (read-only online backup; the service keeps running)...' -ForegroundColor Cyan
    & $python -I $helper $config $out
    if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed' }
} finally {
    # Remove-Item rejects some 8.3 short TEMP paths (e.g. C:\Users\S30CE~1.TAS); delete directly.
    try { [IO.File]::Delete($helper) } catch { }
}

Get-ChildItem -LiteralPath $out | ForEach-Object { Write-Host ("  {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
Write-Host "Done. Copy this folder to the review PC: $out" -ForegroundColor Green
