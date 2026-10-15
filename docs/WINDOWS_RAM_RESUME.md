# Windows RAM suspend/resume

This branch extends `backup-named-ram` (`d387d4a8`). The alternative that uses only the existing VRAM resize command is retained on `贮藏_zero-speed干净版` (`70db164e`). Neither backup branch is rewritten.

## Behavior

| Web action | Engine | GPU allocation | Next conversation |
| --- | --- | --- | --- |
| 高速 / 中速 | stays running | expert cache resized at the configured reserve | uses that mode |
| 零速 | stays running | expert cache at its inference floor; dense/KV/MTP remain | stays in zero mode |
| 卸载 | stays running, same PID and CUDA host registrations | expert cache evicted; other large device allocations copied to locked RAM and unmapped | restores original mode and continues the existing KV |
| 完全卸载 | exits | GPU allocations and retained weight sections released | cold weight loading; restores the configured retained session |
| Close Windows terminal | server and descendants exit through a Windows Job Object | all resources released, including retained sessions | next start is cold |

Ordinary unload does not run MSAVE/MRESTORE, restart CUDA, reread the model, or register the expert arena again. `PARK` evicts the expert cache, and snapshots the other tracked device allocations. `WAKE [reserve_mib]` maps new physical GPU memory at the **original virtual addresses**, restores their bytes, and refills the expert cache from resident CPU weights. Sessions and CUDA graphs retain their pointers.

The mode is opt-in: set `env.STRATA_DEVICE_PARK` to `"1"`, keep `vram_elastic: true`, and use `shared_expert_arena: "mem:auto"` for the resident weight/PLE source. The IQ3_S configurations enable these. The non-Windows and disabled paths call the original CUDA allocator. Device parking supports one Windows NVIDIA GPU; layer splitting, pipelining and a peer GPU are refused. Allocations smaller than 256 KiB and the CUDA context remain on the GPU.

Snapshot storage uses VirtualAlloc + VirtualLock, with sufficient physical RAM checked before unmapping any device memory. Historical KV payloads (`ConversationBuffer` segments) also use a resident allocator in this mode. Resume frees the device snapshot and restores its previous working-set quota. Failure to map the device on resume preserves the snapshot for a retry. The expert cache is reconstructed from its existing CPU source instead of retaining another 30 GiB copy.

The shared resident weights are approximately 78.2 GiB for this model. The measured device snapshot is approximately 8.2 GiB. Historical conversations continue to obey the configured conversation-cache budget; memory use can grow when new conversations are cached, independently of device snapshot cleanup.

## Build and visible startup

Build with the existing Windows toolchain:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build-named-ram.ps1
cmd /c tools\build-device-park-test.cmd
```

The build script refreshes Windows source timestamps because WSL/DrvFS timestamp caching previously caused Ninja to reuse old objects. `device_park_test` is excluded from the normal all target.

Deploy `build-0.1.44.1-out\strata.exe` to `engine\strata.exe` while the previous engine has exited, then launch **run-iq3_s.bat** in Windows. `tools\start-visible-iq3.ps1` creates a normal visible Windows terminal and refuses to launch a second listener on port 8080. It does not start a hidden WSL model service.

Closing the console is backed by a non-inheritable Windows Job Object owned by the server. Windows can retire tens of GiB of locked mappings after the process signals exit, so RAM reclamation can lag the disappearance of the listener/child processes. The acceptance record includes a follow-up confirming all PIDs were gone and physical use returned to about 21.6 GiB.

## Acceptance and limits

Run using the Windows interpreter, after the visible server is ready:

```powershell
.venv\Scripts\python.exe tools\verify_device_park.py
.venv\Scripts\python.exe tools\verify_device_park_long.py
.venv\Scripts\python.exe tools\verify_device_park_full.py
# This closes the exact console launched for the test; use its returned ConsolePID.
.venv\Scripts\python.exe tools\verify_console_close.py <ConsolePID>
```

The scripts save raw phase results. They check same PID, real NVML memory use (not only an old CUDA reply), high/mid/zero semantics, KV token reuse, and snapshot bytes returning to zero. Engine process I/O counters include stdin/stdout and logging; whole-disk counters also include other programs and Windows activity.

Measured on the final build, including the historical-KV resident allocator, on this 48 GiB RTX 4090 / 192 GiB Windows machine:

- First high-mode unload after cold startup: 4.60 s; subsequent ones 4.22–4.40 s. These measurements include actual board memory falling below 8 GiB, not merely the command returning.
- High-mode resume: 3.60–4.04 s in the engine; the HTTP batch request including resume and generation took 5.27–5.84 s. Mid-mode resume: 2.03 s in the engine.
- A 232,093-token prompt: unload 4.16 s, explicit wake 3.88 s. The next request reused 232,062 tokens and read 31; it took 4.04 s including normal request preparation/generation.
- Actual whole-board use while parked: about 5.8–6.3 GiB including vision/desktop applications. An earlier Windows GPU process-counter measurement attributed about 0.9 GiB of dedicated VRAM to the parked engine itself.
- Engine I/O during pure unload/wake was 6/11 bytes read and about 440/367 bytes written, consistent with control lines and logging, not model or KV-file transfer. The acceptance scripts assert an upper bound on this I/O.
- Snapshot storage returned to zero after every resume. Repeated cycles returned physical use to approximately the same baseline, roughly 115 GiB awake and 123 GiB parked. The approximately 8.2 GiB difference is the intentional locked device snapshot.
- The full-unload regression released all weights, exited the engine, and retained a 9.49 GiB session. Its cold reload took 66.0 s, after which 232,088 tokens were reused. Full unload is deliberately not the fast/no-model-read operation.

The final deployed binary's SHA-256 and raw final measurements are in `bench/results/local-device-park/final.json`, `locked-kv-acceptance.json` and `locked-kv-long-context.json`. Preliminary measurements are retained separately for traceability; the initial preliminary build once took 9.74 s on its first unload, so that figure is not used to characterize the final build.

Standalone CUDA checks cover six map/unmap cycles, byte-exact contents, the original captured CUDA graph, snapshot cleanup, freeing while asleep, and resident conversation-buffer allocation/copy. CPU session-format tests passed 201 checks; CPU conversation-cache tests passed 4,219 checks. Python lifecycle/admission tests passed 20 cases. Final deployment, ordinary cycles and the 232K-context test all passed after the KV-locking change.

These timings describe this hardware and model. Ordinary restore is a few seconds, not instant: it still transfers about 8 GiB over PCIe and refills tens of GiB of experts. GPU usage does not reach zero while its process/context and the separate vision encoder remain alive. Whole-machine disk activity is not a reliable proxy for model I/O; OS logs, other programs and a cold/full reload remain separate cases.
