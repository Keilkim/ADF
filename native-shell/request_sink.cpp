// Test fixture only. Never packaged with the app or used by the production DLL.
#include <windows.h>
#include <shellapi.h>
#include <aclapi.h>
#include <string>
#include <vector>

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
    if (!arguments || count != 3 || wcscmp(arguments[1], L"--shell-request")) return 10;
    const std::wstring request = arguments[2];
    LocalFree(arguments);
    const auto separator = request.find_last_of(L"\\/");
    if (separator == std::wstring::npos || !PrivateAcl(request.c_str()) || !PrivateAcl(request.substr(0, separator).c_str())) return 11;
    wchar_t module[32768]{};
    if (!GetModuleFileNameW(nullptr, module, 32768)) return 12;
    std::wstring output(module);
    output.resize(output.find_last_of(L"\\/"));
    output += L"\\capture-" + std::to_wstring(GetCurrentProcessId()) + L".json";
    if (!CopyFileW(request.c_str(), output.c_str(), TRUE)) return 13;
    if (!DeleteFileW(request.c_str())) return 14;
    return 0;
}
