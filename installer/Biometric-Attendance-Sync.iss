#define AppName "Biometric Attendance Sync"
#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
#define AppPublisher "Biometric Attendance Sync"
#define ServiceName "ERPNextBiometricPushService"
#define ManagerExe "Biometric-Attendance-Sync-Manager.exe"
#define ServiceExe "Biometric-Attendance-Sync-Service.exe"

[Setup]
AppId={{F3E3DD71-B7E0-4CF5-A1BF-25F100100100}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Biometric Attendance Sync
DefaultGroupName=Biometric Attendance Sync
DisableProgramGroupPage=yes
OutputDir=..\release\installer
OutputBaseFilename=Biometric-Attendance-Sync-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
Name: "{commonappdata}\BiometricAttendanceSync"
Name: "{commonappdata}\BiometricAttendanceSync\config"
Name: "{commonappdata}\BiometricAttendanceSync\logs"
Name: "{commonappdata}\BiometricAttendanceSync\state"
Name: "{commonappdata}\BiometricAttendanceSync\retry"
Name: "{commonappdata}\BiometricAttendanceSync\secrets"
Name: "{commonappdata}\BiometricAttendanceSync\backups"
Name: "{commonappdata}\BiometricAttendanceSync\diagnostics"

[Files]
; Application binaries only.
;
; Customer configuration is deliberately NOT bundled with the installer.
; The Manager creates and maintains:
;
;   C:\ProgramData\BiometricAttendanceSync\config.json
;
; Existing configuration therefore survives upgrades and reinstallations.
Source: "..\release\Biometric Attendance Sync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Biometric Attendance Sync"; Filename: "{app}\{#ManagerExe}"
Name: "{autodesktop}\Biometric Attendance Sync"; Filename: "{app}\{#ManagerExe}"; Tasks: desktopicon

[Run]
; Register the Windows service on every successful installation.
Filename: "{app}\service\{#ServiceExe}"; Parameters: "install"; StatusMsg: "Installing Windows service..."; Flags: runhidden waituntilterminated

; Configure the service for delayed automatic startup.
Filename: "{sys}\sc.exe"; Parameters: "config {#ServiceName} start= delayed-auto"; StatusMsg: "Configuring delayed automatic startup..."; Flags: runhidden waituntilterminated

; Configure automatic restart if the service unexpectedly fails.
Filename: "{sys}\sc.exe"; Parameters: "failure {#ServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/60000"; StatusMsg: "Configuring service recovery..."; Flags: runhidden waituntilterminated

; Start only when usable configuration already exists.
;
; This covers:
;   1. normal commercial config.json installations
;   2. legacy local_config.py installations during the compatibility period
;
; A brand-new installation remains stopped until setup is completed through
; the Manager.
Filename: "{sys}\sc.exe"; Parameters: "start {#ServiceName}"; StatusMsg: "Starting Windows service..."; Flags: runhidden waituntilterminated; Check: HasRealConfig

; Normal customer entry point after installation.
;
; On an unconfigured machine the Manager automatically opens its first-run
; setup wizard. On an upgrade the checkbox remains optional.
Filename: "{app}\{#ManagerExe}"; Description: "Launch Biometric Attendance Sync"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\sc.exe"; Parameters: "stop {#ServiceName}"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\service\{#ServiceExe}"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]

function CommercialConfigPath(): String;
begin
  Result := ExpandConstant(
    '{commonappdata}\BiometricAttendanceSync\config.json'
  );
end;


function LegacyConfigPath(): String;
begin
  Result := ExpandConstant(
    '{commonappdata}\BiometricAttendanceSync\config\local_config.py'
  );
end;


function HasCommercialConfig(): Boolean;
begin
  Result := FileExists(CommercialConfigPath());
end;


function HasLegacyConfig(): Boolean;
begin
  Result := FileExists(LegacyConfigPath());
end;


function HasRealConfig(): Boolean;
begin
  Result := HasCommercialConfig() or HasLegacyConfig();
end;


function ServiceExePath(): String;
begin
  Result := ExpandConstant(
    '{app}\service\{#ServiceExe}'
  );
end;


procedure StopServiceIfPresent();
var
  ResultCode: Integer;
  I: Integer;
begin
  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'query {#ServiceName}',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );

  if ResultCode <> 0 then
    exit;

  Exec(
    ExpandConstant('{sys}\sc.exe'),
    'stop {#ServiceName}',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );

  { Allow the Windows service time to finish its current operation cleanly. }
  for I := 1 to 30 do
  begin
    Exec(
      ExpandConstant('{cmd}'),
      '/C sc query {#ServiceName} | find "STOPPED"',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );

    if ResultCode = 0 then
      break;

    Sleep(1000);
  end;
end;


function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Important for upgrades: stop the existing service before binaries are
    replaced so files are not locked and an in-progress sync is not cut off
    by the installer. }
  StopServiceIfPresent();

  Result := '';
end;


procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not HasRealConfig() then
    begin
      MsgBox(
        'Biometric Attendance Sync was installed successfully.' + #13#10 + #13#10 +
        'The Windows service has been installed but has not been started ' +
        'because initial configuration has not been completed.' + #13#10 + #13#10 +
        'Open Biometric Attendance Sync and complete the setup wizard.',
        mbInformation,
        MB_OK
      );
    end;
  end;
end;


procedure InitializeUninstallProgressForm();
begin
  StopServiceIfPresent();
end;
