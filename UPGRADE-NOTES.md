# Strata 0.1.40.3 本地升级记录

新版位于 `D:\git\Strata-latest`，基于官方发布标签 `v0.1.40.3`，提交 `d5ea7133741e67743c0e886bb426c0ce8d69cf6c`。
原目录 `D:\git\Strata-main` 保留，原配置、启动脚本、自编译 EXE 和源代码没有被覆盖。额外备份及 SHA-256 清单在 `local-upgrade\backup-before-upgrade`、`local-upgrade\backup-manifest.json`。

## 引擎与加载优化

三个新版配置均使用 `build-huihui-q4-load\strata.exe`，编译版本 0.1.40.3，CUDA 13.0.2，sm_89，AVX2 portable。引擎 SHA-256 与来源在同目录的 `BUILD.json`。

保留原有 Windows 加载补丁：每批64个专家、映射范围预取、并行组装、与GPU缓存填充重叠。源码改动保存在 `local-upgrade\windows-startup-64.patch`，只涉及 `expert_source.hpp`、`expert_source.cpp`、`generate.cpp`。huihui重复分片架构元数据的兼容修复在新版官方代码中已具备，沿用官方实现。

三个配置的 `env.STRATA_STARTUP_BATCH` 均为 `1`。日志应出现 `Windows startup pipeline: 64 experts per batch`。这项优化影响启动时GPU专家缓存的填充，不等于所有启动步骤或SSD读盘速度。

K-quant MMQ 保持官方默认 OFF，没有混入额外的提示词计算实验。模型、pack、MTP和mmproj直接复用原路径，没有重新下载或改写大模型权重。

复编译入口：`BUILD-HUIHUI-Q4-LOAD.cmd`。它复用既有的 CUDA 13.0.2、VS2022，以及旧目录中相同固定提交的ggml源码依赖。运行时DLL目录也保留已有路径，因此请继续保留旧目录。

官方原版0.1.40.3引擎及视觉编码器也在 `engine` 中，发布压缩包已核对GitHub提供的SHA-256。

## 官方校准

按用户要求，三模型性能校准使用上游 `setup.calibrate_config`；它调用未经修改的 `tools\calibrate.py`，执行默认题目、预热、候选扫描和交替复测，自动写回配置及 `%APPDATA%\Strata\settings.json`。只有超过官方3%门槛的候选才保留。

`local-upgrade\calibrate_all.py` 仅负责按顺序调用这套官方流程并保存返回报告，没有自定义测速题或选择算法。

顺序：huihui UD-Q4_K_XL → SC117 IQ3_S → Swift IQ3_XXS。

结果：`local-upgrade\official-calibration\summary.json`；各模型完整报告为同目录的 `*.report.json`。结果在每个模型完成后写入，三项 `success` 均为 `true` 才表示整轮成功。

校准前新版配置备份：`local-upgrade\before-calibration`、`local-upgrade\official-calibration\*.json.before`；全局设置备份：`local-upgrade\official-calibration\global-settings-before-calibration.json`。

官方工具调整PCIe分工、MTP阈值、CPU线程数及专家交换策略。上下文、KV、视觉配置、采样和思考预算、鉴权、引擎路径及64专家加载补丁保留。

若以后重跑：先关闭正在运行的Strata，再执行 `CALIBRATE-ALL.bat`。校准期间独占GPU，顺序完成三模型，不启动HTTP服务。官方校准题的速度不能直接当作实际长聊天或Agent请求的速度。

## 日常启动与回退

新版启动脚本：

- `D:\git\Strata-latest\run-huihui-ud-q4_k_xl.bat`
- `D:\git\Strata-latest\run-iq3_s.bat`
- `D:\git\Strata-latest\run-swift-iq3_xxs.bat`

保持原来的 `0.0.0.0:8080` 和API鉴权；现有客户端仍用 `http://192.168.2.120:8080/v1`。三个模型共用8080，每次只启动一个。

回退时关闭新版，运行 `D:\git\Strata-main` 中相应的原启动脚本即可。不要把新版源码直接覆盖进旧目录。未来更新此Git副本前，应先保留本地加载补丁，并在新版本上重新应用、编译。
