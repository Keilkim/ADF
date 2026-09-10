// Native Explorer COM handler. No registry writes and no Python/Qt in Explorer.
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <sddl.h>
#include <aclapi.h>
#include <strsafe.h>
#include <algorithm>
#include <atomic>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>
#include "shell_icon.h"

namespace {
const CLSID kClassId = {0x8093f936, 0x820b, 0x4cdb, {0xa6, 0x4b, 0x7a, 0x39, 0xec, 0x80, 0x7a, 0x11}};
constexpr size_t kMaxFiles = 4096;
constexpr size_t kMaxRequestBytes = 8 * 1024 * 1024;
HMODULE g_module = nullptr;
std::atomic<long> g_objects{0};

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE handle = INVALID_HANDLE_VALUE) : value(handle) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};
struct LocalMemory {
    HLOCAL value = nullptr;
    ~LocalMemory() { if (value) LocalFree(value); }
};
HRESULT LastErrorResult() { const DWORD error = GetLastError(); return HRESULT_FROM_WIN32(error ? error : ERROR_GEN_FAILURE); }

std::wstring ModuleDirectory() {
    std::vector<wchar_t> buffer(32768);
    DWORD size = GetModuleFileNameW(g_module, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!size || size >= buffer.size()) return {};
    std::wstring path(buffer.data(), size);
    const auto end = path.find_last_of(L"\\/");
    return end == std::wstring::npos ? std::wstring() : path.substr(0, end);
}

bool IsAbsolutePdfFile(const std::wstring& path) {
    const bool absolute = (path.size() >= 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/')) ||
                          (path.size() >= 3 && path[0] == L'\\' && path[1] == L'\\');
    if (!absolute || path.size() < 4 || _wcsicmp(path.c_str() + path.size() - 4, L".pdf") != 0) return false;
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

HRESULT ReadDropSelection(HGLOBAL storage, std::vector<std::wstring>& files) {
    // IDataObject is supplied by another component. DragQueryFile assumes the
    // DROPFILES offsets and terminators are valid and can otherwise fault in Explorer.
    const SIZE_T byteCount = GlobalSize(storage);
    if (byteCount < sizeof(DROPFILES) || byteCount > 16 * 1024 * 1024) return E_INVALIDARG;
    const auto* memory = static_cast<const BYTE*>(GlobalLock(storage));
    if (!memory) return LastErrorResult();
    HRESULT result = S_OK;
    try {
        const auto* header = reinterpret_cast<const DROPFILES*>(memory);
        const size_t offset = header->pFiles;
        const size_t unit = header->fWide ? sizeof(wchar_t) : sizeof(char);
        if (offset < sizeof(DROPFILES) || offset >= byteCount || (header->fWide && offset % sizeof(wchar_t))) result = E_INVALIDARG;
        else {
            const size_t count = (byteCount - offset) / unit;
            const BYTE* data = memory + offset;
            auto at = [data, header](size_t index) -> unsigned int {
                return header->fWide ? static_cast<unsigned int>(reinterpret_cast<const wchar_t*>(data)[index]) : data[index];
            };
            size_t cursor = 0;
            bool terminated = false;
            while (cursor < count) {
                if (at(cursor) == 0) { terminated = !files.empty(); break; }
                const size_t start = cursor;
                while (cursor < count && at(cursor) != 0 && cursor - start < 32768) ++cursor;
                if (cursor == count || cursor - start >= 32768 || files.size() >= kMaxFiles) { result = E_INVALIDARG; break; }
                std::wstring path;
                if (header->fWide) path.assign(reinterpret_cast<const wchar_t*>(data) + start, cursor - start);
                else {
                    const char* bytes = reinterpret_cast<const char*>(data) + start;
                    const int length = static_cast<int>(cursor - start);
                    const int required = MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, bytes, length, nullptr, 0);
                    if (!required) { result = E_INVALIDARG; break; }
                    path.resize(required);
                    if (!MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, bytes, length, path.data(), required)) { result = E_INVALIDARG; break; }
                }
                if (!IsAbsolutePdfFile(path)) { result = E_INVALIDARG; break; }
                files.push_back(std::move(path));
                ++cursor; // The first NUL terminates this path; another ends the list.
            }
            if (!terminated) result = E_INVALIDARG;
        }
    } catch (const std::bad_alloc&) { result = E_OUTOFMEMORY; }
      catch (...) { result = E_FAIL; }
    GlobalUnlock(storage);
    return result;
}

std::wstring QuoteArgument(const std::wstring& argument) {
    std::wstring result = L"\"";
    size_t backslashes = 0;
    for (wchar_t character : argument) {
        if (character == L'\\') { ++backslashes; continue; }
        if (character == L'\"') result.append(backslashes * 2 + 1, L'\\');
        else result.append(backslashes, L'\\');
        result.push_back(character);
        backslashes = 0;
    }
    result.append(backslashes * 2, L'\\');
    result.push_back(L'\"');
    return result;
}

std::string JsonString(const std::wstring& value) {
    const int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.c_str(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (!length && !value.empty()) throw std::runtime_error("Invalid UTF-16 path");
    std::string utf8(static_cast<size_t>(length), '\0');
    if (length) WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.c_str(), static_cast<int>(value.size()), utf8.data(), length, nullptr, nullptr);
    std::string result = "\"";
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char character : utf8) {
        if (character == '"' || character == '\\') { result.push_back('\\'); result.push_back(static_cast<char>(character)); }
        else if (character < 32) { result += "\\u00"; result.push_back(hex[character >> 4]); result.push_back(hex[character & 15]); }
        else result.push_back(static_cast<char>(character));
    }
    result.push_back('"');
    return result;
}

HRESULT CreatePrivateRequest(const std::vector<std::wstring>& files, std::wstring& requestPath) {
    std::string json = "{\"version\":1,\"operation\":\"";
    json += files.size() == 1 ? "split" : "merge";
    json += "\",\"files\":[";
    for (size_t i = 0; i < files.size(); ++i) {
        if (i) json.push_back(',');
        json += JsonString(files[i]);
        if (json.size() > kMaxRequestBytes - 2) return HRESULT_FROM_WIN32(ERROR_BUFFER_OVERFLOW);
    }
    json += "]}";

    Handle token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value)) return LastErrorResult();
    DWORD tokenSize = 0;
    GetTokenInformation(token.value, TokenUser, nullptr, 0, &tokenSize);
    if (!tokenSize) return LastErrorResult();
    std::vector<BYTE> tokenData(tokenSize);
    if (!GetTokenInformation(token.value, TokenUser, tokenData.data(), tokenSize, &tokenSize)) return LastErrorResult();
    LPWSTR sidText = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(tokenData.data())->User.Sid, &sidText)) return LastErrorResult();
    LocalMemory sidMemory;
    sidMemory.value = sidText;
    const std::wstring sddl = L"D:P(A;;FA;;;" + std::wstring(sidText) + L")";
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr)) return LastErrorResult();
    LocalMemory descriptorMemory;
    descriptorMemory.value = descriptor;
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};

    PWSTR appData = nullptr;
    HRESULT result = SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_CREATE, nullptr, &appData);
    if (FAILED(result)) return result;
    const std::wstring appFolder = std::wstring(appData) + L"\\ADF";
    CoTaskMemFree(appData);
    if (!CreateDirectoryW(appFolder.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) return LastErrorResult();
    DWORD attributes = GetFileAttributesW(appFolder.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return E_ACCESSDENIED;
    const std::wstring requestFolder = appFolder + L"\\ShellRequests";
    if (!CreateDirectoryW(requestFolder.c_str(), &security) && GetLastError() != ERROR_ALREADY_EXISTS) return LastErrorResult();
    attributes = GetFileAttributesW(requestFolder.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_DIRECTORY) || (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return E_ACCESSDENIED;
    // Also restrict a pre-existing request directory; files receive their own protected DACL.
    PACL dacl = nullptr;
    BOOL present = FALSE, defaulted = FALSE;
    if (!GetSecurityDescriptorDacl(descriptor, &present, &dacl, &defaulted) || !present || !dacl) return E_ACCESSDENIED;
    const DWORD aclResult = SetNamedSecurityInfoW(const_cast<LPWSTR>(requestFolder.c_str()), SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION, nullptr, nullptr, dacl, nullptr);
    if (aclResult != ERROR_SUCCESS) return HRESULT_FROM_WIN32(aclResult);

    GUID id{};
    result = CoCreateGuid(&id);
    if (FAILED(result)) return result;
    wchar_t idText[40]{};
    StringFromGUID2(id, idText, 40);
    const std::wstring name(idText + 1, 36);
    requestPath = requestFolder + L"\\request-" + name + L".json";
    Handle file(CreateFileW(requestPath.c_str(), GENERIC_WRITE, 0, &security, CREATE_NEW, FILE_ATTRIBUTE_TEMPORARY, nullptr));
    if (file.value == INVALID_HANDLE_VALUE) { requestPath.clear(); return LastErrorResult(); }
    DWORD written = 0;
    if (!WriteFile(file.value, json.data(), static_cast<DWORD>(json.size()), &written, nullptr) || written != json.size()) {
        result = LastErrorResult();
        CloseHandle(file.value);
        file.value = INVALID_HANDLE_VALUE;
        DeleteFileW(requestPath.c_str());
        requestPath.clear();
        return result;
    }
    return S_OK;
}

HRESULT LaunchRequest(const std::vector<std::wstring>& files, int show) {
    const std::wstring directory = ModuleDirectory();
    if (directory.empty()) return E_FAIL;
    const std::wstring executable = directory + L"\\ADF.exe";
    const DWORD attributes = GetFileAttributesW(executable.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
    std::wstring request;
    HRESULT result = CreatePrivateRequest(files, request);
    if (FAILED(result)) return result;
    std::wstring command = QuoteArgument(executable) + L" --shell-request " + QuoteArgument(request);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = static_cast<WORD>(show == SW_HIDE ? SW_SHOWNORMAL : show);
    PROCESS_INFORMATION process{};
    // An explicit executable path and argv quoting bypass cmd.exe and all shell evaluation.
    if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_UNICODE_ENVIRONMENT,
                        nullptr, directory.c_str(), &startup, &process)) {
        result = LastErrorResult();
        DeleteFileW(request.c_str());
        return result;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return S_OK;
}

class Menu final : public IShellExtInit, public IContextMenu {
    std::atomic<ULONG> references_{1};
    std::vector<std::wstring> files_;
    bool offered_ = false;
    HBITMAP bitmap_ = nullptr;
    const wchar_t* VerbW() const { return files_.size() == 1 ? L"ADF.Split" : L"ADF.Merge"; }
    const char* VerbA() const { return files_.size() == 1 ? "ADF.Split" : "ADF.Merge"; }
public:
    Menu() {
        ++g_objects;
        bitmap_ = CreateMenuIconBitmap(g_module, MenuWindowDpi());
    }
    ~Menu() {
        if (bitmap_) DeleteObject(bitmap_);
        --g_objects;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (IsEqualIID(iid, IID_IUnknown) || IsEqualIID(iid, IID_IShellExtInit)) *value = static_cast<IShellExtInit*>(this);
        else if (IsEqualIID(iid, IID_IContextMenu)) *value = static_cast<IContextMenu*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override { const ULONG remaining = --references_; if (!remaining) delete this; return remaining; }
    HRESULT STDMETHODCALLTYPE Initialize(PCIDLIST_ABSOLUTE, IDataObject* data, HKEY) override {
        files_.clear();
        offered_ = false;
        if (!data) return E_INVALIDARG;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        STGMEDIUM medium{};
        HRESULT result = data->GetData(&format, &medium);
        if (FAILED(result)) return result;
        try {
            if (medium.tymed != TYMED_HGLOBAL || !medium.hGlobal) result = DV_E_TYMED;
            else result = ReadDropSelection(medium.hGlobal, files_);
        } catch (const std::bad_alloc&) { result = E_OUTOFMEMORY; }
          catch (...) { result = E_FAIL; }
        ReleaseStgMedium(&medium);
        if (FAILED(result)) files_.clear();
        return result;
    }
    HRESULT STDMETHODCALLTYPE QueryContextMenu(HMENU menu, UINT index, UINT first, UINT last, UINT flags) override {
        offered_ = false;
        if (files_.empty() || (flags & CMF_DEFAULTONLY) || first > last) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        MENUITEMINFOW item{};
        item.cbSize = sizeof(item);
        item.fMask = MIIM_ID | MIIM_STRING;
        if (bitmap_) {
            item.fMask |= MIIM_BITMAP;
            item.hbmpItem = bitmap_;
        }
        item.wID = first;
        item.dwTypeData = const_cast<LPWSTR>(files_.size() == 1 ? L"ADF로 PDF 분할…" : L"ADF로 PDF 병합…");
        if (!InsertMenuItemW(menu, index, TRUE, &item)) return LastErrorResult();
        offered_ = true;
        return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 1);
    }
    HRESULT STDMETHODCALLTYPE GetCommandString(UINT_PTR command, UINT flags, UINT*, LPSTR name, UINT length) override {
        if (command || !offered_ || files_.empty()) return E_INVALIDARG;
        if (flags == GCS_VALIDATEA || flags == GCS_VALIDATEW) return S_OK;
        if (!name || !length) return E_POINTER;
        if (flags == GCS_VERBW) return StringCchCopyW(reinterpret_cast<LPWSTR>(name), length, VerbW());
        if (flags == GCS_VERBA) return StringCchCopyA(name, length, VerbA());
        if (flags == GCS_HELPTEXTW) return StringCchCopyW(reinterpret_cast<LPWSTR>(name), length, files_.size() == 1 ? L"선택한 PDF를 별도 창에서 분할합니다." : L"선택한 PDF를 별도 창에서 병합합니다.");
        if (flags == GCS_HELPTEXTA) return StringCchCopyA(name, length, files_.size() == 1 ? "Split the selected PDF in ADF." : "Merge the selected PDFs in ADF.");
        return E_INVALIDARG;
    }
    HRESULT STDMETHODCALLTYPE InvokeCommand(CMINVOKECOMMANDINFO* info) override {
        if (!info || info->cbSize < sizeof(CMINVOKECOMMANDINFO) || files_.empty()) return E_INVALIDARG;
        bool valid = false;
        const bool unicode = info->cbSize >= sizeof(CMINVOKECOMMANDINFOEX) && (info->fMask & CMIC_MASK_UNICODE);
        const auto extended = reinterpret_cast<CMINVOKECOMMANDINFOEX*>(info);
        if (unicode && HIWORD(extended->lpVerbW)) valid = _wcsicmp(extended->lpVerbW, VerbW()) == 0;
        else if (HIWORD(info->lpVerb)) valid = _stricmp(info->lpVerb, VerbA()) == 0;
        else valid = offered_ && LOWORD(info->lpVerb) == 0;
        if (!valid) return E_INVALIDARG;
        try {
            // Revalidate files in case the selection changed on disk while the menu was open.
            for (const auto& path : files_) if (!IsAbsolutePdfFile(path)) return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
            return LaunchRequest(files_, info->nShow);
        } catch (const std::bad_alloc&) { return E_OUTOFMEMORY; }
          catch (...) { return E_FAIL; }
    }
};

class Factory final : public IClassFactory {
    std::atomic<ULONG> references_{1};
public:
    Factory() { ++g_objects; }
    ~Factory() { --g_objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (!IsEqualIID(iid, IID_IUnknown) && !IsEqualIID(iid, IID_IClassFactory)) return E_NOINTERFACE;
        *value = static_cast<IClassFactory*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override { ULONG remaining = --references_; if (!remaining) delete this; return remaining; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* menu = new (std::nothrow) Menu();
        if (!menu) return E_OUTOFMEMORY;
        HRESULT result = menu->QueryInterface(iid, value);
        menu->Release();
        return result;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override { if (lock) ++g_objects; else --g_objects; return S_OK; }
};
} // namespace

extern "C" BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) g_module = module;
    return TRUE;
}
extern "C" HRESULT __stdcall DllCanUnloadNow() { return g_objects == 0 ? S_OK : S_FALSE; }
extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID iid, void** value) {
    if (!value) return E_POINTER;
    *value = nullptr;
    if (!IsEqualCLSID(clsid, kClassId)) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) Factory();
    if (!factory) return E_OUTOFMEMORY;
    const HRESULT result = factory->QueryInterface(iid, value);
    factory->Release();
    return result;
}
