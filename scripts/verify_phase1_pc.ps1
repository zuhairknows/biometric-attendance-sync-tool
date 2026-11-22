# Read-only inventory. This never imports local_config, changes ACLs, or operates a service.
param(
    [Parameter(Mandatory = $true)][string]$CodeDirectory,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [string]$DatabasePath = 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3',
    [string]$ExpectedCommit = ''
)
$ErrorActionPreference = 'Stop'
$phase1Code = (Resolve-Path -LiteralPath $CodeDirectory).Path.TrimEnd('\')
$phase1Db = [IO.Path]::GetFullPath($DatabasePath)
if (-not [IO.Path]::IsPathRooted($DatabasePath)) { throw 'Database path must be absolute' }
if ($phase1Db.Equals($phase1Code, [StringComparison]::OrdinalIgnoreCase) -or
    $phase1Db.StartsWith($phase1Code + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'NO-GO: database is inside the code checkout'
}
$phase1Service = Get-CimInstance Win32_Service -Filter "Name='ERPNextBiometricPushService'"
if (-not $phase1Service) { throw 'NO-GO: existing service not found; do not install a replacement automatically' }
$phase1Service | Select-Object Name, State, StartMode, StartName, PathName
$phase1Head = (& git -C $phase1Code rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Git revision inspection failed' }
if ($ExpectedCommit -and $phase1Head -ne $ExpectedCommit) { throw 'NO-GO: checkout differs from approved commit' }
Write-Output "Checkout commit: $phase1Head"
& git -C $phase1Code status --short
& git -C $phase1Code check-ignore -- local_config.py
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: local_config.py is not ignored' }
$phase1TrackedConfig = & git -C $phase1Code ls-files -- local_config.py
if ($phase1TrackedConfig) { throw 'NO-GO: local_config.py is tracked' }
& $PythonExecutable -c "import sqlite3,sys; print('Python:',sys.version.split()[0]); print('SQLite:',sqlite3.sqlite_version)"
if ($LASTEXITCODE -ne 0) { throw 'Python/SQLite inspection failed' }
# Parse literal operational settings rather than executing the credential-bearing config.
$phase1ConfigInspector = @'
import ast,datetime,json,re,sys
from pathlib import Path
try: tree=ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
except (OSError,SyntaxError): raise SystemExit('NO-GO: invalid config syntax or read access; inspect locally') from None
wanted={'devices','shift_type_device_mapping','DURABLE_SYNC_ENABLED','DURABLE_DB_PATH','IMPORT_START_DATE'}
values={}
for node in tree.body:
    if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in wanted:
        try: values[node.targets[0].id]=ast.literal_eval(node.value)
        except (ValueError,TypeError): raise SystemExit('NO-GO: nonliteral operational config; audit manually')
devices=values.get('devices',[])
ids=[d.get('device_id') for d in devices]
if not ids or len(set(ids))!=len(ids) or any(not isinstance(i,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',i) for i in ids):
    raise SystemExit('NO-GO: missing/invalid/duplicate device IDs')
if any(d.get('clear_from_device_on_fetch',False) is not False for d in devices):
    raise SystemExit('NO-GO: attendance deletion flag is not False')
enabled=values.get('DURABLE_SYNC_ENABLED',False)
if not isinstance(enabled,bool): raise SystemExit('NO-GO: durable flag is not a boolean')
boundary=values.get('IMPORT_START_DATE')
if boundary is not None and (not isinstance(boundary,str) or not re.fullmatch(r'[0-9]{8}',boundary)):
    raise SystemExit('NO-GO: import boundary is not YYYYMMDD')
if boundary is not None:
    try: datetime.datetime.strptime(boundary,'%Y%m%d')
    except ValueError: raise SystemExit('NO-GO: invalid calendar date') from None
for mapping in values.get('shift_type_device_mapping',[]):
    if not mapping.get('related_device_id') or set(mapping['related_device_id'])-set(ids):
        raise SystemExit('NO-GO: Shift Type mapping has missing device IDs')
safe_devices=[{k:d.get(k) for k in ('device_id','ip','host','port','punch_direction','clear_from_device_on_fetch')} for d in devices]
print(json.dumps({'durable_enabled':enabled,'database_path':values.get('DURABLE_DB_PATH'),
                  'import_start_date':boundary,'devices':safe_devices,
                  'shift_type_device_mapping':values.get('shift_type_device_mapping',[])},ensure_ascii=True,indent=2))
'@
& $PythonExecutable -c $phase1ConfigInspector (Join-Path $phase1Code 'local_config.py')
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: operational config inspection failed' }
$phase1Parent = Split-Path -Parent $phase1Db
if (Test-Path -LiteralPath $phase1Parent) {
    Get-Acl -LiteralPath $phase1Parent | Select-Object Path, Owner, AccessToString
} else {
    Write-Output 'NOT INITIALIZED: database parent does not exist; provision it only after approval'
}
if (Test-Path -LiteralPath $phase1Db) {
    & $PythonExecutable (Join-Path $phase1Code 'sync_cli.py') --db $phase1Db verify
    if ($LASTEXITCODE -ne 0) { throw 'NO-GO: existing ledger integrity/schema validation failed' }
    & $PythonExecutable (Join-Path $phase1Code 'sync_cli.py') --db $phase1Db status
    if ($LASTEXITCODE -ne 0) { throw 'Ledger status inspection failed' }
} else {
    Write-Output 'NOT INITIALIZED: no ledger; no database was created by this inspection'
}
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, Size, FreeSpace
Write-Output 'Inventory complete. ACL correctness, device reachability, credentials and live acceptance still require operator review.'
