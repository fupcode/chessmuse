# 棋妙 ChessMuse

中国象棋 AI 复盘项目。目标与优先级见 [项目总纲](docs/overview.md)。

当前交付为**第一阶段播放骨架**：8 个主线半回合、2 步分支、5 段缓存中文解说，可播放、暂停、重播、跳转节点和回到主线。示例是原创手写开局教学片段，不是完整对局，也不含专家引擎结论。Pikafish、直觉模型和证据驱动讲解尚未接入。

## 启动

需要 Python 3.12+ 和 uv。在项目根目录运行：

```powershell
uv sync --locked
uv run chessmuse serve
```

打开 <http://127.0.0.1:8000>，点击「播放解说」。Ctrl+C 停止服务。端口占用时使用 `uv run chessmuse serve --port 8001`。

演示音频已随项目缓存；完成依赖安装后，播放不需要网络、LLM、引擎或系统 TTS。首次点击播放后才会发声；语音加载失败会暂停并提示，可重试或关闭「中文语音」继续看字幕。

如果 uv 的默认缓存目录不可访问，可在以上 uv 命令中加入 `--cache-dir .uv-cache`，例如 `uv --cache-dir .uv-cache sync --locked`。

## 验证与修改

```powershell
uv run chessmuse validate
uv run pytest -q
node tests/player.test.mjs
```

Node.js 仅用于播放调度器测试，启动演示不需要 Node 或前端构建工具。棋规测试覆盖错误行棋方、蹩马腿、将帅照面和自陷将军；调度测试覆盖音频完成后推进、暂停剩余时长、重播、跳转与音频失败。

手写棋谱、字幕和播放时间线位于 `src/chessmuse/data/demo.json`。修改讲解后需重新生成音频，缓存清单会比较原文并阻止旧音频与新字幕混播：

```powershell
uv run chessmuse prepare-audio
```

重新生成需要 Windows、Windows PowerShell 和 `Microsoft Huihui Desktop` 中文声音；使用本机 System.Speech，不调用云端服务。已有缓存可以在其他支持 Python 的系统播放。

## 代码与下一阶段

- `src/chessmuse/game.py`：棋规库适配，验证 ICCS 走法、行棋方和自陷将军。
- `src/chessmuse/demo.py`：脚本编译、主线核对、分支快照和音频清单校验。
- `src/chessmuse/static/`：SVG 棋盘、统一调度器和段落字幕。
- `src/chessmuse/cli.py`：本地服务、校验与音频生成入口。
- [播放协议及人工验收](docs/playback.md)：验收流程与当前验证边界。

棋规依赖固定为 [walker8088/cchess](https://github.com/walker8088/cchess) 的 PyPI 发行版 1.25.5（GPL-3.0），该版与仓库最新接口存在差异，已在适配层处理。后续复用或分发时需保留并遵循其许可证。当前仅验证走法级合法性，尚未处理长将、长捉与重复局面的裁决。

本阶段人工验收通过后，按总纲转入 Pikafish 专家分析与 ElephantEye 定向评估，建立真实候选对比；不继续扩展界面装饰。
