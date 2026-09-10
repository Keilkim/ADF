#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
#include "shell_icon.h"

namespace {
const CLSID kClassId = {0x8093f936, 0x820b, 0x4cdb, {0xa6, 0x4b, 0x7a, 0x39, 0xec, 0x80, 0x7a, 0x11}};
using GetFactory = HRESULT(__stdcall*)(REFCLSID, REFIID, void**);
using CanUnload = HRESULT(__stdcall*)();
int checks = 0;
void Check(bool valid, const char* message) { ++checks; if (!valid) throw std::runtime_error(message); }
void CheckHr(HRESULT result, const char* message) { if (FAILED(result)) std::printf("HRESULT: %08lx\n", static_cast<unsigned long>(result)); Check(SUCCEEDED(result), message); }

void CheckMenuIcon(HBITMAP bitmap, UINT dpi) {
    DIBSECTION section{};
    Check(GetObjectW(bitmap, sizeof(section), &section) == sizeof(section), "Menu icon is a DIB section");
    Check(section.dsBm.bmBitsPixel == 32 && section.dsBm.bmBits, "Menu icon retains 32-bit alpha pixels");
    Check(section.dsBm.bmWidth == std::clamp(GetSystemMetricsForDpi(SM_CXSMICON, dpi), 16, 64), "Menu icon follows display DPI");
    const auto* bytes = static_cast<const BYTE*>(section.dsBm.bmBits);
    bool opaque = false, translucent = false, transparent = false;
    for (int i = 0; i < section.dsBm.bmWidth * section.dsBm.bmHeight; ++i) {
        const auto* p = bytes + i * 4;
        Check(p[0] <= p[3] && p[1] <= p[3] && p[2] <= p[3], "Menu bitmap uses premultiplied alpha");
        if (!p[3]) transparent = true;
        else if (p[3] == 255) {
            opaque = true;
            Check(p[0] > p[1] && p[1] > p[2], "Logo interior is blue, without background artifacts");
        } else translucent = true;
    }
    Check(opaque && translucent && transparent, "Menu icon has solid A, antialiased edges and transparent background");
    Check(bytes[3] == 0, "Menu icon corner remains transparent");
}

class DropData final : public IDataObject {
    ULONG references_ = 1;
    std::vector<wchar_t> paths_;
    std::vector<BYTE> raw_;
public:
    DropData(const std::vector<BYTE>& raw, int) : raw_(raw) {}
    explicit DropData(const std::vector<std::wstring>& files) {
        for (const auto& file : files) { paths_.insert(paths_.end(), file.begin(), file.end()); paths_.push_back(0); }
        paths_.push_back(0);
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDataObject) return E_NOINTERFACE;
        *result = static_cast<IDataObject*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG remaining = --references_; if (!remaining) delete this; return remaining; }
    HRESULT STDMETHODCALLTYPE GetData(FORMATETC* format, STGMEDIUM* medium) override {
        if (!format || !medium) return E_POINTER;
        if (format->cfFormat != CF_HDROP || !(format->tymed & TYMED_HGLOBAL)) return DV_E_FORMATETC;
        const size_t size = raw_.empty() ? sizeof(DROPFILES) + paths_.size() * sizeof(wchar_t) : raw_.size();
        HGLOBAL storage = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size);
        if (!storage) return E_OUTOFMEMORY;
        auto* drop = static_cast<DROPFILES*>(GlobalLock(storage));
        if (!drop) { GlobalFree(storage); return E_OUTOFMEMORY; }
        if (!raw_.empty()) memcpy(drop, raw_.data(), raw_.size());
        else {
            drop->pFiles = sizeof(DROPFILES);
            drop->fWide = TRUE;
            memcpy(reinterpret_cast<BYTE*>(drop) + sizeof(DROPFILES), paths_.data(), paths_.size() * sizeof(wchar_t));
        }
        GlobalUnlock(storage);
        medium->tymed = TYMED_HGLOBAL;
        medium->hGlobal = storage;
        medium->pUnkForRelease = nullptr;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDataHere(FORMATETC*, STGMEDIUM*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE QueryGetData(FORMATETC* format) override { return format && format->cfFormat == CF_HDROP ? S_OK : DV_E_FORMATETC; }
    HRESULT STDMETHODCALLTYPE GetCanonicalFormatEtc(FORMATETC*, FORMATETC* out) override { if (out) out->ptd = nullptr; return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetData(FORMATETC*, STGMEDIUM*, BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumFormatEtc(DWORD, IEnumFORMATETC**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE DAdvise(FORMATETC*, DWORD, IAdviseSink*, DWORD*) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE DUnadvise(DWORD) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumDAdvise(IEnumSTATDATA**) override { return OLE_E_ADVISENOTSUPPORTED; }
};

struct Library {
    HMODULE module;
    IClassFactory* factory = nullptr;
    CanUnload unload;
    explicit Library(const std::wstring& path) : module(LoadLibraryW(path.c_str())), unload(nullptr) {
        Check(module != nullptr, "LoadLibrary production DLL");
        auto get = reinterpret_cast<GetFactory>(GetProcAddress(module, "DllGetClassObject"));
        unload = reinterpret_cast<CanUnload>(GetProcAddress(module, "DllCanUnloadNow"));
        Check(get && unload, "COM exports present");
        for (UINT dpi : {96U, 120U, 144U, 192U, 240U, 288U, 384U}) {
            HBITMAP bitmap = CreateMenuIconBitmap(module, dpi);
            Check(bitmap != nullptr, "Build menu icon at supported DPI");
            CheckMenuIcon(bitmap, dpi);
            DeleteObject(bitmap);
        }
        Check(unload() == S_OK, "DLL initially unloadable");
        CheckHr(get(kClassId, IID_IClassFactory, reinterpret_cast<void**>(&factory)), "COM factory construction");
        Check(unload() == S_FALSE, "Factory holds DLL lifetime");
    }
    ~Library() { if (factory) factory->Release(); if (module) FreeLibrary(module); }
};

struct Selection {
    IShellExtInit* init = nullptr;
    IContextMenu* menu = nullptr;
    HMENU popup = CreatePopupMenu();
    HRESULT initialization;
    Selection(Library& library, const std::vector<std::wstring>& files) {
        CheckHr(library.factory->CreateInstance(nullptr, IID_IShellExtInit, reinterpret_cast<void**>(&init)), "Native menu construction");
        CheckHr(init->QueryInterface(IID_IContextMenu, reinterpret_cast<void**>(&menu)), "Native IContextMenu interface");
        auto* data = new DropData(files);
        initialization = init->Initialize(nullptr, data, nullptr);
        data->Release();
    }
    ~Selection() { if (menu) menu->Release(); if (init) init->Release(); if (popup) DestroyMenu(popup); }
    void Expect(const wchar_t* label, UINT flags = CMF_NORMAL, UINT first = 17, UINT last = 100) {
        const HRESULT result = menu->QueryContextMenu(popup, 0, first, last, flags);
        CheckHr(result, "QueryContextMenu");
        const int expected = label ? 1 : 0;
        Check(GetMenuItemCount(popup) == expected && HRESULT_CODE(result) == expected, "Exact menu item count and consumed command IDs");
        if (label) {
            wchar_t actual[100]{};
            GetMenuStringW(popup, 0, actual, 100, MF_BYPOSITION);
            Check(wcscmp(actual, label) == 0, "Correct Korean action label");
            Check(GetMenuItemID(popup, 0) == first, "Menu command ID respects shell range");
            MENUITEMINFOW details{};
            details.cbSize = sizeof(details);
            details.fMask = MIIM_BITMAP;
            Check(GetMenuItemInfoW(popup, 0, TRUE, &details) && details.hbmpItem,
                  "ADF menu command carries the shared application icon");
            CheckMenuIcon(details.hbmpItem, MenuWindowDpi());
        }
    }
    HRESULT Invoke(const wchar_t* verb = nullptr) {
        CMINVOKECOMMANDINFOEX command{};
        command.cbSize = sizeof(command);
        command.nShow = SW_SHOWNORMAL;
        if (verb) { command.fMask = CMIC_MASK_UNICODE; command.lpVerbW = verb; }
        else command.lpVerb = MAKEINTRESOURCEA(0);
        return menu->InvokeCommand(reinterpret_cast<CMINVOKECOMMANDINFO*>(&command));
    }
};

struct PrivateClasses {
    std::wstring path;
    HKEY classes = nullptr;
    HKEY association = nullptr;
    bool overridden = false;
    explicit PrivateClasses(const std::wstring& dll) {
        GUID id{};
        CheckHr(CoCreateGuid(&id), "Private registry fixture GUID");
        wchar_t text[40]{};
        StringFromGUID2(id, text, 40);
        path = L"Software\\ADF\\NativeShellTests\\" + std::wstring(text);
        Check(RegCreateKeyExW(HKEY_CURRENT_USER, path.c_str(), 0, nullptr, REG_OPTION_VOLATILE, KEY_ALL_ACCESS, nullptr, &classes, nullptr) == ERROR_SUCCESS,
              "Create isolated volatile classes registry");
        try {
            Set(L"CLSID\\{8093F936-820B-4CDB-A64B-7A39EC807A11}\\InprocServer32", nullptr, dll);
            Set(L"CLSID\\{8093F936-820B-4CDB-A64B-7A39EC807A11}\\InprocServer32", L"ThreadingModel", L"Apartment");
            Set(L".pdf", nullptr, L"Other.Pdf.Reader");
            Set(L"Other.Pdf.Reader", nullptr, L"Existing default reader fixture");
            Set(L"SystemFileAssociations\\.pdf\\shellex\\ContextMenuHandlers\\ADF", nullptr, L"{8093F936-820B-4CDB-A64B-7A39EC807A11}");
            Check(RegOpenKeyExW(classes, L"SystemFileAssociations\\.pdf", 0, KEY_READ, &association) == ERROR_SUCCESS, "Open isolated SFA handler association");
            Check(RegOverridePredefKey(HKEY_CLASSES_ROOT, classes) == ERROR_SUCCESS, "Override HKCR only within harness process");
            overridden = true;
        } catch (...) { Cleanup(); throw; }
    }
    void Set(const wchar_t* key, const wchar_t* name, const std::wstring& value) {
        HKEY target = nullptr;
        Check(RegCreateKeyExW(classes, key, 0, nullptr, REG_OPTION_VOLATILE, KEY_SET_VALUE, nullptr, &target, nullptr) == ERROR_SUCCESS, "Create private registration key");
        const LONG status = RegSetValueExW(target, name, 0, REG_SZ, reinterpret_cast<const BYTE*>(value.c_str()), static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
        RegCloseKey(target);
        Check(status == ERROR_SUCCESS, "Write private registration value");
    }
    void Cleanup() {
        if (overridden) { RegOverridePredefKey(HKEY_CLASSES_ROOT, nullptr); overridden = false; }
        if (association) { RegCloseKey(association); association = nullptr; }
        if (classes) { RegCloseKey(classes); classes = nullptr; }
        if (!path.empty()) { RegDeleteTreeW(HKEY_CURRENT_USER, path.c_str()); path.clear(); }
    }
    ~PrivateClasses() { Cleanup(); }
};

void CheckCaptured(const std::wstring& directory, const std::vector<std::wstring>& paths, const char* operation);

void ShellAssembledMenu(const std::wstring& dll, const std::wstring& directory, const std::vector<std::wstring>& names, const wchar_t* expected) {
    PIDLIST_ABSOLUTE folderId = nullptr;
    CheckHr(SHParseDisplayName(directory.c_str(), nullptr, &folderId, 0, nullptr), "Parse native shell folder");
    IShellFolder* desktop = nullptr;
    IShellFolder* folder = nullptr;
    CheckHr(SHGetDesktopFolder(&desktop), "Get native desktop folder");
    HRESULT result = desktop->BindToObject(folderId, nullptr, IID_IShellFolder, reinterpret_cast<void**>(&folder));
    desktop->Release();
    CheckHr(result, "Bind native filesystem shell folder");
    std::vector<PIDLIST_RELATIVE> children;
    std::vector<PCUITEMID_CHILD> pointers;
    for (const auto& name : names) {
        PIDLIST_RELATIVE child = nullptr;
        CheckHr(folder->ParseDisplayName(nullptr, nullptr, const_cast<LPWSTR>(name.c_str()), nullptr, &child, nullptr), "Parse actual shell selection PIDL");
        children.push_back(child);
        pointers.push_back(child);
    }
    {
        PrivateClasses registry(dll);
        Library library(dll);
        DWORD cookie = 0;
        CheckHr(CoRegisterClassObject(kClassId, library.factory, CLSCTX_INPROC_SERVER, REGCLS_MULTIPLEUSE, &cookie), "Register native class factory only within harness process");
        IContextMenu* activated = nullptr;
        CheckHr(CoCreateInstance(kClassId, nullptr, CLSCTX_INPROC_SERVER, IID_IContextMenu, reinterpret_cast<void**>(&activated)), "COM activates process-local native class factory");
        activated->Release();
        DEFCONTEXTMENU definition{};
        definition.pidlFolder = folderId;
        definition.psf = folder;
        definition.cidl = static_cast<UINT>(pointers.size());
        definition.apidl = pointers.data();
        definition.cKeys = 1;
        definition.aKeys = &registry.association;
        IContextMenu* menu = nullptr;
        CheckHr(SHCreateDefaultContextMenu(&definition, IID_IContextMenu, reinterpret_cast<void**>(&menu)), "Create Windows shell assembled context menu");
        HMENU popup = CreatePopupMenu();
        CheckHr(menu->QueryContextMenu(popup, 0, 1, 0x7FFF, CMF_NORMAL), "Windows shell populates native menu from SFA handler");
        int found = 0;
        UINT commandId = 0;
        for (int index = 0; index < GetMenuItemCount(popup); ++index) {
            wchar_t label[256]{};
            GetMenuStringW(popup, index, label, 256, MF_BYPOSITION);
            if (wcsstr(label, L"ADF")) {
                ++found;
                Check(expected && wcscmp(label, expected) == 0, "Shell-assembled ADF label follows full actual selection");
                commandId = GetMenuItemID(popup, index);
            }
        }
        Check(found == (expected ? 1 : 0), "Exactly one expected ADF command in real shell assembled menu");
        if (expected) {
            CMINVOKECOMMANDINFO command{};
            command.cbSize = sizeof(command);
            command.lpVerb = MAKEINTRESOURCEA(commandId - 1);
            command.nShow = SW_SHOWNORMAL;
            CheckHr(menu->InvokeCommand(&command), "Invoke through Windows assembled menu routing");
            std::vector<std::wstring> selected;
            for (const auto& name : names) selected.push_back(directory + L"\\" + name);
            CheckCaptured(directory, selected, names.size() == 1 ? "split" : "merge");
        }
        DestroyMenu(popup);
        menu->Release();
        CheckHr(CoRevokeClassObject(cookie), "Remove harness-only native class factory");
        // COM may cache the in-process DLL. Release it before deleting the fixture.
        CoFreeUnusedLibrariesEx(0, 0);
    }
    for (auto child : children) CoTaskMemFree(child);
    folder->Release();
    CoTaskMemFree(folderId);
}

std::string Utf8(const std::wstring& value) {
    int size = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    std::string bytes(size, 0);
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), bytes.data(), size, nullptr, nullptr);
    return bytes;
}
std::string EscapedPath(const std::wstring& path) {
    std::string escaped = "\"";
    for (char c : Utf8(path)) { if (c == '\\' || c == '"') escaped += '\\'; escaped += c; }
    return escaped + '"';
}
void MakeFile(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    Check(file != INVALID_HANDLE_VALUE, "Create test PDF path");
    const char pdf[] = "%PDF-1.4\n%Native shell fixture\n";
    DWORD written;
    WriteFile(file, pdf, sizeof(pdf) - 1, &written, nullptr);
    CloseHandle(file);
}
std::vector<std::wstring> Captures(const std::wstring& directory) {
    std::vector<std::wstring> results;
    WIN32_FIND_DATAW data{};
    HANDLE search = FindFirstFileW((directory + L"\\capture-*.json").c_str(), &data);
    if (search == INVALID_HANDLE_VALUE) return results;
    do { results.push_back(directory + L"\\" + data.cFileName); } while (FindNextFileW(search, &data));
    FindClose(search);
    return results;
}
void CheckCaptured(const std::wstring& directory, const std::vector<std::wstring>& paths, const char* operation) {
    std::vector<std::wstring> captures;
    for (int elapsed = 0; elapsed < 5000; elapsed += 25) { captures = Captures(directory); if (!captures.empty()) break; Sleep(25); }
    // A second process launch would create a second pid-specific output.
    Sleep(100);
    captures = Captures(directory);
    if (captures.size() != 1) {
        WIN32_FIND_DATAW data{};
        HANDLE search = FindFirstFileW((directory + L"\\sink-error-*.txt").c_str(), &data);
        if (search != INVALID_HANDLE_VALUE) {
            do {
                const auto path = directory + L"\\" + data.cFileName;
                HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
                if (file != INVALID_HANDLE_VALUE) {
                    char message[512]{};
                    DWORD read = 0;
                    if (ReadFile(file, message, sizeof(message) - 1, &read, nullptr)) std::printf("Fixture diagnostic: %s", message);
                    CloseHandle(file);
                }
            } while (FindNextFileW(search, &data));
            FindClose(search);
        }
    }
    Check(captures.size() == 1, "Invoke launches exactly one process; private user-only ACL and argv validated by sink");
    HANDLE file = CreateFileW(captures[0].c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    Check(file != INVALID_HANDLE_VALUE, "Open captured UTF-8 JSON");
    DWORD size = GetFileSize(file, nullptr), read = 0;
    std::string bytes(size, 0);
    Check(ReadFile(file, bytes.data(), size, &read, nullptr) && read == size, "Read captured JSON");
    CloseHandle(file);
    std::string expected = "{\"version\":1,\"operation\":\"" + std::string(operation) + "\",\"files\":[";
    for (size_t index = 0; index < paths.size(); ++index) { if (index) expected += ','; expected += EscapedPath(paths[index]); }
    expected += "]}";
    Check(bytes == expected, "JSON keeps complete selection, original ordering, Unicode, spaces, apostrophes and punctuation");
    Check(DeleteFileW(captures[0].c_str()) != FALSE, "Remove exact test capture");
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    try {
        CheckHr(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED), "Initialize COM apartment");
        if (argc >= 4 && wcscmp(argv[1], L"--invoke-app") == 0) {
            Library library(argv[2]);
            std::vector<std::wstring> paths;
            for (int index = 3; index < argc; ++index) paths.emplace_back(argv[index]);
            Selection selection(library, paths);
            CheckHr(selection.initialization, "Initialize actual app handoff selection");
            selection.Expect(paths.size() == 1 ? L"ADF로 PDF 분할…" : L"ADF로 PDF 병합…");
            CheckHr(selection.Invoke(), "Invoke actual app via production DLL");
            std::printf("PASS actual app handoff (%zu PDFs)\n", paths.size());
            return 0;
        }
        const bool componentOnly = argc == 4 && wcscmp(argv[3], L"--component-only") == 0;
        Check(argc == 3 || componentOnly, "Usage: shell-tests.exe <ADFShell.dll> <request-sink.exe> [--component-only]");
        std::wstring dll = argv[1];
        const auto parent = dll.substr(0, dll.find_last_of(L"\\/"));
        const std::wstring fixture = parent + L"\\fixture 한글 ' & (메뉴) " + std::to_wstring(GetCurrentProcessId());
        Check(CreateDirectoryW(fixture.c_str(), nullptr) != FALSE, "Create unique local test fixture");
        const std::wstring testDll = fixture + L"\\ADFShell-0.3.24.dll";
        const std::wstring sink = fixture + L"\\ADF.exe";
        Check(CopyFileW(argv[1], testDll.c_str(), TRUE) != FALSE, "Copy production DLL into fixture");
        Check(CopyFileW(argv[2], sink.c_str(), TRUE) != FALSE, "Copy test-only executable sink");
        const std::wstring one = fixture + L"\\첫 파일 ' $ (금액) & 계약.pdf";
        const std::wstring two = fixture + L"\\두 번째 文件.PDF";
        const std::wstring text = fixture + L"\\메모.txt";
        const std::wstring folder = fixture + L"\\폴더.pdf";
        MakeFile(one); MakeFile(two); MakeFile(text);
        Check(CreateDirectoryW(folder.c_str(), nullptr) != FALSE, "Create directory named .pdf");
        if (componentOnly) {
            std::printf("SCOPE: component tests only; Windows-assembled Explorer menus are not checked in this invocation.\n");
        } else {
            ShellAssembledMenu(testDll, fixture, {one.substr(fixture.size() + 1)}, L"ADF로 PDF 분할…");
            ShellAssembledMenu(testDll, fixture, {two.substr(fixture.size() + 1), one.substr(fixture.size() + 1)}, L"ADF로 PDF 병합…");
            ShellAssembledMenu(testDll, fixture, {one.substr(fixture.size() + 1), text.substr(fixture.size() + 1)}, nullptr);
        }
        {
            Library library(testDll);
            {
                Selection selection(library, {one});
                CheckHr(selection.initialization, "Single PDF accepted");
                selection.Expect(L"ADF로 PDF 분할…");
                wchar_t verb[64]{};
                CheckHr(selection.menu->GetCommandString(0, GCS_VERBW, nullptr, reinterpret_cast<LPSTR>(verb), 64), "Unicode canonical verb");
                Check(wcscmp(verb, L"ADF.Split") == 0, "Split canonical verb");
                Check(FAILED(selection.Invoke(L"ADF.Merge")), "Merge verb rejected for single PDF");
                CheckHr(selection.Invoke(), "Invoke single PDF split by numeric command");
                CheckCaptured(fixture, {one}, "split");
            }
            {
                Selection selection(library, {two, one});
                CheckHr(selection.initialization, "Multiple PDFs accepted");
                selection.Expect(L"ADF로 PDF 병합…");
                Check(FAILED(selection.Invoke(L"ADF.Split")), "Split verb rejected for multiple PDFs");
                CheckHr(selection.Invoke(L"ADF.Merge"), "Invoke merge by Unicode canonical verb");
                CheckCaptured(fixture, {two, one}, "merge");
            }
            for (const auto& invalid : std::vector<std::vector<std::wstring>>{{}, {text}, {folder}, {one, text}, {one, folder}, {fixture + L"\\missing.pdf"}}) {
                Selection selection(library, invalid);
                Check(FAILED(selection.initialization), "Non-PDF, mixed, directory, missing or empty selection rejected");
                selection.Expect(nullptr);
                Check(FAILED(selection.Invoke()), "Rejected selection cannot invoke");
            }
            for (int corruption = 0; corruption < 5; ++corruption) {
                std::vector<BYTE> raw(corruption == 0 ? 4 : 128, 0x41);
                if (corruption != 0) {
                    auto* drop = reinterpret_cast<DROPFILES*>(raw.data());
                    *drop = {};
                    drop->fWide = TRUE;
                    drop->pFiles = corruption == 1 ? 0xFFFFFF00 : corruption == 2 ? sizeof(DROPFILES) + 1 : corruption == 3 ? 0 : sizeof(DROPFILES);
                }
                auto* malformed = new DropData(raw, 0);
                Selection selection(library, {});
                Check(FAILED(selection.init->Initialize(nullptr, malformed, nullptr)), "Malformed DROPFILES header, offset, alignment or missing terminator rejected safely");
                malformed->Release();
                selection.Expect(nullptr);
            }
            { Selection selection(library, {one}); selection.Expect(nullptr, CMF_DEFAULTONLY); Check(FAILED(selection.Invoke()), "Default PDF open never invokes split"); }
            { Selection selection(library, {one}); selection.Expect(nullptr, CMF_NORMAL, 100, 99); }
            { Selection selection(library, {one, two}); selection.Expect(L"ADF로 PDF 병합…", CMF_NODEFAULT); }
            std::vector<std::wstring> many;
            for (int index = 0; index < 512; ++index) {
                const auto file = fixture + L"\\긴 선택 문서 " + std::to_wstring(index) + L".pdf";
                MakeFile(file); many.push_back(file);
            }
            {
                Selection selection(library, many);
                CheckHr(selection.initialization, "512-file selection accepted");
                selection.Expect(L"ADF로 PDF 병합…");
                CheckHr(selection.Invoke(), "Long selection invokes in one process beyond command line limit");
                CheckCaptured(fixture, many, "merge");
            }
            { Selection selection(library, std::vector<std::wstring>(4097, one)); Check(FAILED(selection.initialization), "4096-file bound enforced"); selection.Expect(nullptr); }
            {
                Selection selection(library, {one});
                selection.Expect(L"ADF로 PDF 분할…");
                auto* changed = new DropData({text});
                Check(FAILED(selection.init->Initialize(nullptr, changed, nullptr)), "Reinitialization rejects changed selection");
                changed->Release();
                DeleteMenu(selection.popup, 0, MF_BYPOSITION);
                selection.Expect(nullptr);
            }
            for (const auto& path : many) Check(DeleteFileW(path.c_str()) != FALSE, "Remove exact generated PDF");
            library.factory->Release(); library.factory = nullptr;
            Check(library.unload() == S_OK, "All COM objects released; DLL unloadable");
        }
        for (const auto& path : {one, two, text, testDll, sink}) Check(DeleteFileW(path.c_str()) != FALSE, "Remove exact test fixture file");
        Check(RemoveDirectoryW(folder.c_str()) != FALSE, "Remove empty PDF-named folder");
        Check(RemoveDirectoryW(fixture.c_str()) != FALSE, "Remove empty unique test fixture");
        CoUninitialize();
        std::printf("PASS native %s: %d assertions; real COM selection matrix, owner-only request ACL, Unicode handoff, 512-file single-process invoke.\n", componentOnly ? "component tests (Explorer menu assembly excluded)" : "Explorer integration", checks);
        return 0;
    } catch (const std::exception& error) { std::fprintf(stderr, "FAIL: %s (after %d assertions)\n", error.what(), checks); return 1; }
}
