// Opt-in Windows/CUDA: keep device virtual addresses while their physical pages sleep in RAM.
#pragma once
#include <cuda_runtime.h>
#include <cstddef>
#include <cstdint>
#include <string>

namespace strata::core::park {
#if defined(_WIN32) && !defined(STRATA_USE_HIP)
cudaError_t allocate(void** p, size_t bytes);
cudaError_t deallocate(void* p);
bool enabled();
bool asleep();
uint64_t saved_bytes();
bool release(std::string& error);
bool resume(std::string& error);
#else
inline cudaError_t allocate(void** p, size_t bytes) { return cudaMalloc(p, bytes); }
inline cudaError_t deallocate(void* p) { return cudaFree(p); }
inline bool enabled() { return false; }
inline bool asleep() { return false; }
inline uint64_t saved_bytes() { return 0; }
inline bool release(std::string& e) { e = "device parking requires Windows CUDA"; return false; }
inline bool resume(std::string&) { return true; }
#endif
template<class T> cudaError_t allocate(T** p, size_t bytes) {
    return allocate(reinterpret_cast<void**>(p), bytes);
}
}  // namespace strata::core::park
