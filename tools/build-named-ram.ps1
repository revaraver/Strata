$ErrorActionPreference = 'Stop'
$root = 'D:\git\Strata-latest'
$files = @('src\platform\direct_file.cpp','src\ngram\ple_reader.cpp','include\strata\platform\direct_file.hpp','src\core\pinned.cu','src\core\weights.cpp','src\core\mtp.cpp','src\core\conversation_file.cpp','src\core\expert_source.cpp','src\program\generate.cpp','src\program\windows_memory_sessions.inc','include\strata\core\pinned.hpp','include\strata\core\conversation_cache.hpp','include\strata\artifact\gguf_reader.hpp','include\strata\platform\windows_shared_memory.hpp')
$files += @('CMakeLists.txt','src\core\device_park.cpp','include\strata\core\device_park.hpp',
    'include\strata\core\resident_allocator.hpp')
$files += @(& git -C $root diff --name-only -- '*.cpp' '*.cu' '*.hpp')
foreach ($file in ($files | Select-Object -Unique)) { (Get-Item (Join-Path $root $file)).LastWriteTime = Get-Date }
& cmd.exe /c "$root\BUILD-0.1.44.1.cmd"
exit $LASTEXITCODE
