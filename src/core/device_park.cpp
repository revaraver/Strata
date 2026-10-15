// Windows/CUDA only. VMM preserves pointers held by sessions, kernels and CUDA graphs across sleep.
#include "strata/core/device_park.hpp"
#include "strata/core/resident_allocator.hpp"
#include "strata/platform/memory.hpp"
#include <cuda.h>
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <map>
#include <mutex>

namespace strata::core::park {
namespace {
struct Block {
    CUdeviceptr va = 0;
    CUmemGenericAllocationHandle handle = 0;
    size_t bytes = 0, physical = 0, offset = 0;
    int device = 0;
    bool mapped = false;
};
struct State {
    std::mutex mutex;
    std::map<void*, Block> blocks;
    uint8_t* host = nullptr;
    size_t host_bytes = 0;
    SIZE_T old_min_ws = 0, old_max_ws = 0;
    DWORD old_ws_flags = 0;
    std::atomic<bool> sleeping{false};
};
// The process releases OS/CUDA resources at exit. Avoid CUDA calls from a static destructor after runtime shutdown.
State& state() { static State* s = new State; return *s; }
CUmemAllocationProp prop(int device) {
    CUmemAllocationProp p{};
    p.type = CU_MEM_ALLOCATION_TYPE_PINNED;
    p.location.type = CU_MEM_LOCATION_TYPE_DEVICE;
    p.location.id = device;
    return p;
}
bool check(CUresult r, std::string& e, const char* operation) {
    if (r == CUDA_SUCCESS) return true;
    const char* text = nullptr;
    cuGetErrorString(r, &text);
    e = std::string("device park: ") + operation + ": " + (text ? text : "driver error");
    return false;
}
bool map(Block& b, std::string& e) {
    const auto p = prop(b.device);
    if (!check(cuMemCreate(&b.handle, b.physical, &p, 0), e, "allocate physical pages")) return false;
    if (!check(cuMemMap(b.va, b.physical, 0, b.handle, 0), e, "map pages")) {
        cuMemRelease(b.handle); b.handle = 0; return false;
    }
    CUmemAccessDesc a{}; a.location = p.location; a.flags = CU_MEM_ACCESS_FLAGS_PROT_READWRITE;
    if (!check(cuMemSetAccess(b.va, b.physical, &a, 1), e, "set page access")) {
        cuMemUnmap(b.va, b.physical); cuMemRelease(b.handle); b.handle = 0; return false;
    }
    b.mapped = true;
    return true;
}
void free_host(State& s) {
    if (s.host) {
        strata::platform::unlock_resident(s.host, s.host_bytes);
        VirtualFree(s.host, 0, MEM_RELEASE);
    }
    s.host = nullptr; s.host_bytes = 0;
    if (s.old_max_ws) SetProcessWorkingSetSizeEx(GetCurrentProcess(), s.old_min_ws, s.old_max_ws, s.old_ws_flags);
    s.old_min_ws = s.old_max_ws = 0;
}
bool restore(State& s, std::string& e) {
    // Map everything first. If the card is still in use by another application, leave the RAM snapshot intact.
    for (auto& [p, b] : s.blocks) {
        if (!b.mapped && !map(b, e)) {
            for (auto& [q, c] : s.blocks) {
                if (c.mapped) { cuMemUnmap(c.va, c.physical); cuMemRelease(c.handle); c.mapped = false; c.handle = 0; }
            }
            return false;
        }
    }
    for (auto& [p, b] : s.blocks)
        if (!check(cuMemcpyHtoD(b.va, s.host + b.offset, b.bytes), e, "restore device contents")) return false;
    free_host(s);
    s.sleeping = false;
    strata::core::resident::unlock_all();
    return true;
}
} // namespace
bool enabled() {
    static const bool on = [] { const char* v = std::getenv("STRATA_DEVICE_PARK"); return v && std::string(v) == "1"; }();
    return on;
}
bool asleep() { return state().sleeping.load(); }
uint64_t saved_bytes() { return state().host_bytes; }
cudaError_t allocate(void** p, size_t bytes) {
    // Tiny control buffers stay in the CUDA allocator. This avoids a physical VMM granule per scalar.
    if (!enabled() || bytes < (256u << 10)) return cudaMalloc(p, bytes);
    *p = nullptr;
    auto& s = state(); std::lock_guard<std::mutex> lock(s.mutex);
    if (s.sleeping) return cudaErrorNotReady;
    Block b; b.bytes = bytes;
    auto r = cudaGetDevice(&b.device);
    if (r != cudaSuccess) return r;
    r = cudaFree(nullptr); // initialize this thread's runtime primary context before using the driver API
    if (r != cudaSuccess) return r;
    auto pr = prop(b.device);
    size_t granule = 0;
    if (cuMemGetAllocationGranularity(&granule, &pr, CU_MEM_ALLOC_GRANULARITY_MINIMUM) != CUDA_SUCCESS)
        return cudaErrorNotSupported;
    b.physical = (bytes + granule - 1) / granule * granule;
    if (cuMemAddressReserve(&b.va, b.physical, 0, 0, 0) != CUDA_SUCCESS) return cudaErrorMemoryAllocation;
    std::string e;
    if (!map(b, e)) { cuMemAddressFree(b.va, b.physical); return cudaErrorMemoryAllocation; }
    *p = reinterpret_cast<void*>(b.va);
    s.blocks.emplace(*p, b);
    return cudaSuccess;
}
cudaError_t deallocate(void* p) {
    if (!p || !enabled()) return cudaFree(p);
    auto& s = state(); std::lock_guard<std::mutex> lock(s.mutex);
    auto it = s.blocks.find(p);
    if (it == s.blocks.end()) return cudaFree(p);
    auto& b = it->second;
    // cudaFree was synchronous at these call sites; preserve that contract.
    if (b.mapped) {
        auto r = cudaDeviceSynchronize(); if (r != cudaSuccess) return r;
        if (cuMemUnmap(b.va, b.physical) != CUDA_SUCCESS) return cudaErrorUnknown;
        cuMemRelease(b.handle);
    }
    cuMemAddressFree(b.va, b.physical);
    s.blocks.erase(it);
    return cudaSuccess;
}
bool release(std::string& e) {
    if (!enabled()) { e = "device parking was not enabled at engine startup"; return false; }
    auto& s = state(); std::lock_guard<std::mutex> lock(s.mutex);
    if (s.sleeping) return true;
    // Conversations may page to disk while the GPU runs; while it sleeps the next request must not
    // pay page-file reads, so lock them for the sleep (and refuse the park if RAM cannot hold them).
    if (!strata::core::resident::lock_all(e)) return false;
    if (cudaDeviceSynchronize() != cudaSuccess) { strata::core::resident::unlock_all(); e = "device did not synchronize before parking"; return false; }
    size_t total = 0;
    for (auto& [p, b] : s.blocks) { b.offset = total; total += (b.bytes + 4095) / 4096 * 4096; }
    if (!total) { strata::core::resident::unlock_all(); e = "no parkable device allocations"; return false; }
    // Refuse before changing any GPU mappings if physical RAM cannot hold the snapshot plus a 4 GiB margin.
    MEMORYSTATUSEX ms{}; ms.dwLength = sizeof ms; GlobalMemoryStatusEx(&ms);
    if (ms.ullAvailPhys < total + (4ull << 30)) { strata::core::resident::unlock_all(); e = "not enough physical RAM to park the GPU without paging"; return false; }
    s.host = (uint8_t*) VirtualAlloc(nullptr, total, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    s.host_bytes = total;
    if (!s.host) { s.host_bytes = 0; strata::core::resident::unlock_all(); e = "cannot allocate the device RAM snapshot"; return false; }
    GetProcessWorkingSetSizeEx(GetCurrentProcess(), &s.old_min_ws, &s.old_max_ws, &s.old_ws_flags);
    const auto locked = strata::platform::lock_resident(s.host, total);
    if (!locked.ok || locked.locked_bytes < total) { free_host(s); strata::core::resident::unlock_all(); e = "cannot lock the device RAM snapshot: " + locked.note; return false; }
    for (auto& [p, b] : s.blocks)
        if (!check(cuMemcpyDtoH(s.host + b.offset, b.va, b.bytes), e, "save device contents")) { free_host(s); strata::core::resident::unlock_all(); return false; }
    s.sleeping = true;
    for (auto& [p, b] : s.blocks) {
        if (!check(cuMemUnmap(b.va, b.physical), e, "release device mapping")) {
            std::string rollback;
            if (!restore(s, rollback)) e += "; rollback: " + rollback;
            return false;
        }
        b.mapped = false;
        cuMemRelease(b.handle); b.handle = 0;
    }
    return true;
}
bool resume(std::string& e) {
    auto& s = state(); std::lock_guard<std::mutex> lock(s.mutex);
    if (!s.sleeping) return true;
    return restore(s, e);
}
} // namespace strata::core::park
