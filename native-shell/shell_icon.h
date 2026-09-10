#pragma once
#include <windows.h>
#include <algorithm>
#include <cstring>

// Explorer consumes a premultiplied 32-bit DIB, not an icon's device-dependent
// color plane with its transparency mask thrown away.
inline HBITMAP CreateMenuIconBitmap(HINSTANCE module, UINT dpi) {
    const int width = std::clamp(GetSystemMetricsForDpi(SM_CXSMICON, dpi), 16, 64);
    const int height = std::clamp(GetSystemMetricsForDpi(SM_CYSMICON, dpi), 16, 64);
    HICON icon = static_cast<HICON>(LoadImageW(module, MAKEINTRESOURCEW(101),
                                             IMAGE_ICON, width, height, LR_DEFAULTCOLOR));
    if (!icon) return nullptr;
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height;  // Top-down BGRA.
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    void* pixels = nullptr;
    HBITMAP bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
    HDC dc = CreateCompatibleDC(nullptr);
    bool drawn = false;
    if (bitmap && pixels && dc) {
        std::memset(pixels, 0, static_cast<size_t>(width) * height * 4);
        HGDIOBJ previous = SelectObject(dc, bitmap);
        if (previous && previous != HGDI_ERROR) {
            drawn = DrawIconEx(dc, 0, 0, icon, width, height, 0, nullptr, DI_NORMAL | DI_NOMIRROR) != FALSE;
            GdiFlush();
            SelectObject(dc, previous);
        }
    }
    if (dc) DeleteDC(dc);
    DestroyIcon(icon);
    if (!drawn && bitmap) { DeleteObject(bitmap); bitmap = nullptr; }
    return bitmap;
}

inline UINT MenuWindowDpi() {
    const HWND owner = GetForegroundWindow();
    const UINT dpi = owner ? GetDpiForWindow(owner) : 0;
    return dpi ? dpi : GetDpiForSystem();
}
