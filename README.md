# 棋妙 ChessMuse

中国象棋 AI 复盘项目，目标与优先级见 [项目总纲](docs/overview.md)。现已提供《橘中秘·弃马十三着》的完整讲解演示：完整主线、自然攻防讲解、关键变化推演、晓晓中文语音和同步字幕。其他导入棋谱目前支持合法回放与走子短句。

## 启动

需要 Python 3.12+ 和 uv，从项目根目录运行：

```powershell
uv sync --locked
uv run chessmuse serve
```

打开 <http://127.0.0.1:8000>。默认准备「橘中秘 · 弃马十三着」，点击「播放解说」可听完整演示；也可选择开局示例或导入自己的 JSON 棋谱。服务启动不要求已存在音频；选择棋谱后会显示校验与准备进度。古谱讲解、古谱走子短句和「中炮对屏风马」短句随代码交付，播放无需在线 LLM；默认 TTS 首次生成需要联网，已有有效缓存时直接复用。

本工作区古谱音频已准备好。新工作区可先执行 `uv run chessmuse prepare-example --example qima-13`。完整来源、三处重点、模型实验和重做流程见 [著名古谱讲解交付](docs/famous-review.md)。

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
- `.cache/review/`、`.cache/analysis/`：古谱证据和专家搜索；`.cache/elephanteye/`：白名单源码、直觉模型构建及实验缓存。
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

# 重新生成短句后，再准备与新文本匹配的音频
uv run chessmuse prepare-narration --example opening-v1
uv run chessmuse prepare-audio --example opening-v1

# 只强制重新合成音频，保留已有短句
uv run chessmuse prepare-audio --example opening-v1 --force

# 命令行试用其他语速（网页仍使用默认配置，见下文）
uv run chessmuse prepare-audio --rate=+12% --force
```

默认使用晓晓女声 `zh-CN-XiaoxiaoNeural`，语速 108%。音频通过 [edge-tts](https://github.com/rany2/edge-tts) 联网生成。已有有效短句缓存时，可执行 `prepare-audio --provider windows` 离线合成慧慧 WAV，需安装对应系统声音；此后端使用脚本固定语速，不应用 `--voice`、`--rate`，每次执行均重新合成。

网页每次准备棋谱都按默认晓晓、108% 检查缓存。因此命令行使用其他音色、语速或 Windows 合成后，再在网页选择该棋谱，会重新生成默认配置的音频。当前尚无网页音色/语速设置；若要持久切换，需要先改默认配置并验收。

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
node tests/review-playback.test.mjs
uv run chessmuse validate
```

自动化测试使用模拟 LLM 与生成的微型 WAV，不需要真实 API 密钥或本地音频，不调用收费服务。`validate` 验证实际默认示例缓存，需要先准备音频。Node.js 仅用于前端模块测试，启动不需要前端构建工具。

Python 源码采用 `src/chessmuse/` 布局：`game.py` 负责棋规边界，`demo.py` 编译播放快照，`library.py` 管理棋谱与缓存，`server.py` 提供异步导入任务和进度，`static/` 为播放器页面。

## 专家分析（命令行）

先从 [Pikafish 官方发行版](https://github.com/official-pikafish/Pikafish/releases/tag/Pikafish-2026-09-06)解压引擎和 `pikafish.nnue` 到 `.cache/pikafish/`。本机本次开发已准备好 Windows 版本：

```powershell
uv run chessmuse analyze-game --engine .cache/pikafish/Pikafish-Windows-x86-64-universal.exe --review-ply 5
uv run chessmuse analyze-suite --engine .cache/pikafish/Pikafish-Windows-x86-64-universal.exe
```

默认示例全局 depth 6，最多 3 处 depth 10 复查，每步实战走法独立搜索；`analyze-position` 支持任意合法 FEN 和多个指定候选。原始 UCI、评分、合法 PV 和逐步吃子/将军证据保存在 `.cache/analysis/`。这不会调用 LLM 或生成语音。详情、评分限制与真实实验见 [专家分析阶段交付](docs/expert-analysis.md)。引擎源码 GPLv3，发行版权重另有限制商用的许可，当前不打包分发。

## 当前交接状态（2026-10-20）

已实现一份著名古谱的完整讲解：25个主线半回合、3个关键节点、4段推演、77个事件及69段真实语音。ElephantEye 的纯子力/传统评价配置完成1/3/5层对照，Pikafish深度12/16复查关键候选；详细战术采用已核验事实卡，由LLM组织段落。修复了多分支「回到主线」定位问题，完整音频缓存可直接复用。

下一上下文先读 [项目总纲](docs/overview.md)、本 README 和 [著名古谱讲解交付](docs/famous-review.md)。真实HTTP加载、全部音频访问、完整Player事件推进已验证；桌面操作因网址识别限制停止，画面、实际听音和主观讲解质量仍待人工验收。当前三个古谱节点由开发时指定，尚未覆盖任意导入棋谱的自动完整解说；下一步优先按真实观感改进本演示，再推广节点选择和名局样本。

服务需在新会话中确认或重新启动，不依赖上一个会话的后台进程。导入任务和进度只保存在服务内存中；重启后需重新选择或导入，磁盘中有效的棋谱、短句和音频仍可复用。旧音频文件暂不自动清理，HTTP 仅提供当前清单引用的文件。

棋规依赖 [walker8088/cchess](https://github.com/walker8088/cchess) 的 PyPI 1.25.5 发行版（GPL-3.0），分发时需遵循其许可证。当前处理走法级合法性，尚未实现长将、长捉和重复局面裁决。
