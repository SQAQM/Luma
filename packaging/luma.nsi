Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
Name "Luma ${LUMA_VERSION}"
OutFile "${LUMA_OUTPUT}"
InstallDir "$EXEDIR\Luma"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Luma.WinUI.D412B8DB" "InstallLocation"
VIProductVersion "${LUMA_FILE_VERSION}"
VIAddVersionKey /LANG=2052 "ProductName" "Luma 图片管理"
VIAddVersionKey /LANG=2052 "FileDescription" "Luma 原生桌面安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${LUMA_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Luma Contributors"
BrandingText "Luma"
ShowInstDetails show
ShowUninstDetails show
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Luma.WinUI.D412B8DB"
!define IDENTITY "LUMA-WINUI-D412B8DB-INSTALL-v1"
!define MUI_ICON "${LUMA_BUNDLE}\Assets\Luma.ico"
!define MUI_UNICON "${LUMA_BUNDLE}\Assets\Luma.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 Luma ${LUMA_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Luma 图片与模型管理。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Luma.exe"
!define MUI_FINISHPAGE_RUN_TEXT "打开 Luma"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_UNCONFIRMPAGE_TEXT_TOP "卸载 Luma"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Var UnsafeDirectory
!include "${GENERATED_INPUT}"

!macro SafetyFunctions PREFIX
Function ${PREFIX}CheckDirectory
  Exch $0
  Push $1
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1'
  StrCmp $1 -1 directory_done
  IntOp $1 $1 & 0x400
  StrCmp $1 0 directory_done
  StrCpy $UnsafeDirectory 1
directory_done:
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}CheckFile
  Exch $0
  Push $1
  Push $2
  Push $3
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1'
  StrCmp $1 -1 file_done
  IntOp $1 $1 & 0x410
  StrCmp $1 0 +2
  Goto file_unsafe
  System::Call 'kernel32::CreateFileW(w r0, i 0, i 7, p 0, i 3, i 0x02200000, p 0) p.r1'
  StrCmp $1 -1 file_unsafe
  System::Alloc 52
  Pop $2
  StrCmp $2 0 file_no_memory
  System::Call 'kernel32::GetFileInformationByHandle(p r1, p r2) i.r3'
  System::Call 'kernel32::CloseHandle(p r1)'
  StrCmp $3 0 file_release_unsafe
  System::Call '*$2(i .r3)'
  IntOp $3 $3 & 0x410
  StrCmp $3 0 +2
  Goto file_release_unsafe
  IntOp $2 $2 + 40
  System::Call '*$2(i .r3)'
  IntOp $2 $2 - 40
  System::Free $2
  IntCmp $3 1 file_done file_unsafe file_unsafe
file_release_unsafe:
  System::Free $2
  Goto file_unsafe
file_no_memory:
  System::Call 'kernel32::CloseHandle(p r1)'
file_unsafe:
  StrCpy $UnsafeDirectory 1
file_done:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}ValidateDestination
  StrCpy $UnsafeDirectory 0
  StrCpy $0 $INSTDIR
  System::Call 'kernel32::GetFullPathNameW(w r0, i ${NSIS_MAX_STRLEN}, w .r1, p 0) i.r2'
  IntCmp $2 0 unsafe_destination unsafe_destination +1
  IntCmp $2 900 +1 +1 unsafe_destination
  StrCpy $INSTDIR $1
  StrLen $0 $INSTDIR
  IntCmp $0 3 unsafe_destination unsafe_destination +1
  IntCmp $0 900 +1 +1 unsafe_destination
  StrCpy $0 $INSTDIR 2
  StrCmp $0 "\\" unsafe_destination
  StrCpy $0 $INSTDIR
ancestor_loop:
  Push $0
  Call ${PREFIX}CheckDirectory
  ${GetParent} $0 $1
  StrCmp $1 "" ancestors_done
  StrCmp $1 $0 ancestors_done
  StrCpy $0 $1
  Goto ancestor_loop
ancestors_done:
  !insertmacro CheckPayloadDirectories "${PREFIX}"
  !insertmacro CheckPayloadFiles "${PREFIX}"
  StrCmp $UnsafeDirectory 0 +2
  Return
  IfFileExists "$INSTDIR\." 0 destination_done
  ClearErrors
  FileOpen $0 "$INSTDIR\luma-install.identity" r
  IfErrors unsafe_destination
  FileRead $0 $1 128
  FileClose $0
  StrCmp $1 "${IDENTITY}$\r$\n" destination_done
unsafe_destination:
  StrCpy $UnsafeDirectory 1
destination_done:
FunctionEnd

Function ${PREFIX}CheckNotRunning
  IfFileExists "$INSTDIR\Luma.exe" 0 worker_check
  ClearErrors
  FileOpen $0 "$INSTDIR\Luma.exe" a
  IfErrors program_busy
  FileClose $0
worker_check:
  IfFileExists "$INSTDIR\engine\Luma.Engine.exe" 0 running_done
  ClearErrors
  FileOpen $0 "$INSTDIR\engine\Luma.Engine.exe" a
  IfErrors program_busy
  FileClose $0
  Goto running_done
program_busy:
  MessageBox MB_OK|MB_ICONSTOP "程序文件正在使用或不可写。请先正常关闭 Luma 和运行任务后重试。" /SD IDOK
  SetErrorLevel 4
  Quit
running_done:
FunctionEnd
!macroend
!insertmacro SafetyFunctions ""
!insertmacro SafetyFunctions "un."

Function InstallFailed
  MessageBox MB_OK|MB_ICONSTOP "无法安全写入程序文件，安装未完成。请关闭占用程序，检查目录权限后重试。" /SD IDOK
  SetErrorLevel 5
  Quit
FunctionEnd

Function PreparePayloadFile
  Exch $0
  Push $0
  Call CheckFile
  StrCmp $UnsafeDirectory 0 +2
  Call InstallFailed
  IfFileExists "$0" 0 payload_prepared
  ClearErrors
  Delete "$0"
  IfErrors 0 +2
  Call InstallFailed
payload_prepared:
  Pop $0
FunctionEnd

Function un.DeletePayloadFile
  Exch $0
  IfFileExists "$0" 0 payload_deleted
  ClearErrors
  Delete "$0"
  IfErrors 0 payload_deleted
  MessageBox MB_OK|MB_ICONSTOP "部分程序文件仍被占用，卸载未完成。请正常关闭相关程序后重试。图库、原图和模型仍保留。" /SD IDOK
  SetErrorLevel 5
  Quit
payload_deleted:
  Pop $0
FunctionEnd

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    Goto unsupported_system
  ${EndIf}
  System::Alloc 64
  Pop $0
  StrCmp $0 0 unsupported_system
  System::Call 'kernel32::GetNativeSystemInfo(p r0)'
  System::Call '*$0(&i2 .r1)'
  System::Free $0
  IntCmp $1 9 architecture_ok unsupported_system unsupported_system
architecture_ok:
  SetRegView 64
  ReadRegStr $0 HKLM "Software\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
  IntCmp $0 19041 system_ok unsupported_system system_ok
unsupported_system:
  MessageBox MB_OK|MB_ICONSTOP "需要 x64 Windows 10（2004 / 19041）或更高版本。" /SD IDOK
  SetErrorLevel 3
  Quit
system_ok:
FunctionEnd

Function .onVerifyInstDir
  Call ValidateDestination
  StrCmp $UnsafeDirectory 0 valid_directory
  SetErrors
  Return
valid_directory:
  ClearErrors
FunctionEnd

Section "程序文件"
  SetShellVarContext current
  SetRegView 64
  Call ValidateDestination
  StrCmp $UnsafeDirectory 0 +4
  MessageBox MB_OK|MB_ICONSTOP "请选择新的程序目录，或由本安装器创建的 Luma 目录；不覆盖其它目录，也不使用目录链接。" /SD IDOK
  SetErrorLevel 4
  Quit
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 "" registration_ok
  StrCmp $0 $INSTDIR registration_ok
  MessageBox MB_OK|MB_ICONSTOP "检测到另一个安装目录。请先正常卸载已有 Luma，再选择新位置；图库和原图会保留。" /SD IDOK
  SetErrorLevel 4
  Quit
registration_ok:
  Call CheckNotRunning
  ClearErrors
  CreateDirectory "$INSTDIR"
  Push "$INSTDIR\luma-install.identity"
  Call PreparePayloadFile
  FileOpen $0 "$INSTDIR\luma-install.identity" w
  FileWrite $0 "${IDENTITY}$\r$\n"
  FileClose $0
  IfErrors 0 +2
  Call InstallFailed
  SetOverwrite on
  !insertmacro InstallPayload
  SetOutPath "$INSTDIR"
  Push "$INSTDIR\Uninstall.exe"
  Call PreparePayloadFile
  ClearErrors
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors 0 +2
  Call InstallFailed
  CreateDirectory "$SMPROGRAMS\Luma WinUI"
  CreateShortcut "$SMPROGRAMS\Luma WinUI\Luma.lnk" "$INSTDIR\Luma.exe" "" "$INSTDIR\Assets\Luma.ico"
  CreateShortcut "$SMPROGRAMS\Luma WinUI\卸载 Luma.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Luma ${LUMA_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${LUMA_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Luma Contributors"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Assets\Luma.ico"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${LUMA_SIZE_KIB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  Call un.ValidateDestination
  StrCmp $UnsafeDirectory 0 +4
  MessageBox MB_OK|MB_ICONSTOP "无法确认程序目录的身份或目录已变为链接，未删除文件。" /SD IDOK
  SetErrorLevel 4
  Quit
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 $INSTDIR +4
  MessageBox MB_OK|MB_ICONSTOP "安装登记与当前目录不一致，未删除文件。" /SD IDOK
  SetErrorLevel 4
  Quit
  Call un.CheckNotRunning
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  !insertmacro RemovePayload
  Delete "$INSTDIR\luma-install.identity"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\Luma WinUI\Luma.lnk"
  Delete "$SMPROGRAMS\Luma WinUI\卸载 Luma.lnk"
  RMDir "$SMPROGRAMS\Luma WinUI"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
