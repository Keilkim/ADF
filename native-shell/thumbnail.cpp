// Explorer thumbnail of a PDF's first page, drawn by Windows' own PDF renderer.
// Explorer runs this handler in its isolated thumbnail process. The handler
// parses no PDF itself: Windows.Data.Pdf renders the page and WIC decodes it.
#include <windows.h>
#include <objbase.h>
#include <propsys.h>
#include <roapi.h>
#include <shcore.h>
#include <thumbcache.h>
#include <wincodec.h>
#include <winstring.h>
#include <wrl/client.h>
// MinGW's boolean is BYTE, so windows.foundation.h specializes
// IReference<BYTE> twice. This handler uses neither.
#define ____FIReference_1_boolean_INTERFACE_DEFINED__
#include <windows.storage.streams.h>
#include "windows.data.pdf.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <new>
#include <vector>
#include "shell_module.h"

using Microsoft::WRL::ComPtr;
namespace Foundation = ABI::Windows::Foundation;
namespace Pdf = ABI::Windows::Data::Pdf;
namespace Streams = ABI::Windows::Storage::Streams;

namespace adf {
namespace {
// Explorer requests at most 2560 pixels. A larger PDF is shown as an icon.
constexpr UINT kMaxSize = 2560;
constexpr ULONGLONG kMaxPdfBytes = 1ull << 30;
constexpr DWORD kTimeoutMs = 20000;

template <size_t N>
HRESULT Activate(const wchar_t (&name)[N], REFIID iid, void** value) {
    HSTRING_HEADER header;
    HSTRING string = nullptr;
    HRESULT result = WindowsCreateStringReference(name, N - 1, &header, &string);
    if (FAILED(result)) return result;
    ComPtr<IInspectable> instance;
    result = RoActivateInstance(string, &instance);
    return FAILED(result) ? result : instance->QueryInterface(iid, value);
}

template <size_t N>
HRESULT Factory(const wchar_t (&name)[N], REFIID iid, void** value) {
    HSTRING_HEADER header;
    HSTRING string = nullptr;
    HRESULT result = WindowsCreateStringReference(name, N - 1, &header, &string);
    return FAILED(result) ? result : RoGetActivationFactory(string, iid, value);
}

// Signals an event when an asynchronous operation ends. The handler is agile,
// so the operation calls it on its own thread instead of this blocked one.
template <typename Handler, typename Operation>
class Completion final : public Handler, public IAgileObject {
    std::atomic<ULONG> references_{1};
public:
    const HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    ~Completion() { if (event) CloseHandle(event); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (IsEqualIID(iid, IID_IUnknown) || IsEqualIID(iid, __uuidof(Handler))) *value = static_cast<Handler*>(this);
        else if (IsEqualIID(iid, __uuidof(IAgileObject))) *value = static_cast<IAgileObject*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override { const ULONG remaining = --references_; if (!remaining) delete this; return remaining; }
    HRESULT STDMETHODCALLTYPE Invoke(Operation*, AsyncStatus) override { SetEvent(event); return S_OK; }
};

// Waits for an operation and returns its error, if any. A timed-out
// operation is cancelled; the handler keeps its event alive until it runs.
template <typename Handler, typename Operation>
HRESULT Await(Operation* operation) {
    ComPtr<Completion<Handler, Operation>> completion;
    completion.Attach(new (std::nothrow) Completion<Handler, Operation>());
    if (!completion || !completion->event) return E_OUTOFMEMORY;
    HRESULT result = operation->put_Completed(completion.Get());
    if (FAILED(result)) return result;
    HANDLE event = completion->event;
    DWORD index = 0;
    result = CoWaitForMultipleHandles(COWAIT_DISPATCH_CALLS, kTimeoutMs, 1, &event, &index);
    if (result == CO_E_NOTINITIALIZED)
        result = WaitForSingleObject(event, kTimeoutMs) == WAIT_OBJECT_0 ? S_OK : RPC_S_CALLPENDING;
    ComPtr<IAsyncInfo> info;
    const HRESULT query = operation->QueryInterface(IID_PPV_ARGS(&info));
    if (FAILED(query)) return query;
    if (result == RPC_S_CALLPENDING) {
        info->Cancel();
        return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    }
    if (FAILED(result)) return result;
    AsyncStatus status = Started;
    result = info->get_Status(&status);
    if (FAILED(result) || status == Completed) return result;
    HRESULT error = E_FAIL;
    info->get_ErrorCode(&error);
    return FAILED(error) ? error : E_ABORT;
}

// Copies the file into memory, so that the renderer's threads never call the
// stream that Explorer handed to this thread.
HRESULT ReadFile(IStream* source, IStream** copy) {
    STATSTG stat{};
    HRESULT result = source->Stat(&stat, STATFLAG_NONAME);
    if (FAILED(result)) return result;
    if (!stat.cbSize.QuadPart || stat.cbSize.QuadPart > kMaxPdfBytes) return HRESULT_FROM_WIN32(ERROR_FILE_TOO_LARGE);
    const ULONG size = static_cast<ULONG>(stat.cbSize.QuadPart);
    LARGE_INTEGER start{};
    result = source->Seek(start, STREAM_SEEK_SET, nullptr);
    if (FAILED(result)) return result;
    HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, size);
    if (!memory) return E_OUTOFMEMORY;
    auto* bytes = static_cast<BYTE*>(GlobalLock(memory));
    ULONG total = 0;
    while (bytes && total < size) {
        ULONG read = 0;
        result = source->Read(bytes + total, std::min<ULONG>(size - total, 1 << 20), &read);
        if (FAILED(result) || !read) break;
        total += read;
    }
    if (bytes) GlobalUnlock(memory);
    else result = E_OUTOFMEMORY;
    if (SUCCEEDED(result) && total != size) result = HRESULT_FROM_WIN32(ERROR_HANDLE_EOF);
    if (SUCCEEDED(result)) result = CreateStreamOnHGlobal(memory, TRUE, copy);
    if (FAILED(result)) { GlobalFree(memory); return result; }
    // The allocation can be larger than the file; the stream must not be.
    ULARGE_INTEGER length{};
    length.QuadPart = size;
    return (*copy)->SetSize(length);
}

HRESULT Decode(IStream* image, UINT size, HBITMAP* bitmap) {
    ComPtr<IWICImagingFactory> factory;
    HRESULT result = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory));
    if (FAILED(result)) return result;
    ComPtr<IWICBitmapDecoder> decoder;
    result = factory->CreateDecoderFromStream(image, nullptr, WICDecodeMetadataCacheOnDemand, &decoder);
    if (FAILED(result)) return result;
    ComPtr<IWICBitmapFrameDecode> frame;
    result = decoder->GetFrame(0, &frame);
    if (FAILED(result)) return result;
    UINT width = 0, height = 0;
    result = frame->GetSize(&width, &height);
    if (FAILED(result)) return result;
    if (!width || !height || width > size + 1 || height > size + 1) return E_UNEXPECTED;
    ComPtr<IWICFormatConverter> converter;
    result = factory->CreateFormatConverter(&converter);
    if (FAILED(result)) return result;
    result = converter->Initialize(frame.Get(), GUID_WICPixelFormat32bppBGR, WICBitmapDitherTypeNone, nullptr, 0, WICBitmapPaletteTypeCustom);
    if (FAILED(result)) return result;
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(info.bmiHeader);
    info.bmiHeader.biWidth = static_cast<LONG>(width);
    info.bmiHeader.biHeight = -static_cast<LONG>(height);  // top-down rows
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    void* bits = nullptr;
    HBITMAP created = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!created) return E_OUTOFMEMORY;
    result = converter->CopyPixels(nullptr, width * 4, width * 4 * height, static_cast<BYTE*>(bits));
    if (FAILED(result)) { DeleteObject(created); return result; }
    *bitmap = created;
    return S_OK;
}

HRESULT RenderFirstPage(IStream* file, UINT size, HBITMAP* bitmap) {
    ComPtr<IStream> copy;
    HRESULT result = ReadFile(file, &copy);
    if (FAILED(result)) return result;
    ComPtr<Streams::IRandomAccessStream> input;
    result = CreateRandomAccessStreamOverStream(copy.Get(), BSOS_DEFAULT, IID_PPV_ARGS(&input));
    if (FAILED(result)) return result;
    ComPtr<Pdf::IPdfDocumentStatics> documents;
    result = Factory(L"Windows.Data.Pdf.PdfDocument", IID_PPV_ARGS(&documents));
    if (FAILED(result)) return result;
    ComPtr<Foundation::IAsyncOperation<Pdf::PdfDocument*>> loading;
    result = documents->LoadFromStreamAsync(input.Get(), &loading);
    if (FAILED(result)) return result;
    result = Await<Foundation::IAsyncOperationCompletedHandler<Pdf::PdfDocument*>>(loading.Get());
    if (FAILED(result)) return result;
    ComPtr<Pdf::IPdfDocument> document;
    result = loading->GetResults(&document);
    if (FAILED(result)) return result;
    UINT32 pages = 0;
    result = document->get_PageCount(&pages);
    if (FAILED(result)) return result;
    if (!pages) return E_FAIL;
    ComPtr<Pdf::IPdfPage> page;
    result = document->GetPage(0, &page);
    if (FAILED(result)) return result;
    // The size includes the page's rotation.
    Foundation::Size extent{};
    result = page->get_Size(&extent);
    if (FAILED(result)) return result;
    if (!(extent.Width > 0 && extent.Height > 0 && std::isfinite(extent.Width) && std::isfinite(extent.Height))) return E_FAIL;
    const double scale = size / static_cast<double>(std::max(extent.Width, extent.Height));
    const UINT width = std::max(1u, std::min(size, static_cast<UINT>(std::lround(extent.Width * scale))));
    const UINT height = std::max(1u, std::min(size, static_cast<UINT>(std::lround(extent.Height * scale))));
    ComPtr<Pdf::IPdfPageRenderOptions> options;
    result = Activate(L"Windows.Data.Pdf.PdfPageRenderOptions", IID_PPV_ARGS(&options));
    if (FAILED(result)) return result;
    result = options->put_DestinationWidth(width);
    if (SUCCEEDED(result)) result = options->put_DestinationHeight(height);
    if (FAILED(result)) return result;
    ComPtr<Streams::IRandomAccessStream> output;
    result = Activate(L"Windows.Storage.Streams.InMemoryRandomAccessStream", IID_PPV_ARGS(&output));
    if (FAILED(result)) return result;
    ComPtr<Foundation::IAsyncAction> rendering;
    result = page->RenderWithOptionsToStreamAsync(output.Get(), options.Get(), &rendering);
    if (FAILED(result)) return result;
    result = Await<Foundation::IAsyncActionCompletedHandler>(rendering.Get());
    if (FAILED(result)) return result;
    result = output->Seek(0);
    if (FAILED(result)) return result;
    ComPtr<IStream> image;
    result = CreateStreamOverRandomAccessStream(output.Get(), IID_PPV_ARGS(&image));
    if (FAILED(result)) return result;
    return Decode(image.Get(), size, bitmap);
}

// The blue ADF mark, small, in the page's bottom-right corner of Explorer and
// desktop thumbnails. It is scaled from the icon's 256-pixel frame, because
// Windows would stretch the nearest small frame and soften the logo's shape.
// The desktop's medium icons ask for 48 pixels; smaller views show icons.
void StampLogo(HBITMAP bitmap) {
    DIBSECTION section{};
    if (GetObjectW(bitmap, sizeof(section), &section) != sizeof(section) || !section.dsBm.bmBits) return;
    const int width = section.dsBm.bmWidth, height = section.dsBm.bmHeight;
    if (std::max(width, height) < 40) return;
    const int mark = std::min({std::max(10, static_cast<int>(std::lround(std::max(width, height) * 0.11))), width, height});
    const int margin = std::max(1, mark / 4);
    HICON icon = static_cast<HICON>(LoadImageW(g_module, MAKEINTRESOURCEW(101), IMAGE_ICON, 256, 256, LR_DEFAULTCOLOR));
    if (!icon) return;
    std::vector<BYTE> logo(static_cast<size_t>(mark) * mark * 4);
    ComPtr<IWICImagingFactory> factory;
    ComPtr<IWICBitmap> source;
    ComPtr<IWICFormatConverter> premultiplied;
    ComPtr<IWICBitmapScaler> scaler;
    // Scaling premultiplied pixels keeps the antialiased edge free of dark fringes.
    const bool scaled = SUCCEEDED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)))
        && SUCCEEDED(factory->CreateBitmapFromHICON(icon, &source))
        && SUCCEEDED(factory->CreateFormatConverter(&premultiplied))
        && SUCCEEDED(premultiplied->Initialize(source.Get(), GUID_WICPixelFormat32bppPBGRA, WICBitmapDitherTypeNone, nullptr, 0, WICBitmapPaletteTypeCustom))
        && SUCCEEDED(factory->CreateBitmapScaler(&scaler))
        && SUCCEEDED(scaler->Initialize(premultiplied.Get(), mark, mark, WICBitmapInterpolationModeFant))
        && SUCCEEDED(scaler->CopyPixels(nullptr, mark * 4, static_cast<UINT>(logo.size()), logo.data()));
    DestroyIcon(icon);
    if (!scaled) return;
    auto* pixels = static_cast<BYTE*>(section.dsBm.bmBits);
    const int left = std::max(0, width - mark - margin), top = std::max(0, height - mark - margin);
    for (int y = 0; y < mark; ++y) {
        for (int x = 0; x < mark; ++x) {
            const BYTE* mark_pixel = &logo[(static_cast<size_t>(y) * mark + x) * 4];
            BYTE* page = pixels + (static_cast<size_t>(top + y) * width + left + x) * 4;
            for (int channel = 0; channel < 3; ++channel)
                page[channel] = static_cast<BYTE>(mark_pixel[channel] + page[channel] * (255 - mark_pixel[3]) / 255);
        }
    }
}

class ThumbnailProvider final : public IInitializeWithStream, public IThumbnailProvider {
    std::atomic<ULONG> references_{1};
    ComPtr<IStream> stream_;
public:
    ThumbnailProvider() { ++g_objects; }
    ~ThumbnailProvider() { --g_objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (IsEqualIID(iid, IID_IUnknown) || IsEqualIID(iid, __uuidof(IInitializeWithStream))) *value = static_cast<IInitializeWithStream*>(this);
        else if (IsEqualIID(iid, __uuidof(IThumbnailProvider))) *value = static_cast<IThumbnailProvider*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override { const ULONG remaining = --references_; if (!remaining) delete this; return remaining; }
    HRESULT STDMETHODCALLTYPE Initialize(IStream* stream, DWORD) override {
        if (!stream) return E_INVALIDARG;
        if (stream_) return HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED);
        stream_ = stream;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetThumbnail(UINT size, HBITMAP* bitmap, WTS_ALPHATYPE* alpha) override {
        if (!bitmap || !alpha) return E_POINTER;
        *bitmap = nullptr;
        *alpha = WTSAT_UNKNOWN;
        if (!stream_) return E_UNEXPECTED;
        if (!size || size > kMaxSize) return E_INVALIDARG;
        try {
            const HRESULT result = RenderFirstPage(stream_.Get(), size, bitmap);
            if (FAILED(result)) return result;
            StampLogo(*bitmap);
            *alpha = WTSAT_RGB;
            return result;
        } catch (const std::bad_alloc&) { return E_OUTOFMEMORY; }
          catch (...) { return E_FAIL; }
    }
};
} // namespace

HRESULT CreateThumbnailProvider(REFIID iid, void** value) {
    auto* provider = new (std::nothrow) ThumbnailProvider();
    if (!provider) return E_OUTOFMEMORY;
    const HRESULT result = provider->QueryInterface(iid, value);
    provider->Release();
    return result;
}
} // namespace adf
