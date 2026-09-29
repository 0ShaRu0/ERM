[Setup]
AppId={{E46F0595-BD3D-47DF-AB01-B3F9B1532845}
AppName=ERM Equipment Rental
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\ERM
DefaultGroupName=ERM
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ERM-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
Source: "build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ERM"; Filename: "{app}\erm_native.exe"
Name: "{autodesktop}\ERM"; Filename: "{app}\erm_native.exe"

[Run]
Filename: "{app}\erm_native.exe"; Description: "Start ERM"; Flags: nowait postinstall skipifsilent
