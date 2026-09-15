[Setup]
AppName=Nexgen POS
AppVersion=1.2025
AppPublisher=Nexgen
AppPublisherURL=https://nexgenpos.com
AppSupportURL=https://nexgenpos.com/support
AppUpdatesURL=https://nexgenpos.com/updates
DefaultDirName={autopf}\Nexgen POS
DefaultGroupName=Nexgen POS
AllowNoIcons=yes
OutputDir=dist\installer
OutputBaseFilename=NexgenPOS_Setup_1.2025_20260915_162809
SetupIconFile=static\images\nexgen_pos_icon.ico
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\NexgenPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: ".env"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{group}\Nexgen POS"; Filename: "{app}\NexgenPOS.exe"; WorkingDir: "{app}"; IconFilename: "{app}\NexgenPOS.exe"; IconIndex: 0
Name: "{group}\{cm:UninstallProgram,Nexgen POS}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Nexgen POS"; Filename: "{app}\NexgenPOS.exe"; WorkingDir: "{app}"; IconFilename: "{app}\NexgenPOS.exe"; IconIndex: 0; Tasks: desktopicon

[Run]
Filename: "{app}\NexgenPOS.exe"; Description: "{cm:LaunchProgram,Nexgen POS}"; Flags: nowait postinstall skipifsilent hidewizard; WorkingDir: "{app}"

[Code]
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpReady then
  begin
    WizardForm.ReadyMemo.Lines.Add('');
    WizardForm.ReadyMemo.Lines.Add('Installation Details:');
    WizardForm.ReadyMemo.Lines.Add('  • Application will be installed to: ' + ExpandConstant('{app}'));
    WizardForm.ReadyMemo.Lines.Add('  • A desktop shortcut will be created (optional)');
    WizardForm.ReadyMemo.Lines.Add('  • Database files will be preserved if they exist');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    MsgBox('Your data will be preserved. Check the installation folder if you need to backup your database.', mbInformation, MB_OK);
  end;
end;
