; Inno Setup script for SwitcherSetup-<version>.exe
; Built by packaging/windows/build.py, which passes AppVersion, SourceDir, OutputDir and IconFile.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F1E6A43-3C4B-4C39-9E0B-5B8C2F6D7A10}
AppName=Switcher
AppVersion={#AppVersion}
AppVerName=Switcher {#AppVersion}
AppPublisher=battaloff
AppPublisherURL=https://github.com/battaloff/Switcher_ENG_RUS
AppSupportURL=https://github.com/battaloff/Switcher_ENG_RUS/issues
; Per-user install: no administrator rights needed.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Switcher
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir={#OutputDir}
OutputBaseFilename=SwitcherSetup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Switcher.exe
UninstallDisplayName=Switcher — умный переключатель раскладки
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app holds this mutex while running: setup asks to close it before updating.
AppMutex=SwitcherSingleInstance
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "autostart"; Description: "Запускать Switcher при входе в Windows"; GroupDescription: "Дополнительно:"
Name: "desktopicon"; Description: "Значок на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Switcher"; Filename: "{app}\Switcher.exe"; Comment: "Умный переключатель раскладки RU/EN"
Name: "{autodesktop}\Switcher"; Filename: "{app}\Switcher.exe"; Tasks: desktopicon

[Registry]
; Same value the app's "Запускать вместе с Windows" checkbox uses.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Switcher"; ValueData: """{app}\Switcher.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\Switcher.exe"; Description: "Запустить Switcher"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Switcher.exe"; Flags: runhidden; RunOnceId: "StopSwitcher"

; The learned profile in %APPDATA%\Switcher is kept on uninstall, so a reinstall
; remembers everything.  Delete that folder by hand to start from scratch.
