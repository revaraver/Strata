// Validate data, graph pointer stability, repeated park/wake, and freeing an allocation while asleep.
#include "strata/core/device_park.hpp"
#include "strata/core/conversation_buffer.hpp"
#include <windows.h>
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
#define CK(x) do { auto r=(x); if(r!=cudaSuccess) { std::fprintf(stderr,"%s: %s\n",#x,cudaGetErrorString(r)); return 1; } } while(0)
int main() {
    _putenv_s("STRATA_DEVICE_PARK", "1");
    SIZE_T quota_before=0,quota_high=0; DWORD quota_flags=0;
    GetProcessWorkingSetSizeEx(GetCurrentProcess(),&quota_before,&quota_high,&quota_flags);
    {
        strata::core::ConversationBuffer buffer;
        buffer.resize((33u<<20)+3, 73);
        auto copy=buffer;
        if (!(copy==buffer)) return 9;
        bool locked=true;
        buffer.visit(0,buffer.size(),[&](const uint8_t* p,size_t n,size_t) {
            // VirtualUnlock succeeds only for locked pages. Re-lock immediately after probing both ends.
            const bool first=VirtualUnlock((void*)p,1)!=0;
            VirtualLock((void*)p,1);
            const bool last=VirtualUnlock((void*)(p+n-1),1)!=0;
            VirtualLock((void*)(p+n-1),1);
            locked = locked && first && last;
            return true;
        });
        if(!locked) return 10;
        buffer.resize(0);
        copy.resize(0);
    }
    SIZE_T quota_after=0;
    GetProcessWorkingSetSizeEx(GetCurrentProcess(),&quota_after,&quota_high,&quota_flags);
    if(quota_after!=quota_before) return 11;
    constexpr size_t bytes=512u<<20;
    unsigned char* device=nullptr;
    CK(strata::core::park::allocate(&device,bytes));
    CK(cudaMemset(device,17,bytes));
    cudaStream_t stream; CK(cudaStreamCreate(&stream));
    cudaGraph_t graph; cudaGraphExec_t exec;
    CK(cudaStreamBeginCapture(stream,cudaStreamCaptureModeGlobal));
    CK(cudaMemsetAsync(device,29,bytes/2,stream));
    CK(cudaStreamEndCapture(stream,&graph));
    CK(cudaGraphInstantiate(&exec,graph,nullptr,nullptr,0));
    std::vector<unsigned char> host(bytes);
    std::string error;
    auto stats=[](const char* name) { size_t f=0,t=0; cudaMemGetInfo(&f,&t); std::printf("%s free=%llu MiB\n",name,(unsigned long long)(f>>20)); };
    stats("allocated");
    for(int i=0;i<6;++i) {
        if(!strata::core::park::release(error)) { std::fprintf(stderr,"park: %s\n",error.c_str()); return 2; }
        if(!strata::core::park::asleep() || strata::core::park::saved_bytes()!=bytes) return 3;
        stats("parked");
        if(!strata::core::park::resume(error)) { std::fprintf(stderr,"wake: %s\n",error.c_str()); return 4; }
        if(strata::core::park::asleep() || strata::core::park::saved_bytes()!=0) return 5;
        CK(cudaGraphLaunch(exec,stream)); CK(cudaStreamSynchronize(stream));
        CK(cudaMemcpy(host.data(),device,bytes,cudaMemcpyDeviceToHost));
        for(size_t j=0;j<bytes;++j) if(host[j]!=(j<bytes/2?29:17)) return 6;
    }
    CK(cudaGraphExecDestroy(exec)); CK(cudaGraphDestroy(graph)); CK(cudaStreamDestroy(stream));
    if(!strata::core::park::release(error)) return 7;
    CK(strata::core::park::deallocate(device));
    if(!strata::core::park::resume(error)) return 8;
    std::puts("PASS: six GPU park/wake cycles, original CUDA graph/data, snapshot cleanup and sleeping free");
    return 0;
}
