# 棋妙 ChessMuse

中国象棋 AI 复盘项目，目标与优先级见 [项目总纲](docs/overview.md)。目前支持网页选择示例、导入棋谱、LLM 自然走子短句、中文语音与同步棋盘动画。专家引擎及完整复盘分析尚未接入，页面不提供引擎优劣判断。

## 启动

需要 Python 3.12+ 和 uv，从项目根目录运行：

```powershell
uv sync --locked
uv run chessmuse serve
```

打开 <http://127.0.0.1:8000>。可选择「中炮对屏风马」和「双方起马」示例，或导入自己的 JSON 棋谱。服务启动不要求已存在音频；选择棋谱后，页面显示校验、短句生成、语音准备和完成的真实进度。首次生成需要网络及 `.env` 中可用的 LLM 配置，已有有效缓存时直接复用。

## 导入棋谱

网页提供「下载示例 JSON」。当前格式如下，标准初始局面，走法为 ICCS 坐标，最多 200 个半回合、文件最大 256KB：

```json
{
  "title": "我的棋谱",
  "moves": ["h2e2", "h9g7", "h0g2", "b9c7"]
}
```

导入后先校验全部走法，再生成带上下文的自然短句和音频，完成后可播放。非法走法会直接报错，不启动生成；处理中可看到真实进度，失败后可重试。再次导入相同标题和走法会复用本地棋谱缓存。格式和接口见 [棋谱导入说明](docs/importing.md)。

## 本地文件与缓存

- `audio/<棋谱ID>/`：实际 MP3/WAV 及 `audio.json` 清单，位于项目根目录，**不提交 Git，也不打入 Python 包**。
- `.cache/games/<棋谱ID>/`：导入棋谱与本地生成的 LLM 短句。
- `.cache/tts/`、`.cache/narration/`：生成中的临时文件。
- `.cache/uv/`、`.cache/pytest_cache/`、`.cache/pytest-tmp/`：工具缓存与测试临时文件。
- `src/chessmuse/data/`：内置示例及一份可复用的文本短句记录，随代码交付。

每次选择示例，会按文本、音色、语速和文件有效性检查缓存。匹配的语音不会再次调用 TTS；只生成缺失或失效的片段。短句输入及上下文未变化时也不会调用 LLM。生成失败保留原有清单；新文件全部成功后才发布新清单。已有缓存时播放可离线完成。

## 开发时重新生成

```powershell
# 准备完整示例：有缓存则直接复用
uv run chessmuse prepare-example
uv run chessmuse prepare-example --example horse-opening

# 强制重新生成该示例的 LLM 短句和全部音频
uv run chessmuse prepare-example --example opening-v1 --force

# 只重新生成短句或语音
uv run chessmuse prepare-narration --example opening-v1
uv run chessmuse prepare-audio --example opening-v1 --force

# 自定义音色或语速
uv run chessmuse prepare-audio --rate=+8% --force
```

默认使用晓晓女声 `zh-CN-XiaoxiaoNeural`，语速 108%。音频通过 [edge-tts](https://github.com/rany2/edge-tts) 联网生成。需要完全离线合成时，可执行 `prepare-audio --provider windows`，使用 Windows 自带慧慧语音；需安装对应系统声音。生成后重新选择该示例或刷新页面即可使用新清单。

象棋中的「车」统一按 jū（一声）合成：仅在送给 TTS 的文本中使用同音字「居」，字幕和 LLM 原文仍保留「车」。音频清单分别记录显示原文与合成文本，发音规则变化时只重新生成受影响的片段。

## LLM 配置与播放

将 `.env.example` 复制为 `.env`，填写 `LLM_API_KEY`。默认 `LLM_MODEL=deepseek-flash`、`LLM_BASE_URL=https://api.deepseek.com`。真实 `.env` 不纳入 Git，进程环境变量优先于环境文件。连通性检查会发送一条短请求：

```powershell
uv run chessmuse llm-check
```

`chessmuse.llm` 提供异步文本/JSON 调用、超时、有限重试、用量记录和脱敏错误。`chessmuse.narration` 按每批最多 16 步，把行棋方、正式走法、已核验的将军/吃子/开局标签及近期上下文压缩成短句，例如「红方当头炮」「黑方屏风马」。保留行棋方，不加标点，不机械朗读几进几。输出须通过基本约束校验，失败时要求修正一次；复杂威胁和杀法仍需后续分析内核提供证据。

每步短句实际开始播放后，棋子同步移动，动画最长 550ms 且不超过语音时长的 60%。短句结束后可接详细解释，也可直接下一步。暂停、继续、缓冲和跳转统一控制语音与动画；跳转定位到该步起始局面。详见 [播放协议](docs/playback.md)。

## 验证

```powershell
uv run pytest -q
node tests/player.test.mjs
node tests/motion.test.mjs
node tests/import.test.mjs
uv run chessmuse validate
```

自动化测试使用模拟 LLM 与生成的微型 WAV，不需要真实 API 密钥或本地音频，不调用收费服务。`validate` 验证实际默认示例缓存，需要先准备音频。Node.js 仅用于前端模块测试，启动不需要前端构建工具。

Python 源码采用 `src/chessmuse/` 布局：`game.py` 负责棋规边界，`demo.py` 编译播放快照，`library.py` 管理棋谱与缓存，`server.py` 提供异步导入任务和进度，`static/` 为播放器页面。

棋规依赖 [walker8088/cchess](https://github.com/walker8088/cchess) 的 PyPI 1.25.5 发行版（GPL-3.0），分发时需遵循其许可证。当前处理走法级合法性，尚未实现长将、长捉和重复局面裁决。
