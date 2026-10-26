; Inno Setup script: turns dist\VoicePenPlotter\ (made by PyInstaller) into one
; VoicePenPlotter-Setup-1.0.exe. The user runs it, clicks Next, and the app is installed with
; Start menu / desktop shortcuts and an uninstaller. Nothing else is downloaded.
; Built by packaging\build_windows.bat (or the GitHub workflow).

#define AppName "Voice Pen Plotter"
#define AppVersion "1.0"
#define Owner "MOHAMED OSSAMA ELIWA"
#define ExeName "VoicePenPlotter.exe"

[Setup]
AppId={{6E1B7C2A-4F3D-4C55-9C1E-7A2B5D8E3F41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Owner}
AppCopyright=(c) 2026 {#Owner}
VersionInfoCompany={#Owner}
VersionInfoDescription={#AppName} installer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=VoicePenPlotter-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName} (by {#Owner})
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Install for everyone (asks for admin) or just for this user, as the user prefers.
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\VoicePenPlotter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
