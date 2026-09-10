// Test fixture only. Never packaged with the app or used by the production DLL.
#include <windows.h>
#include <shellapi.h>
#include <aclapi.h>
#include <cstdio>
#include <string>
#include <vector>

static int Fail(int code, const char* message) {
    wchar_t module[32768]{};
    if (GetModuleFileNameW(nullptr, module, 32768)) {
        std::wstring path(module);
        path.resize(path.find_last_of(L"\\/"));
        path += L"\\sink-error-" + std::to_wstring(GetCurrentProcessId()) + L".txt";
        HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            const std::string text = std::to_string(code) + ": " + message + "\n";
            DWORD written = 0;
            WriteFile(file, text.data(), static_cast<DWORD>(text.size()), &written, nullptr);
            CloseHandle(file);
        }
    }
    return code;
}

static bool PrivateAcl(const wchar_t* path) {
    PACL dacl = nullptr;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    PSID owner = nullptr;
    if (GetNamedSecurityInfoW(const_cast<LPWSTR>(path), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                             &owner, nullptr, &dacl, nullptr, &descriptor) != ERROR_SUCCESS) return false;
    HANDLE token = nullptr;
    bool valid = false;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        DWORD size = 0;
        GetTokenInformation(token, TokenUser, nullptr, 0, &size);
        std::vector<BYTE> bytes(size);
        if (size && GetTokenInformation(token, TokenUser, bytes.data(), size, &size)) {
            PSID user = reinterpret_cast<TOKEN_USER*>(bytes.data())->User.Sid;
            SECURITY_DESCRIPTOR_CONTROL control = 0;
            DWORD revision = 0;
            void* ace = nullptr;
            valid = owner && EqualSid(owner, user) && dacl && dacl->AceCount == 1 &&
                    GetSecurityDescriptorControl(descriptor, &control, &revision) && (control & SE_DACL_PROTECTED) &&
                    GetAce(dacl, 0, &ace) && static_cast<ACE_HEADER*>(ace)->AceType == ACCESS_ALLOWED_ACE_TYPE &&
                    EqualSid(&static_cast<ACCESS_ALLOWED_ACE*>(ace)->SidStart, user);
        }
        CloseHandle(token);
    }
    LocalFree(descriptor);
    return valid;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int) {
    int count = 0;
    wchar_t** arguments = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!arguments || count != 3 || wcscmp(arguments[1], L"--shell-request")) return Fail(10, "Unexpected request arguments");
    const std::wstring request = arguments[2];
    LocalFree(arguments);
    const auto separator = request.find_last_of(L"\\/");
    if (separator == std::wstring::npos) return Fail(11, "Request path has no parent");
    if (!PrivateAcl(request.c_str())) return Fail(11, "Request file owner or private ACL mismatch");
    if (!PrivateAcl(request.substr(0, separator).c_str())) return Fail(11, "Request directory owner or private ACL mismatch");
    wchar_t module[32768]{};
    if (!GetModuleFileNameW(nullptr, module, 32768)) return Fail(12, "Cannot locate fixture executable");
    std::wstring output(module);
    output.resize(output.find_last_of(L"\\/"));
    output += L"\\capture-" + std::to_wstring(GetCurrentProcessId()) + L".json";
    if (!CopyFileW(request.c_str(), output.c_str(), TRUE)) return Fail(13, "Cannot copy request capture");
    if (!DeleteFileW(request.c_str())) return Fail(14, "Cannot remove captured request");
    return 0;
}
