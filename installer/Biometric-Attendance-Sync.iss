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
Name: "{commonappdata}\BiometricAttendanceSync\config"
Name: "{commonappdata}\BiometricAttendanceSync\logs"
Name: "{commonappdata}\BiometricAttendanceSync\state"
Name: "{commonappdata}\BiometricAttendanceSync\retry"

[Files]
Source: "..\release\Biometric Attendance Sync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\local_config.py.template"; DestDir: "{commonappdata}\BiometricAttendanceSync\config"; DestName: "local_config.py.template"; Flags: ignoreversion onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\Biometric Attendance Sync Manager"; Filename: "{app}\{#ManagerExe}"
Name: "{autodesktop}\Biometric Attendance Sync Manager"; Filename: "{app}\{#ManagerExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\service\{#ServiceExe}"; Parameters: "install"; StatusMsg: "Installing Windows service..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "config {#ServiceName} start= delayed-auto"; StatusMsg: "Configuring delayed automatic startup..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure {#ServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/60000"; StatusMsg: "Configuring service recovery..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "start {#ServiceName}"; StatusMsg: "Starting Windows service..."; Flags: runhidden waituntilterminated; Check: HasRealConfig

[UninstallRun]
Filename: "{sys}\sc.exe"; Parameters: "stop {#ServiceName}"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\service\{#ServiceExe}"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]
function RealConfigPath(): String;
begin
  Result := ExpandConstant('{commonappdata}\BiometricAttendanceSync\config\local_config.py');
end;

function HasRealConfig(): Boolean;
begin
  Result := FileExists(RealConfigPath());
end;

function ServiceExePath(): String;
begin
  Result := ExpandConstant('{app}\service\{#ServiceExe}');
end;

procedure StopServiceIfPresent();
var
  ResultCode: Integer;
  I: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'query {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then
    exit;

  Exec(ExpandConstant('{sys}\sc.exe'), 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  for I := 1 to 30 do
  begin
    Exec(ExpandConstant('{cmd}'), '/C sc query {#ServiceName} | find "STOPPED"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode = 0 then
      break;
    Sleep(1000);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
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
        'Biometric Attendance Sync was installed, but the Windows service was left stopped because no real local_config.py was found.' + #13#10 + #13#10 +
        'Create the production config at:' + #13#10 +
        RealConfigPath() + #13#10 + #13#10 +
        'A safe template was installed beside it as local_config.py.template.',
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
