# 本地 CPU 思考翻译

本机当前选择 `utrobinmv/t5_translate_en_ru_zh_small_1024`，约 1.11 亿参数。
这是针对英、俄、中翻译训练的 T5 编码器—解码器 NMT，使用 CTranslate2 CPU INT8，
不使用聊天 LLM、提示词推理服务或显卡。转换后的模型与词表约 110 MiB。

客户端继续使用原 Strata 端口，三种 API 的思考按原协议发送。
正文、工具参数、原始模型的 token 计数保持原来的语义。

## 整句异步缓冲

原始生成线程 → 原文缓冲区 → 完整句子队列 → 后台翻译 → 译文句子队列 → 客户端发送。

- 原始模型独立生成，不等待翻译和客户端发送。
- 在句号、问号、感叹号或换行处分段；英文句号等后续空白，避免拆开小数。
- 不按定时器拆成几个词；思考结束时也发送剩余尾段。
- 译完整句、检查异常后才发送。已有积压句子可以批量处理，但不等待凑满。
- 中文及中文夹英文工具名直接保留，不再次英译中。
- 未知符号、重复异常或达到译文长度上限时整句保留原文，不先发送乱码。
- 有限的技术术语修正只在对应英文术语存在时启用，例如键盘场景的“按键”、
  `generation lock` 的“生成锁”、`translation worker` 的“翻译工作线程”。
- Markdown 围栏代码块、缩进代码和可识别的独立代码行直接保留原文。流式输入中的
  未闭合代码块先缓冲，代码内部的换行和句点不作为普通英文分句边界。
- 行内代码、函数调用、文件路径、常见文件名、变量名、热键和十六进制值先替换为
  占位符；仅翻译周围说明，完成术语修正后还原代码，保留大小写、括号和代码格式。
- 还原按编号对应，允许中文语序调整；占位符丢失、重复或出现额外编号时，整句
  返回英文原文，不发送损坏的代码。
  未标记的自然语言与代码边界有时存在歧义，使用反引号或代码块标记最可靠。
  已经发送给客户端的旧译文不会重新生成。

客户端第一句中文需要等待原句结束和这一句翻译完成。正文或工具调用可能等待前面的
译文句子，以维持 API 顺序；这个等待不暂停原始模型生成。
CPU 翻译仍可能与主模型的 CPU 部分竞争处理器和内存带宽。

后续请求回传翻译后的思考时，构造提示词前恢复保存的原始思考。
对应关系保存在 `reasoning-translation.history.sqlite`，保留最近 1000 条，重启后仍可用。
Responses 续接字段也保存原始思考。普通用户内容和最终回答不会被替换。

## 配置与启停

本机的正式启动、校准、测速、构建脚本使用系统 Python 3.13，模型配置中的 CUDA DLL 也来自该系统环境。
翻译子进程通过 `sys.executable` 继承 Strata 使用的同一个 Python，随 Strata 自动启动。
`ctranslate2` 和 `sentencepiece` 已安装在系统 Python 中；启动、更新入口不会创建或调用项目虚拟环境。
项目根目录 `reasoning-translation.json` 控制开关，模型相对路径以该 JSON 所在目录为准。

| 参数 | 本机配置 | 含义 |
| --- | --- | --- |
| `enabled` | `true` | 开关；`false` 返回原文。 |
| `auto_start` | `true` | 自动管理翻译子进程。 |
| `backend` | `t5` | 专用 T5 翻译后端。 |
| `sentence_mode` | `true` | 完整句子缓冲并按整句发送。 |
| `model` | `pack/translation/t5-en-zh-small-ct2` | 模型目录。 |
| `threads` | `8` | 每个翻译工作线程池的 CPU 线程数。 |
| `beam_size` | `1` | 贪心解码；提高到 2～4 会更慢，质量不一定改善。 |
| `nmt_workers` | `2` | 可并发执行的翻译任务数。 |
| `batch_segments` | `8` | 一次处理的已有完整句子上限。 |
| `max_pending_segments` | `32` | 翻译调度队列上限，不限制独立生成线程。 |
| `port` | `8096` | 内部端口，仅监听 `127.0.0.1`；客户端不需要连接。 |
| `timeout_s` | `10` | 内部流读取超时。 |
| `startup_timeout_s` | `30` | 等待翻译服务就绪的上限。 |
| `max_output_tokens` | `256` | 每句输出 token 上限；还按输入长度收紧限制。 |

配置每秒检查一次，已有回复保留开始时的设置，后续请求使用新设置。
模型、线程和端口变更等已有翻译完成后生效。句子模式忽略旧定时切分参数。
缺少 JSON 默认关闭；非法 JSON 保留上一份有效设置并记录错误。

## 选择依据与速度

本次在 Threadripper 3960X 上，用同一组 8 条英中技术句子比较 CPU INT8。
计算汉字数除以墙钟耗时，模型加载与原文积累时间不计入。词表保留英文软件名称。

| 候选配置 | CPU 线程 | 逐句汉字/秒 | 每批 4 句汉字/秒 |
| --- | --- | --- | --- |
| 旧 OPUS/Marian，beam 1 | 4 | 209 | 536 |
| NLLB 600M，beam 1 | 4 | 33 | 87 |
| NLLB 1.3B，beam 1 | 8 | 23 | 56 |
| MADLAD 3B，beam 1 | 8 | 12 | 27 |
| 专用 T5 base，beam 1 | 8 | 116 | 256 |
| 专用 T5 small，beam 1 | 8 | 281 | 635 |
| 选用 T5 small 经本地 HTTP、包含术语修正 | 8 | 227 | 594 |

T5 small 的 HTTP 单句耗时在这组内容中约 104～135 毫秒。
加入代码保护后，在另起的本地 HTTP 服务中使用 8 线程、2 个工作池，对同一组 8 句
逐句测得约 191 汉字/秒、每句约 106～155 毫秒；其中 1 句因占位符检查保留英文，
该句不计入汉字数。另测 9 条含 AHK 代码、路径和变量的输入，代码均原样保留。
这次记录在 `local-upgrade/code-translation-verification.json`。
beam 2 的裸模型逐句约 200 汉字/秒，部分句子的译文并未改善，余量也更小，因此选 beam 1。
T5 base 在批量时达标，逐句不达标；多语言的大模型未满足本机速度目标。

这组内容中，T5 small 比旧 OPUS 少了几处明显错译；它仍有生硬表达，不是 Google 在线翻译
质量的保证，也不是所有开源模型的全面排名。长句、冷启动、并发和主模型竞争 CPU 时
的速度会不同。批量吞吐不能替代单句实时速度。

完整英文、各模型译文、搜索宽度与速度记录在 `local-upgrade/translation-comparison.json`。
对比用的未选中模型、原始转换权重和临时转换环境在结果保留后删除。
TranslateGemma、HY-MT 及翻译专用 llama.cpp 文件与调用路径也已删除。

## 安装与来源

本机已安装，其他 Windows 机器在 Strata 目录执行：

```bat
C:\Users\revar\AppData\Local\Programs\Python\Python313\python.exe tools\setup_nmt_translation.py
```

推理只依赖 `ctranslate2==4.8.2` 和 `sentencepiece==0.2.2`。
首次安装下载固定版本的原始翻译权重，在隔离环境中使用 PyTorch/Transformers 转为 INT8。
转换成功后删除原始下载和转换环境，日常启动不需要 PyTorch/Transformers。
`tools/setup_reasoning_translation.py` 也是该安装器的兼容入口。

模型卡与来源：
<https://huggingface.co/utrobinmv/t5_translate_en_ru_zh_small_1024>，Apache-2.0 许可。
固定版本：`bb215079c5910bee0154f7f6c1350d57502c5d97`。
安装器校验源文件大小和 LFS 文件 SHA-256。

参考的其他模型卡：
<https://huggingface.co/facebook/nllb-200-distilled-600M>、
<https://huggingface.co/google/madlad400-3b-mt>、
<https://huggingface.co/Helsinki-NLP/opus-mt-en-zh>。

启动或翻译失败时回退原文。日志是 `reasoning-translation.log`。
若日志出现 `ModuleNotFoundError: No module named 'ctranslate2'`，说明启动 Strata 的 Python 缺少翻译依赖，
需用同一个 Python 执行上述安装命令。翻译服务仅监听本机 `127.0.0.1:8096`，不能通过局域网地址直接检查。
退出或关闭翻译时结束子进程；Windows 上停止整个翻译进程树，防止 venv 的 Python
启动器结束后实际服务仍占用端口。关闭 Strata 时也使用原有 Job Object 管理进程寿命。
