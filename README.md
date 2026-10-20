# 棋妙 ChessMuse

中国象棋 AI 复盘项目。目标与优先级见 [项目总纲](docs/overview.md)。

当前交付为**第一阶段播放骨架**：8 个主线半回合、2 步分支，每次走子配一段 LLM 生成的自然短句语音，另有 5 段手写解释，可播放、暂停、重播、跳转节点和回到主线。示例是原创开局教学片段，不是完整对局，也不含专家引擎结论。Pikafish、直觉模型和完整证据驱动长讲解尚未接入。

## 启动

需要 Python 3.12+ 和 uv。在项目根目录运行：

```powershell
uv sync --locked
uv run chessmuse serve
```

打开 <http://127.0.0.1:8000>，点击「播放解说」。Ctrl+C 停止服务。端口占用时使用 `uv run chessmuse serve --port 8001`。

演示采用**晓晓神经网络女声**，默认语速为 120%（`--rate=+20%`），音频已随项目缓存；完成依赖安装后，播放不需要网络、LLM、引擎或系统 TTS。首次点击播放后才会发声；语音加载失败会暂停并提示，可重试或关闭「中文语音」继续看字幕。

工具缓存统一放在项目根目录的 `.cache/`，由配置自动生效：uv 使用 `.cache/uv/`，pytest 使用 `.cache/pytest_cache/`，测试临时文件使用 `.cache/pytest-tmp/`。直接运行上述命令即可，不需要额外传入缓存路径。整个 `.cache/` 不提交 Git；测试临时目录会在下一次测试开始时重建。

## 验证与修改

```powershell
uv run chessmuse validate
uv run pytest -q
node tests/player.test.mjs
node tests/motion.test.mjs
```

Node.js 仅用于播放调度器及动画控制测试，启动演示不需要 Node 或前端构建工具。棋规测试覆盖错误行棋方、蹩马腿、将帅照面和自陷将军；调度测试覆盖报步与走子同时开始、等待语音结束、暂停、缓冲、重播、跳转与音频失败。

每次走棋由 LLM 把行棋方、正式走法、已核验的将军/吃子/开局标签和近期走子上下文压缩成自然短句，例如「红方当头炮」「黑方跳马」「黑方屏风马」。保留行棋方，不加逗号或其他标点，避免照读几进几。音频实际开始时启动走子，动画最长 550ms，并控制在短句语音时长的 60% 以内；短句说完后，有独立解释段落就继续讲，没有就直接进入下一步。暂停会同时暂停音频与棋子动画，跳转会撤销旧动画并定位到所选走子的起始局面。系统启用减少动态效果时会直接落子，语音节奏保持一致。

棋谱、手写长解释和播放时间线位于 `src/chessmuse/data/demo.json`。修改棋步或上下文后，先生成 LLM 短句，再生成音频：

```powershell
uv run chessmuse prepare-narration
uv run chessmuse prepare-audio
```

默认通过 [edge-tts](https://github.com/rany2/edge-tts) 调用微软在线 TTS，将讲解文本发送到服务端，生成晓晓女声 MP3。生成需要网络，无需配置 API Key；缓存后的播放仍完全离线。使用音频实际时长保持段落同步。生成失败会保留当前正在使用的缓存和清单。

`prepare-narration` 使用 `.env` 的 `LLM_*` 配置，调用 `deepseek-flash` 并保存 `src/chessmuse/data/narration.json`。缓存记录完整事实输入、上下文、模型、提示词版本、结果及用量，作为演示交付物提交 Git；临时文件放在 `.cache/narration/`。播放时复用缓存，不调用 LLM。事实或上下文变化会要求重新生成，缺失缓存时不会退回机械报步。仅修改手写长解释时直接重新生成音频即可。

短句输出会检查行棋方、标点、长度、棋子、行动方向以及将军/吃子/已知标签等基本约束；不合格时最多要求 LLM 修正一次，仍失败则保留旧缓存并报错。分支结束后恢复主线历史。上述约束不等同于完整自然语言事实验证，复杂威胁与杀法仍需后续分析内核提供证据，不能仅凭文字示例推断。

可以调整音色与语速，例如：

```powershell
uv run chessmuse prepare-audio --voice zh-CN-XiaoxiaoNeural --rate=+20%
```

需要完全离线生成时，可使用 `uv run chessmuse prepare-audio --provider windows`，回退到 Windows 自带的慧慧声音（需要 Windows PowerShell 和 `Microsoft Huihui Desktop`）。修改缓存后重启服务并刷新页面，新音频使用独立 URL，避免浏览器沿用旧声音。

## 代码与下一阶段

### DeepSeek 调用层

从项目根目录运行。首次配置时将 `.env.example` 复制为 `.env`，填写 `LLM_API_KEY`；已有 `.env` 时直接修改配置，无需覆盖。默认模型为 `deepseek-flash`，接口地址为 `https://api.deepseek.com`。

```powershell
uv run chessmuse llm-check
# 也可以显式指定环境文件
uv run chessmuse llm-check --env-file .env
```

`llm-check` 会发送一条真实的短请求并显示模型和 token 用量，需要网络及可用余额。普通 `pytest` 测试使用模拟接口，不调用收费 API。`.env` 及本地环境变体不提交 Git，`.env.example` 仅包含空密钥和默认参数。进程环境变量优先于 `.env`，配置在调用 `from_env()` 时读取，不会在模块导入时加载，也不会修改全局环境。

Python 调用示例：

```python
import asyncio
from chessmuse.llm import DeepSeekClient, DeepSeekSettings, Message

async def main():
    async with DeepSeekClient(DeepSeekSettings.from_env()) as client:
        result = await client.complete([
            Message("system", "只描述提供的已验证事实，缺少搜索证据时不要判断走法优劣。"),
            Message("user", "已验证事实：标准初始局面，红炮从 h2 移至 e2。请返回 JSON 对象，text 字段描述这个事实。"),
        ], json_mode=True)
        print(result.data)
        print(result.usage.total_tokens)

asyncio.run(main())
```

`complete()` 默认返回文本；开启 `json_mode=True` 后会补充 JSON 指令并校验返回值为 JSON 对象，通过 `result.data` 读取。返回结果同时包含原始文本、服务返回的模型名称、请求 ID 及输入/输出/总 token 用量。JSON 模式只验证格式，后续仍需由讲解模块校验证据引用与具体业务字段。

可通过 `.env.example` 中的配置调整模型、思考模式、输出 token 上限、单次请求总超时及重试次数。默认关闭思考模式、输出上限 2048 tokens、每次请求超时 60 秒，网络错误、HTTP 429 和 5xx 最多额外重试两次；其他 HTTP 错误直接返回。支持有界的 `Retry-After` 等待，不重试无效响应或被截断的内容。认证失败、余额不足、空结果和截断等会转成 `ConfigurationError` / `LLMError`，错误信息不包含密钥或服务端原始响应体。

此层目前提供非流式文本和 JSON 调用，不缓存请求或回复，不会自动修改现有演示脚本，也不直接控制棋步。协议依据 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 实现，并已用 `deepseek-flash` 完成真实连通性验证。

### 模块说明

- `src/chessmuse/game.py`：棋规库适配，验证 ICCS 走法、行棋方和自陷将军。
- `src/chessmuse/demo.py`：脚本编译、主线核对、分支快照和音频清单校验。
- `src/chessmuse/static/`：SVG 棋盘、统一调度器和段落字幕。
- `src/chessmuse/cli.py`：本地服务、校验与音频生成入口。
- `src/chessmuse/llm/`：DeepSeek 配置读取、异步调用、响应解析与错误处理。
- `src/chessmuse/narration.py`：带上下文的 LLM 短句生成、校验与缓存。
- [播放协议及人工验收](docs/playback.md)：验收流程与当前验证边界。

棋规依赖固定为 [walker8088/cchess](https://github.com/walker8088/cchess) 的 PyPI 发行版 1.25.5（GPL-3.0），该版与仓库最新接口存在差异，已在适配层处理。后续复用或分发时需保留并遵循其许可证。当前仅验证走法级合法性，尚未处理长将、长捉与重复局面的裁决。

本阶段人工验收通过后，按总纲转入 Pikafish 专家分析与 ElephantEye 定向评估，建立真实候选对比；不继续扩展界面装饰。
