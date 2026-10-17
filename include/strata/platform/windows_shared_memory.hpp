// Windows-only, opt-in resident model/session storage. No disk-backed mappings.
#pragma once
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>
#include <malloc.h>

namespace strata::platform::winmem {
inline std::string space() { const char* s = std::getenv("STRATA_MEMORY_NAMESPACE"); return s ? s : ""; }
inline uint64_t hash(const void* data, size_t n, uint64_t h = 14695981039346656037ull) {
    const auto* p = static_cast<const uint8_t*>(data);
    while (n--) { h ^= *p++; h *= 1099511628211ull; }
    return h;
}
inline std::string hex(uint64_t h) { char b[17]; std::snprintf(b, sizeof b, "%016llx", (unsigned long long) h); return b; }
inline std::runtime_error failure(const char* op) {
    return std::runtime_error(std::string(op) + ": Windows error " + std::to_string(GetLastError()));
}
inline std::wstring wide(const std::string& s) {
    const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), (int) s.size(), nullptr, 0);
    if (n <= 0) throw failure("UTF-8 path");
    std::wstring w((size_t) n, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), (int) s.size(), w.data(), n);
    return w;
}
struct Region {
    HANDLE handle = nullptr;
    uint8_t* data = nullptr;
    uint64_t bytes = 0;
    bool fresh = false;
    Region(const std::string& name, uint64_t n, bool create) : bytes(n) {
        auto w = wide(name);
        if (create) {
            handle = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                                        (DWORD) (n >> 32), (DWORD) n, w.c_str());
            fresh = GetLastError() != ERROR_ALREADY_EXISTS;
        } else handle = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, w.c_str());
        if (!handle) throw failure("named shared memory");
        data = (uint8_t*) MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, (SIZE_T) n);
        if (!data) { const DWORD e = GetLastError(); CloseHandle(handle); handle = nullptr; SetLastError(e); throw failure("MapViewOfFile"); }
    }
    ~Region() { if (data) UnmapViewOfFile(data); if (handle) CloseHandle(handle); }
    Region(const Region&) = delete;
    Region& operator=(const Region&) = delete;
};
inline std::mutex& mutex() { static std::mutex m; return m; }
inline std::map<std::string, std::shared_ptr<Region>>& regions() {
    static std::map<std::string, std::shared_ptr<Region>> r; return r;
}
// After drop_weights(), weight sections are transient: a lazy post-load read maps the range,
// serves the memcpy, and releases it with the caller's shared_ptr (DirectFile keeps its own).
// No region-table cache, no MEMKEEP line, so neither side re-commits or re-locks.
inline bool& weights_dropped() { static bool b = false; return b; }
// The engine retains views until shutdown. The server consumes MEMKEEP before READY/SAVED and holds
// its own handle/view/VirtualLock across engine restarts. No payload crosses stdout or Python's heap.
inline std::shared_ptr<Region> region(const std::string& name, uint64_t bytes, const char* kind,
                                      bool create = true) {
    std::lock_guard<std::mutex> lk(mutex());
    auto& r = regions();
    auto it = r.find(name);
    if (it != r.end()) {
        if (it->second->bytes != bytes) throw std::runtime_error("shared memory size changed: " + name);
        return it->second;
    }
    auto p = std::make_shared<Region>(name, bytes, create);
    r.emplace(name, p);
    // After drop_weights(), weight ranges re-read lazily stay engine-local (pointers must remain
    // valid for tensor_data callers) but are no longer announced: the server must not re-lock them.
    if (create && !(weights_dropped() && kind && std::strcmp(kind, "weights") == 0))
        { std::printf("MEMKEEP %s %llu %s\n", kind, (unsigned long long) bytes, name.c_str()); std::fflush(stdout); }
    return p;
}
inline void forget(const std::string& prefix) {
    std::lock_guard<std::mutex> lk(mutex());
    for (auto it = regions().begin(); it != regions().end();) {
        if (it->first == prefix || it->first.rfind(prefix + "-", 0) == 0) it = regions().erase(it); else ++it;
    }
}
struct Header { uint64_t magic, bytes, identity, ready; };
constexpr uint64_t magic = 0x324d454d41525453ull; // STRAMEM2
constexpr uint64_t header_bytes = 4096;

// Cache only the requested source range. Experts use the arena directly, never a second GGUF cache.
// Cold reads use one 8 MiB aligned staging block and bypass the Windows file cache.
// `out` receives the owning shared_ptr so a long-lived consumer (DirectFile) keeps the view mapped
// even after the region table forgets the name (drop_weights at READY).
inline const uint8_t* file_range_ref(const std::string& path, uint64_t offset, uint64_t n,
                                     std::shared_ptr<Region>* out) {
    const std::string ns = space();
    if (ns.empty() || n == 0) return nullptr;
    auto wp = wide(path);
    HANDLE f = CreateFileW(wp.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_NO_BUFFERING, nullptr);
    if (f == INVALID_HANDLE_VALUE) throw failure("open resident weight source");
    struct Close { HANDLE f; ~Close() { CloseHandle(f); } } close{f};
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(f, &info)) throw failure("weight identity");
    const uint64_t size = (uint64_t(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
    if (offset > size || n > size - offset) throw std::runtime_error("resident weight range outside source: " + path);
    uint64_t id = hash(&info.dwVolumeSerialNumber, sizeof info.dwVolumeSerialNumber);
    id = hash(&info.nFileIndexHigh, sizeof info.nFileIndexHigh, id);
    id = hash(&info.nFileIndexLow, sizeof info.nFileIndexLow, id);
    id = hash(&info.ftLastWriteTime, sizeof info.ftLastWriteTime, id);
    id = hash(&size, sizeof size, id); id = hash(&offset, sizeof offset, id); id = hash(&n, sizeof n, id);
    const std::string name = ns + "-w-" + hex(id);
    auto r = region(name, header_bytes + n, "weights");
    auto* h = (Header*) r->data;
    if (!r->fresh && (h->magic != magic || h->bytes != n || h->identity != id))
        throw std::runtime_error("incompatible resident weight range");
    if (h->ready == 1) { if (out) *out = r; return r->data + header_bytes; }
    *h = Header{magic, n, id, 0};
    constexpr uint64_t chunk = 8ull << 20, align = 4096;
    auto* buf = (uint8_t*) _aligned_malloc((size_t) chunk, (size_t) align);
    if (!buf) throw std::bad_alloc();
    struct Free { void* p; ~Free() { _aligned_free(p); } } free{buf};
    uint64_t at = offset / align * align, done = 0;
    LARGE_INTEGER pos{}; pos.QuadPart = (LONGLONG) at;
    if (!SetFilePointerEx(f, pos, nullptr, FILE_BEGIN)) throw failure("weight seek");
    while (done < n) {
        const uint64_t skip = offset + done - at;
        const DWORD ask = (DWORD) std::min<uint64_t>(chunk, (skip + n - done + align - 1) / align * align);
        DWORD got = 0;
        if (!ReadFile(f, buf, ask, &got, nullptr)) throw failure("weight read");
        if (got <= skip) throw std::runtime_error("short resident weight read");
        const uint64_t c = std::min<uint64_t>(got - skip, n - done);
        std::memcpy(r->data + header_bytes + done, buf + skip, (size_t) c);
        done += c; at += got;
    }
    MemoryBarrier(); h->ready = 1;
    if (out) *out = r;
    return r->data + header_bytes;
}
inline const uint8_t* file_range(const std::string& path, uint64_t offset, uint64_t n) {
    return file_range_ref(path, offset, n, nullptr);
}
// Once the engine has finished loading, the device holds the model and the weight sections are only
// redundant RAM: a pagefile-backed section charges full commit while any handle is open. Forget our
// own views and ask the server to release its locked copies (MEMDROP). Opt out with STRATA_MEMKEEP_KEEP=1.
inline void drop_weights() {
    const std::string ns = space();
    if (ns.empty()) return;
    weights_dropped() = true;
    forget(ns + "-w");
    std::printf("MEMDROP %s-w\n", ns.c_str()); std::fflush(stdout);
}
} // namespace strata::platform::winmem
#endif
