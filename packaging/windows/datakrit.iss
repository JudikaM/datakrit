; Inno Setup script of the Datakrit Windows app installer (Inno Setup 6).
;
; Build it with packaging/windows/build_installer.py after build_app.py, which passes:
;   /DAppVersion=X.Y.Z  /DSourceDir=<the one-folder build Datakrit\>  /DOutputDir=<folder>
;
; The installer installs for the current user only (no administrator rights) into
; %LOCALAPPDATA%\Programs\Datakrit, adds Datakrit to the Start Menu (and, if chosen, to the
; Desktop), and registers an uninstaller under Settings > Apps. Installing a newer version
; over an older one updates it.

#ifndef AppVersion
  #error Pass /DAppVersion=X.Y.Z
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<folder of the one-folder build>
#endif
#ifndef OutputDir
  #error Pass /DOutputDir=<output folder>
#endif

[Setup]
; Never change AppId: it ties every version together, so that a newer installer updates the
; installed app and the uninstaller finds it.
AppId={{9B4D2E39-8AEB-4D82-BEEF-C4C6AAEF7CEE}
AppName=Datakrit
AppVersion={#AppVersion}
AppVerName=Datakrit {#AppVersion}
AppPublisher=Datakrit
AppPublisherURL=https://github.com/JudikaM/datakrit
AppSupportURL=https://github.com/JudikaM/datakrit/issues
AppUpdatesURL=https://github.com/JudikaM/datakrit/releases
DefaultDirName={autopf}\Datakrit
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Datakrit-{#AppVersion}-windows-x64-setup
SetupIconFile=datakrit.ico
UninstallDisplayIcon={app}\Datakrit.exe
UninstallDisplayName=Datakrit
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Datakrit
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=Datakrit installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The files of the previous version, so that an update leaves none of them behind.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Datakrit"; Filename: "{app}\Datakrit.exe"; Comment: "Check and clean CSV files"
Name: "{autodesktop}\Datakrit"; Filename: "{app}\Datakrit.exe"; Comment: "Check and clean CSV files"; Tasks: desktopicon

[Run]
Filename: "{app}\Datakrit.exe"; Description: "{cm:LaunchProgram,Datakrit}"; Flags: nowait postinstall skipifsilent
