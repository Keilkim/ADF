# ADF Explorer extension

`ADFShell.dll` is a native x64 COM DLL implementing `IShellExtInit`,
`IContextMenu`, and `IClassFactory`. Explorer only loads this small native
component. The PDF application remains a separate process.

The installer copies it as `ADFShell-0.3.26.dll` beside `ADF.exe` and registers
these values under `HKCU\Software\Classes`:

```text
CLSID\{8093F936-820B-4CDB-A64B-7A39EC807A11}\InprocServer32
  (Default) = <install directory>\ADFShell-0.3.26.dll
  ThreadingModel = Apartment
SystemFileAssociations\.pdf\shellex\ContextMenuHandlers\ADF
  (Default) = {8093F936-820B-4CDB-A64B-7A39EC807A11}
```

The association supplements the user's existing PDF default application.
No `DllRegisterServer` or self-registration is used. On Windows 10 the commands
appear in the normal Explorer context menu. Windows 11 exposes this classic
`IContextMenu` extension through **Show more options**. This DLL does not claim
integration with the Windows 11 abbreviated first menu.

| Complete Explorer selection | ADF command |
| --- | --- |
| One existing PDF file | ADF로 PDF 분할… |
| Two or more existing PDF files | ADF로 PDF 병합… |
| Mixed non-PDF files, directories, missing files or empty selection | None |
| Default-only invocation (e.g. normal file opening) | None |

`CF_HDROP` transfers the entire selection to one handler. Each invoke writes
one UTF-8 JSON request and launches one `ADF.exe --shell-request <request>`
process. Selection ordering, Unicode and filenames with spaces or apostrophes
are preserved. No shell, PowerShell or command-line file-list aggregation is
involved. Request names are random GUIDs in
`%LOCALAPPDATA%\ADF\ShellRequests\request-<GUID>.json`; directory and request
files have protected current-user-only DACLs. The application validates and
consumes the request. The bounds are 4,096 files and 8 MiB per request.

## Build and verify

The shared blue A comes from `assets/adf-mark.svg`. `scripts/make-icon.py`
exports real transparent PNG and 32-bit ICO frames, including 20px, 24px and
40px for common display scales. `shell_icon.h` chooses the small-icon size
for the menu owner's DPI, then draws with `DrawIconEx` into a cleared 32-bit
top-down DIB. Explorer receives premultiplied BGRA pixels via `MIIM_BITMAP`.
The old `GetIconInfo` color-bitmap path discarded the separate icon mask.

`tests/test_icons.py` checks the shipped assets through Qt and Windows'
ICO decoder, including transparent corners and premultiplied edges. It reads
only the ICO data file. The native harness separately checks actual DLL
resources at 96–384 DPI and the bitmap attached to the menu item.

```powershell
.\scripts\build-shell.ps1
```

The script downloads the official portable LLVM-MinGW release `20260826`,
checks its pinned SHA-256 and extracts it under `.tools`. No compiler or
runtime is installed globally. C++ libraries are linked statically; the DLL
only imports Windows system libraries and the UCRT included in Windows 10/11.
Only `ADFShell.dll` is copied into the application; test executables and the
toolchain are not packaged. Rebuilding requires network only on first use.

`shell-tests.exe` directly loads the production DLL and supplies an actual
`IDataObject` with a movable `CF_HDROP` global block. It examines real popup
menus and invokes real native commands. A separate test-only executable,
temporarily copied as `ADF.exe` beside a copied DLL, captures requests after
validating their DACL and argv. Tests include selection filtering, canonical
verbs, default-only behavior, object lifetime, changed selections, Korean and
Chinese paths, punctuation, 512 PDFs in one process and the file-count bound.
All generated files are deleted by exact paths inside the unique fixture.
Five corrupt `DROPFILES` inputs verify rejection before out-of-bounds memory
access. The production parser validates the global allocation, header, offset,
alignment, string bounds and the final double NUL before accepting paths.

The harness also asks Windows `SHCreateDefaultContextMenu` to assemble a menu
using a real filesystem shell folder and selected PIDLs. It supplies the
installer's SFA registry layout under a unique volatile test key and uses a
process-local COM class factory. Single PDF, multiple PDFs and mixed PDF/text
selections are checked in the resulting Windows menu. Split and merge also
invoke through Windows' menu routing and verify the complete captured request.
Registry mapping and
the temporary key are removed afterward. These checks do not alter the user's
actual file associations or register the DLL with Explorer. Explorer is never
restarted.

The optional `--component-only` harness argument runs the direct COM, menu
bitmap, selection, parser and request-handoff checks without the separate
Windows-assembled menu checks. `build-shell.ps1 -ComponentTestsOnly` and
`build-windows.ps1 -NativeComponentTestsOnly` select that scope explicitly;
the default remains the complete suite.

The GitHub Windows Server review workflow uses component checks as a build
gate and still runs the complete harness in a separate, non-blocking step.
Its Explorer menu assembly check failed on the initial hosted run. The
outcome is recorded in the job summary and build-origin JSON, including
failures. A successful review build does not establish correct Explorer
integration or installation on supported Windows 10/11 clients.

For a controlled real-application handoff check:

```powershell
.\build\native-shell\shell-tests.exe --invoke-app '<absolute installed DLL>' '<absolute PDF>'
.\build\native-shell\shell-tests.exe --invoke-app '<absolute installed DLL>' '<absolute PDF1>' '<absolute PDF2>'
```

These explicitly open the split/merge application window. The standard build
test only launches the fixture sink, which never shows a window.

## References

- [Microsoft: DrawIconEx alpha handling](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawiconex)
- [Microsoft: CreateDIBSection](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection)
- [Microsoft: DPI-specific system metrics](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetricsfordpi)
- [Microsoft: creating shell extension handlers](https://learn.microsoft.com/en-us/windows/win32/shell/handlers)
- [Microsoft: implementing IContextMenu](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-implement-the-icontextmenu-interface)
- [Microsoft: registering shell extension handlers](https://learn.microsoft.com/en-us/windows/win32/shell/reg-shell-exts)
- [Microsoft: SystemFileAssociations context-menu registration example](https://learn.microsoft.com/en-us/windows/win32/wic/-wic-integrationregentries)
- [Microsoft: Windows default context-menu assembly](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shcreatedefaultcontextmenu)
- [Pinned official LLVM-MinGW release](https://github.com/mstorsjo/llvm-mingw/releases/tag/20260826)

The distribution's `LICENSES/NativeShell` directory contains the runtime
license notices retained from the pinned toolchain.
