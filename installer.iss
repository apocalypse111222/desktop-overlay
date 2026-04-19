#define AppName "Desktop Overlay"
#define AppVersion "1.1.0"
#define AppPublisher "My Projects"
#define AppExeName "Desktop Overlay.exe"
#define SourceDir "dist\Desktop Overlay"

[Setup]
AppId={{A3F2C1D4-8B7E-4F2A-9C3D-1E5B6A7F8D90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Desktop Overlay Setup
SetupIconFile=assets\app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 安装完成后询问是否立即运行
UninstallDisplayIcon={app}\{#AppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startupicon"; Description: "开机时自动启动"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 主程序和所有依赖文件
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; assets 文件夹（托盘图标等）
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式（可选）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\app.ico"; Tasks: desktopicon

[Registry]
; 开机自启（可选任务）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Desktop Overlay"; \
    ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
; 安装完成后询问是否立即启动
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先关闭程序
Filename: "taskkill.exe"; Parameters: "/f /im ""{#AppExeName}"""; Flags: runhidden
