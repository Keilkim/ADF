// State shared by the COM classes in ADFShell.dll.
#pragma once
#include <windows.h>
#include <atomic>

namespace adf {
extern HMODULE g_module;
// Live objects, including completion handlers that WinRT operations still hold,
// and server locks; DllCanUnloadNow keeps the DLL while any remain.
extern std::atomic<long> g_objects;

// {A96AE73F-5DB5-4CF1-80EF-9A44D2B3D84D}: the PDF thumbnail handler.
constexpr CLSID kThumbnailClassId = {0xa96ae73f, 0x5db5, 0x4cf1, {0x80, 0xef, 0x9a, 0x44, 0xd2, 0xb3, 0xd8, 0x4d}};
HRESULT CreateThumbnailProvider(REFIID iid, void** value);
}
