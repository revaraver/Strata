// Conversation payloads use VirtualAlloc so they CAN be paged like normal memory; the opt-in
// device-park mode locks them only while the GPU sleeps, when a page-out would mean disk I/O.
#pragma once
#include <cstdlib>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <type_traits>
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
#ifndef NOMINMAX
#define NOMINMAX
#endif
// Windows RPC aliases must not change model variable names in including translation units.
#pragma push_macro("small")
#pragma push_macro("near")
#pragma push_macro("far")
#include <windows.h>
#pragma pop_macro("far")
#pragma pop_macro("near")
#pragma pop_macro("small")
#endif
namespace strata::core {
namespace resident_detail {
inline bool enabled() {
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
    static const bool on = [] { const char* v = std::getenv("STRATA_DEVICE_PARK"); return v && v[0]=='1' && !v[1]; }();
    return on;
#else
    return false;
#endif
}
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
struct Registry {
    std::mutex mutex;
    std::map<void*, size_t> live;   // base -> rounded bytes
    bool locked = false;
    SIZE_T old_min = 0, old_max = 0;
    DWORD old_flags = 0;
};
inline Registry& registry() { static auto* r = new Registry; return *r; }
inline size_t pages(size_t bytes) { return (bytes+4095)/4096*4096; }
inline void* allocate(size_t bytes) {
    bytes = pages(bytes);
    void* p = VirtualAlloc(nullptr, bytes, MEM_RESERVE|MEM_COMMIT, PAGE_READWRITE);
    if (!p) throw std::bad_alloc();
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    r.live.emplace(p, bytes);
    return p;
}
inline void deallocate(void* p, size_t bytes) {
    bytes = pages(bytes);
    auto& r = registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    if (r.locked) VirtualUnlock(p, bytes);   // eviction while parked: drop this one lock first
    r.live.erase(p);
    VirtualFree(p, 0, MEM_RELEASE);
}
#endif
} // namespace resident_detail

namespace resident {
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
// Lock every live conversation block, exactly the bytes they hold (never the cache budget).
// Device park calls this: while the GPU sleeps, a conversation page on disk would turn the next
// request into page-file reads. It fails (leaving nothing locked) when physical RAM cannot hold
// them, so the park can refuse and retry later. Wake unlocks them and the pager resumes.
inline bool lock_all(std::string& error) {
    auto& r = resident_detail::registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    if (r.locked) return true;
    size_t total = 0;
    for (const auto& [p, b] : r.live) total += b;
    if (total) {
        MEMORYSTATUSEX ms{}; ms.dwLength = sizeof ms; GlobalMemoryStatusEx(&ms);
        if (ms.ullAvailPhys < total + (1ull<<30)) { error = "not enough physical RAM to lock the conversation cache"; return false; }
        GetProcessWorkingSetSizeEx(GetCurrentProcess(), &r.old_min, &r.old_max, &r.old_flags);
        const SIZE_T want = r.old_min + total;
        if (!SetProcessWorkingSetSizeEx(GetCurrentProcess(), want, r.old_max>want?r.old_max:want+(64<<20), r.old_flags)) {
            error = "cannot raise the working-set quota for the conversation cache"; return false;
        }
        size_t done_n = 0, i = 0;
        for (const auto& [p, b] : r.live) {
            if (!VirtualLock(p, b)) {
                for (auto& [q, c] : r.live) { if (i++ >= done_n) break; VirtualUnlock(q, c); }
                SetProcessWorkingSetSizeEx(GetCurrentProcess(), r.old_min, r.old_max, r.old_flags);
                error = "cannot lock the conversation cache in RAM"; return false;
            }
            ++done_n;
        }
    }
    r.locked = true;
    return true;
}
inline void unlock_all() {
    auto& r = resident_detail::registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    if (!r.locked) return;
    for (const auto& [p, b] : r.live) VirtualUnlock(p, b);
    if (r.old_max) SetProcessWorkingSetSizeEx(GetCurrentProcess(), r.old_min, r.old_max, r.old_flags);
    r.old_min = r.old_max = 0;
    r.locked = false;
}
inline bool locked() { return resident_detail::registry().locked; }
#else
inline bool lock_all(std::string&) { return true; }
inline void unlock_all() {}
inline bool locked() { return false; }
#endif
} // namespace resident

template<class T> struct ResidentAllocator {
    using value_type=T;
    using is_always_equal=std::true_type;
    ResidentAllocator()=default;
    template<class U> ResidentAllocator(const ResidentAllocator<U>&) {}
    T* allocate(size_t n) {
        if(n>std::numeric_limits<size_t>::max()/sizeof(T)-4096) throw std::bad_alloc();
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
        if(n && resident_detail::enabled()) return static_cast<T*>(resident_detail::allocate(n*sizeof(T)));
#endif
        return std::allocator<T>{}.allocate(n);
    }
    void deallocate(T* p,size_t n) noexcept {
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
        if(n && resident_detail::enabled()) { resident_detail::deallocate(p,n*sizeof(T)); return; }
#endif
        std::allocator<T>{}.deallocate(p,n);
    }
    template<class U> bool operator==(const ResidentAllocator<U>&) const { return true; }
    template<class U> bool operator!=(const ResidentAllocator<U>&) const { return false; }
};
} // namespace strata::core
