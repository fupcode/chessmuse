## Read first
- docs/overview.md.

## 技术要求
- 使用 uv 管理 Python 版本、虚拟环境和依赖。
- 包名为 chessmuse，使用 src 布局，源代码统一放在 src/chessmuse/。

## 工具缓存
- 工具缓存和测试临时文件统一放在项目根目录的 `.cache/` 下，按工具分子目录；不要在根目录散落 `.uv-cache/`、`.pytest_cache/` 或测试临时目录。
- uv 使用 `uv.toml` 配置的 `.cache/uv/`；pytest 缓存使用 `.cache/pytest_cache/`，测试临时文件使用 `.cache/pytest-tmp/`。
- 从项目根目录执行工具命令，优先使用已提交的项目配置，直接运行 `uv`、`uv run pytest`；不要用命令行参数或环境变量覆盖为项目外的缓存路径。新增工具时优先配置其缓存目录。
- `.gitignore` 统一忽略根目录 `.cache/`，无需为其中各工具缓存重复添加规则。语音及其清单统一放在根目录 `audio/`，不纳入 Git；示例已有有效缓存时直接复用，开发时可用命令行强制重新生成。

## 代码管理
- 得到用户相关指令后，使用git commit，消息尽量简短，遵循 Conventional Commits 规范，以 feat、fix、refactor 等前缀开头，主体内容使用中文。

## 项目节奏
- 因为本项目的目标和人的主观感受高度相关，在开发过程中应阶段性交付代码或交付调研报告和技术选型，避免过度依赖自动化测试作为进度指标。
