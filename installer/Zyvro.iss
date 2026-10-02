#define MyAppName "Zyvro"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#define MyAppPublisher "Zyvro"
#define MyAppExeName "Zyvro.exe"

[Setup]
AppId={{A9BE62A5-D3A4-4A89-8E69-71EE4BA5BD66}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Zyvro Browser Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\Zyvro
DefaultGroupName=Zyvro
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ZyvroSetup
SetupIconFile=assets\zyvro.ico
UninstallDisplayIcon={app}\Zyvro.exe
UninstallDisplayName={#MyAppName}
WizardStyle=modern
WizardImageFile=assets\installer-sidebar-202.bmp,assets\installer-sidebar-303.bmp,assets\installer-sidebar-404.bmp
WizardImageStretch=yes
WizardKeepAspectRatio=yes
WizardSizePercent=135,145
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
MinVersion=10.0.0
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes
UsePreviousTasks=yes
AllowNoIcons=yes
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=Zyvro Setup
WelcomeLabel1=Welcome to Zyvro Installation Wizard
WelcomeLabel2=This wizard will guide you through the%ninstallation of Zyvro Browser.%n%nIt is recommended that you close all other%napplications before starting Setup. This will%nmake it possible to update relevant system files%nwithout having to reboot your computer.
ClickNext=Click Next to continue.
SelectDirLabel3=Setup will install Zyvro in the following folder.
SelectTasksLabel2=Select the shortcuts you would like Setup to create, then click Next.
InstallingLabel=Please wait while Setup installs Zyvro on your computer.
FinishedHeadingLabel=Zyvro is ready
FinishedLabel=Zyvro has been installed successfully.
ClickFinish=Click Finish to close Setup.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Installation Options:"; Flags: checkedonce
Name: "startmenuicon"; Description: "Add Zyvro to the Start Menu"; GroupDescription: "Installation Options:"; Flags: checkedonce

[Files]
Source: "..\dist\Zyvro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Zyvro"; Filename: "{app}\Zyvro.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Zyvro.exe"; Comment: "Browse with Zyvro"; Tasks: desktopicon
Name: "{userprograms}\Zyvro\Zyvro"; Filename: "{app}\Zyvro.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Zyvro.exe"; Comment: "Browse with Zyvro"; Tasks: startmenuicon
Name: "{userprograms}\Zyvro\Uninstall Zyvro"; Filename: "{uninstallexe}"; WorkingDir: "{app}"; Tasks: startmenuicon

[Registry]
; Register capabilities only. Windows remains responsible for default-app choice.
Root: HKCU; Subkey: "Software\Zyvro"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Zyvro\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "Zyvro"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Zyvro\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Browse with Zyvro"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Zyvro\Capabilities\FileAssociations"; ValueType: string; ValueName: ".htm"; ValueData: "ZyvroHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Zyvro\Capabilities\FileAssociations"; ValueType: string; ValueName: ".html"; ValueData: "ZyvroHTML"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Zyvro\Capabilities\URLAssociations"; ValueType: string; ValueName: "http"; ValueData: "ZyvroURL"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Zyvro\Capabilities\URLAssociations"; ValueType: string; ValueName: "https"; ValueData: "ZyvroURL"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Zyvro"; ValueData: "Software\Zyvro\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\ZyvroHTML"; ValueType: string; ValueData: "Zyvro HTML Document"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\ZyvroHTML\DefaultIcon"; ValueType: string; ValueData: "{app}\Zyvro.exe,0"
Root: HKCU; Subkey: "Software\Classes\ZyvroHTML\shell\open\command"; ValueType: string; ValueData: """{app}\Zyvro.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\ZyvroURL"; ValueType: string; ValueData: "Zyvro URL"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\ZyvroURL"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\ZyvroURL\DefaultIcon"; ValueType: string; ValueData: "{app}\Zyvro.exe,0"
Root: HKCU; Subkey: "Software\Classes\ZyvroURL\shell\open\command"; ValueType: string; ValueData: """{app}\Zyvro.exe"" ""%1"""

[Run]
Filename: "{app}\Zyvro.exe"; Description: "Launch Zyvro"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Browser profiles, bookmarks, settings, history, and cache live outside {app}
; and are deliberately preserved by uninstall.
Type: filesandordirs; Name: "{app}"

[Code]
procedure InitializeWizard;
var
  PortraitWidth: Integer;
begin
  { There is no small top-right logo on destination/install pages. }
  WizardForm.WizardSmallBitmapImage.Visible := False;
  { Keep the supplied artwork at its portrait ratio instead of widening it. }
  PortraitWidth := ScaleX(180);
  WizardForm.WizardBitmapImage.AutoSize := False;
  WizardForm.WizardBitmapImage.Width := PortraitWidth;
  WizardForm.WizardBitmapImage.Stretch := True;

  WizardForm.WelcomeLabel1.Left := PortraitWidth + ScaleX(24);
  WizardForm.WelcomeLabel1.Width :=
    WizardForm.ClientWidth - WizardForm.WelcomeLabel1.Left -
    ScaleX(24);
  WizardForm.WelcomeLabel1.AutoSize := False;
  WizardForm.WelcomeLabel1.Font.Size := 11;
  WizardForm.WelcomeLabel2.Left := WizardForm.WelcomeLabel1.Left;
  WizardForm.WelcomeLabel2.Width := WizardForm.WelcomeLabel1.Width;
  WizardForm.WelcomeLabel2.AutoSize := False;
  WizardForm.WelcomeLabel2.WordWrap := True;
  WizardForm.WelcomeLabel2.Height :=
    WizardForm.WelcomePage.ClientHeight - WizardForm.WelcomeLabel2.Top -
    ScaleY(24);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  Log(Format('Zyvro installer page: %d', [CurPageID]));
  { The portrait belongs only to the first Welcome page. }
  if CurPageID = wpWelcome then
    WizardForm.WizardBitmapImage.Visible := True
  else
  begin
    WizardForm.WizardBitmapImage.Visible := False;
    WizardForm.WizardBitmapImage2.Visible := False;
  end;
  WizardForm.WizardSmallBitmapImage.Visible := False;
  if CurPageID = wpSelectDir then
  begin
    WizardForm.DirBrowseButton.Left :=
      WizardForm.SelectDirPage.ClientWidth - ScaleX(24) -
      WizardForm.DirBrowseButton.Width;
    WizardForm.DirEdit.Width :=
      WizardForm.DirBrowseButton.Left - ScaleX(12) - WizardForm.DirEdit.Left;
    WizardForm.DirBrowseButton.Visible := True;
  end;
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedHeadingLabel.Left := ScaleX(24);
    WizardForm.FinishedHeadingLabel.Width :=
      WizardForm.FinishedPage.ClientWidth - ScaleX(48);
    WizardForm.FinishedLabel.Left := ScaleX(24);
    WizardForm.FinishedLabel.Width :=
      WizardForm.FinishedPage.ClientWidth - ScaleX(48);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    WizardForm.StatusLabel.Caption := 'Preparing installation...';
  if CurStep = ssPostInstall then
    WizardForm.StatusLabel.Caption := 'Registering Zyvro with Windows...';
  if CurStep = ssDone then
    WizardForm.StatusLabel.Caption := 'Finishing installation...';
end;
