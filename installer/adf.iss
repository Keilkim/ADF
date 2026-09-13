#define AppName "ADF"
#define AppVersion "0.3.27"
#define RepoRoot AddBackslash(SourcePath) + ".."
#ifndef AppBuildDir
  #define AppBuildDir RepoRoot + "\dist\ADF"
#endif

[Setup]
AppId={code:GetAppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ADF
AppComments=로컬에서 사용하는 PDF 편집기
DefaultDirName={code:GetDefaultDirName}
DefaultGroupName={code:GetGroupName}
UsePreviousLanguage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
OutputDir={#RepoRoot}\release
OutputBaseFilename=ADF-Setup-{#AppVersion}
SetupIconFile={#RepoRoot}\assets\adf.ico
UninstallDisplayIcon={app}\ADF.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
DisableDirPage=yes
ChangesAssociations=yes
CloseApplications=yes
CloseApplicationsFilter=ADF.exe
RestartApplications=no
AllowNoIcons=yes
UninstallDisplayName=ADF PDF 편집기
VersionInfoVersion={#AppVersion}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
korean.WelcomeLabel1=ADF 설치
korean.WelcomeLabel2=PDF를 읽고, 페이지를 정리하고, 새 파일로 저장하세요.%n%n사용안내, 오픈소스 라이선스 원문과 해당 버전의 소스코드를 함께 설치합니다. 앱의 도움말에서 확인할 수 있습니다.%n%nPython 설치나 계정이 필요하지 않습니다.
korean.FinishedLabel=ADF 설치가 완료되었습니다.%n%nPDF를 ADF에서 항상 열려면 Windows 설정 > 앱 > 기본 앱에서 ADF를 선택하세요.

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; Flags: unchecked

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Excludes: "ADFShell-*.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
; A versioned filename lets an upgrade install without unloading Explorer's DLL.
; Normal version comparison skips an identical DLL on same-version reinstalls.
; Do not use reboot-replacement flags: per-user installation has no admin rights.
Source: "{#AppBuildDir}\ADFShell-{#AppVersion}.dll"; DestDir: "{app}"

[Icons]
Name: "{group}\ADF"; Filename: "{app}\ADF.exe"; AppUserModelID: "ADF.PDF.Editor"
Name: "{group}\사용 안내"; Filename: "{app}\ADF.exe"; Parameters: "--help-section guide"
Name: "{group}\오픈소스 라이선스"; Filename: "{app}\ADF.exe"; Parameters: "--help-section licenses"
Name: "{group}\소스코드"; Filename: "{app}\ADF.exe"; Parameters: "--help-section sources"
Name: "{autodesktop}\ADF"; Filename: "{app}\ADF.exe"; Tasks: desktopicon; AppUserModelID: "ADF.PDF.Editor"; Check: not IsIsolatedTest

[Registry]
; Normal installation expands GetRegistryPrefix to empty. Isolated installer
; verification uses the SAME executable with a private HKCU namespace.
; Never set .pdf's default value or UserChoice, or replace a Windows shell CLSID.
Root: HKCU; Subkey: "{code:GetPrivateTestRoot}"; Flags: uninsdeletekey; Check: IsIsolatedTest
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\ADF.Document"; ValueType: string; ValueName: ""; ValueData: "ADF PDF 문서"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\ADF.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\ADF.exe"",0"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\ADF.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ADF.exe"" ""%1"""
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "ADF.Document"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\Applications\ADF.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "ADF"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\Applications\ADF.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\Applications\ADF.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ADF.exe"" ""%1"""
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\ADF\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "ADF"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\ADF\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "로컬 PDF 보기, 페이지 편집, 병합 및 분리"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\ADF\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\ADF.exe"",0"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\ADF\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "ADF.Document"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\RegisteredApplications"; ValueType: string; ValueName: "ADF"; ValueData: "{code:GetRegistryPrefix}Software\ADF\Capabilities"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shell\ADF.Open"; ValueType: string; ValueName: ""; ValueData: "ADF로 열기"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shell\ADF.Open"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\ADF.exe"",0"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shell\ADF.Open"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Single"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shell\ADF.Open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ADF.exe"" ""%1"""
; Remove 0.1's static split verb when upgrading; native selection logic owns it.
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shell\ADF.Split"; Flags: deletekey dontcreatekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\CLSID\{{8093F936-820B-4CDB-A64B-7A39EC807A11}"; ValueType: string; ValueName: ""; ValueData: "ADF PDF Explorer commands"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\CLSID\{{8093F936-820B-4CDB-A64B-7A39EC807A11}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\ADFShell-{#AppVersion}.dll"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\CLSID\{{8093F936-820B-4CDB-A64B-7A39EC807A11}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"
Root: HKCU; Subkey: "{code:GetRegistryPrefix}Software\Classes\SystemFileAssociations\.pdf\shellex\ContextMenuHandlers\ADF"; ValueType: string; ValueName: ""; ValueData: "{{8093F936-820B-4CDB-A64B-7A39EC807A11}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\ADF.exe"; Description: "ADF 시작"; Flags: nowait postinstall skipifsilent

[Code]
var
  DistributionPage: TInputOptionWizardPage;

function NoticeText: String;
begin
  if ActiveLanguage = 'korean' then
    Result := '무단 배포 금지 · 라이선스 조건 확인' + #13#10#13#10 +
      'ADF는 GNU AGPL v3 이상과 각 구성 요소의 오픈소스 라이선스에 따라 제공됩니다.' + #13#10#13#10 +
      '재배포할 때는 각 라이선스가 요구하는 저작권 고지, 라이선스 원문과 대응 소스를 함께 제공해야 합니다. 이러한 조건을 위반한 무단 배포를 하지 마세요.' + #13#10#13#10 +
      '오픈소스 라이선스가 허용하는 사용·수정·재배포 권리는 그대로 유지됩니다. 설치 후 도움말에서 원문과 소스를 확인할 수 있습니다.'
  else
    Result := 'Redistribution and license notice' + #13#10#13#10 +
      'ADF is provided under GNU AGPL v3 or later and the open-source licenses of its components.' + #13#10#13#10 +
      'Redistribution must include the copyright notices, license texts and corresponding source required by those licenses. Do not distribute in violation of these conditions.' + #13#10#13#10 +
      'The rights to use, modify and redistribute granted by the open-source licenses remain in effect. License texts and sources are available from Help after installation.';
end;

procedure NoticeChecked(Sender: TObject);
begin
  if WizardForm.CurPageID = DistributionPage.ID then
    WizardForm.NextButton.Enabled := DistributionPage.Values[0];
end;

procedure InitializeWizard;
begin
  if ActiveLanguage = 'korean' then begin
    DistributionPage := CreateInputOptionPage(wpWelcome, '배포 조건 확인',
      '설치하기 전에 아래 안내를 확인해 주세요.', NoticeText, False, False);
    DistributionPage.Add('라이선스 조건을 위반한 무단 배포 금지와 위 안내를 확인했습니다. (필수)');
  end else begin
    DistributionPage := CreateInputOptionPage(wpWelcome, 'Redistribution notice',
      'Please read this notice before installing.', NoticeText, False, False);
    DistributionPage.Add('I have read the notice and will follow the redistribution license conditions. (Required)');
  end;
  DistributionPage.Values[0] := False;
  { Unattended deployment requires an explicit acknowledgement for this version.
    A normal interactive installation always starts with an unchecked box. }
  if WizardSilent and (ExpandConstant('{param:ADFACKNOTICE|}') = '{#AppVersion}') then
    DistributionPage.Values[0] := True;
  DistributionPage.CheckListBox.OnClickCheck := @NoticeChecked;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DistributionPage.ID then
    WizardForm.NextButton.Enabled := DistributionPage.Values[0];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = DistributionPage.ID) or (CurPageID = wpReady) then begin
    Result := DistributionPage.Values[0];
    if not Result then Log('ADF redistribution notice has not been acknowledged.');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not DistributionPage.Values[0] then
    Result := 'ADF redistribution notice acknowledgement is required.';
end;

function TestToken: String;
begin
  Result := ExpandConstant('{param:ADFISOLATEDTEST|}');
end;

function IsIsolatedTest: Boolean;
begin
  Result := TestToken <> '';
end;

function GetAppId(Param: String): String;
begin
  if IsIsolatedTest then
    Result := 'ADF.IsolatedInstallerTest.' + TestToken
  else
    Result := '{941DF95F-945A-4A82-BB29-ED83E65BC1B1}';
end;

function GetGroupName(Param: String): String;
begin
  if IsIsolatedTest then
    Result := 'ADF installer test ' + TestToken
  else
    Result := 'ADF';
end;

function GetDefaultDirName(Param: String): String;
begin
  if IsIsolatedTest then
    Result := ExpandConstant('{localappdata}\ADFInstallerSmoke\') + TestToken
  else
    Result := ExpandConstant('{localappdata}\Programs\ADF');
end;

function GetPrivateTestRoot(Param: String): String;
begin
  Result := 'Software\ADFInstallerSmoke\' + TestToken;
end;

function GetRegistryPrefix(Param: String): String;
begin
  if IsIsolatedTest then
    Result := GetPrivateTestRoot('') + '\'
  else
    Result := '';
end;

function InitializeSetup: Boolean;
var
  Token: String;
  I: Integer;
begin
  Result := True;
  Token := TestToken;
  if Token = '' then Exit;
  Result := Length(Token) = 32;
  for I := 1 to Length(Token) do
    if Pos(Token[I], '0123456789abcdef') = 0 then Result := False;
  if not Result then
    MsgBox('Invalid isolated installer test token.', mbError, MB_OK);
end;
