# 手动测试 Strata decode

双击项目根目录 `TEST-DECODE.cmd`，输入上下文 token 数、最大输出 token 数、正式测试轮数、热身轮数和标签。
默认连接 `http://127.0.0.1:8080`，API 密钥 `123`，不需要重启 Strata。
脚本只发送 HTTP 请求，不调整显卡、超频、功耗、风扇或服务器配置。

在项目目录的 PowerShell 中也可以执行：

```powershell
.\TEST-DECODE.cmd --context 4k --output 1k --rounds 5 --warmup 1 --label stock
```

自行调整 Afterburner 后，用相同参数重复，只更改结果标签：

```powershell
.\TEST-DECODE.cmd --context 4k --output 1k --rounds 5 --warmup 1 --label mem1000
```

长上下文示例：

```powershell
.\TEST-DECODE.cmd --context 32k --output 2k --rounds 3 --warmup 1 --label ctx32k
```

- `--context`：总输入 token 数，包含聊天模板。`4k` 是 4096，`32k` 是 32768。
  先通过服务端 `/v1/messages/count_tokens` 填充到目标长度；若分词边界无法精确达到，会报告实际长度。
  `--context 0` 只发送生成任务，不添加参考材料。
- `--output`：最大输出 token 数。模型可能提前结束，脚本记录实际长度和结束原因。
- `--rounds`：正式测试轮数，默认 3。
- `--warmup`：额外热身轮数，默认 1，不计入统计。
- `--label`：结果标签，用于区分不超频、超频或不同配置。
- `--interval`：请求间隔秒数，默认 0。
- `--prompt-file`：可选，自定义生成任务的 UTF-8 文件；上下文填充仍以该任务为结尾。
- `--show-output`：实时显示模型生成内容；默认仅显示进度和每轮结果。
- `--dry-run`：只计数并保存请求，不运行模型。
- `--url`、`--api-key`、`--model`：可覆盖地址、密钥和请求模型别名。
  默认别名 `strata_compress` 使用当前加载的同一个模型，关闭思考。

结果保存在 `local-upgrade/decode-bench/`，每次生成独立 JSON 和 CSV。
JSON 包括请求、每轮原生 timings、实际输入输出 token 数、输出文本和哈希。
API 密钥不会写进结果文件。

Decode 速度用 Strata 的原生 `predicted_n / predicted_ms` 计算，排除 prefill 和翻译缓冲，
并输出正式轮次的中位数、均值、范围和标准差。每 5 秒显示等待/生成进度。

各轮使用相同输入、温度 0、固定 seed 42；模型的动态缓存和 MTP 仍可能让输出与耗时波动。
脚本保留现有缓存策略，并记录每轮命中 token 数。比较显存设置时使用相同输入、输出上限和轮数，
让其他客户端空闲，优先比较热身后的多轮中位数；短测不代表长期稳定性。
