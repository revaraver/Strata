// Keep retained conversation payloads out of the Windows page file in the opt-in device-park mode.
#pragma once
#include <cstdlib>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
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
inline std::mutex& mutex() { static auto* m=new std::mutex; return *m; }
inline size_t pages(size_t bytes) { return (bytes+4095)/4096*4096; }
inline void* allocate(size_t bytes) {
    std::lock_guard<std::mutex> lock(mutex());
    bytes = pages(bytes);
    MEMORYSTATUSEX ms{}; ms.dwLength=sizeof ms; GlobalMemoryStatusEx(&ms);
    if (ms.ullAvailPhys < bytes+(1ull<<30)) throw std::bad_alloc();
    void* p=VirtualAlloc(nullptr,bytes,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    if (!p) throw std::bad_alloc();
    SIZE_T low=0,high=0; DWORD flags=0;
    GetProcessWorkingSetSizeEx(GetCurrentProcess(),&low,&high,&flags);
    const SIZE_T want=low+bytes;
    if (!SetProcessWorkingSetSizeEx(GetCurrentProcess(),want,high>want?high:want+(64<<20),flags) ||
        !VirtualLock(p,bytes)) {
        SetProcessWorkingSetSizeEx(GetCurrentProcess(),low,high,flags);
        VirtualFree(p,0,MEM_RELEASE); throw std::bad_alloc();
    }
    return p;
}
inline void deallocate(void* p,size_t bytes) {
    std::lock_guard<std::mutex> lock(mutex());
    bytes=pages(bytes);
    VirtualUnlock(p,bytes); VirtualFree(p,0,MEM_RELEASE);
    SIZE_T low=0,high=0; DWORD flags=0;
    if (GetProcessWorkingSetSizeEx(GetCurrentProcess(),&low,&high,&flags))
        SetProcessWorkingSetSizeEx(GetCurrentProcess(),low>bytes?low-bytes:low,high,flags);
}
#endif
} // namespace resident_detail

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
