# 专家分析阶段交付（2026-10-20）

本次推进边界为总纲步骤 2：以真实 Pikafish 为指定走法产出可核验的评分和变化，完成全局浅扫、少量深度复查及缓存。直觉模型、战术分类和证据驱动长解说进入后续阶段。

## 前置状态与验收结论

现有播放器的主线、分支返回、音频缓存验证通过，13 项前端模块测试通过。当前会话的浏览器/桌面工具返回空连接清单，无法进行浏览器画面和听音验收；**步骤 1 的主观体验验收仍待完成**，没有以模块测试代替这项门槛。本次利用独立模块开发空间推进专家分析，未修改播放行为，也未宣称端到端 MVP 已通过。

步骤 2 的本次验收结果：

| 条件 | 交付与结果 |
| --- | --- |
| 合法棋谱和局面快照 | 默认 8 半回合棋谱先整体校验；保存初始至结束共 9 个局面 |
| 真正的专家搜索 | Pikafish 2026-09-06 Windows universal，随发行版的 NNUE，单线程、Hash 32 MB |
| 指定候选单独分析 | 实战每步用根局面 `searchmoves` 搜索，不依赖 MultiPV 收录 |
| 浅扫与复查 | 全部走前局面与最终局面 depth 6；3 个局面 depth 10，复查提供 MultiPV 3 及实战定向搜索 |
| 变化验证 | 每条 PV 逐手经 `game.play` 校验，附中文走法、将军、吃子与下一局面 |
| 可复现证据 | 原始收发 UCI、版本、文件元数据、选项、历史、深度均记录；真实重复冷搜索的丢车样本最佳走法与评分一致 |
| 基础局面集 | 10 个真实运行样本；7 个开局阶段、丢车反例、马将军、炮吃马后被车回吃 |

上述是专家分析基础验收，不代表覆盖了总纲步骤 4 的弃子攻杀、弃子争先或认知落差。当前样本没有经过专家人工棋理鉴定，也不是完整的 10～20 个战术质量评测集；后续应补真实实战来源与高手赏析样本。

本次最终通过 90 项 Python 测试（含真实引擎集成）及 13 项前端模块测试，默认 `validate` 通过。第二次执行棋谱分析和实验集时，合计 43 次搜索全部命中缓存并重新核验变化。首次 Python 基线运行因旧测试临时目录的沙箱访问权限失败；在批准的执行环境中使用相同项目配置重跑后通过，未改测试缓存路径。

## 定向调研与实现选择

- [Pikafish 官方 UCI 文档](https://github.com/official-pikafish/Pikafish/wiki/UCI-%26-Commands)确认使用 UCI、MultiPV、`position fen … moves …` 和 `ucinewgame`。本阶段直接用标准库子进程实现通信，复用既有 cchess 规则边界，未增加依赖。
- [固定版本源码 uci.cpp](https://github.com/official-pikafish/Pikafish/blob/Pikafish-2026-09-06/src/uci.cpp)中 `parse_limits` 读取 `searchmoves` 直到行尾，因此它必须放在 `go depth N` 之后。适配器发送完整初始 FEN 和已验证历史，保留引擎识别重复局面所需的上下文。
- 每次搜索先 `ucinewgame` 清空状态；固定单线程、搜索深度与 Hash 32 MB，减少搜索顺序带来的波动。这里的 Hash 是引擎置换表内存参数，不进行文件哈希计算。
- Windows 管道使用读线程加队列实现等待上限。启动失败、异常退出、未完成深度、非法 PV、候选限制不符均报错，关闭时回收子进程。
- [官方发行版](https://github.com/official-pikafish/Pikafish/releases/tag/Pikafish-2026-09-06)源码为 GPLv3；随附 `NNUE-License.md` 另行限制权重未经许可不得商用。本地研究使用，不将二进制或权重提交仓库；产品商用前必须另行解决权重授权或替换方案。

## 复现方式

从官方固定版本发布页下载 `Pikafish.2026-09-06.7z`，解压到 `.cache/pikafish/`，使 Windows exe 和 `pikafish.nnue` 位于同一目录。本次已在当前工作区准备好。其他平台使用发布包中对应的程序，并调整 `--engine`。

```powershell
# 默认示例：每步浅扫，最多复查3处；指定第5半回合进入复查
uv run chessmuse analyze-game --engine .cache/pikafish/Pikafish-Windows-x86-64-universal.exe --review-ply 5

# 10个样本的真实专家搜索与指定候选对照
uv run chessmuse analyze-suite --engine .cache/pikafish/Pikafish-Windows-x86-64-universal.exe

# 人工构造丢车反例，单独搜索诱人走法
uv run chessmuse analyze-position --engine .cache/pikafish/Pikafish-Windows-x86-64-universal.exe --fen "r3k4/9/9/9/4p4/9/9/9/9/R3K4 w" --candidate a0a8 --depth 10
```

`analyze-position --candidate` 可以重复，每个候选分别搜索。`--moves` 可提供目标局面之前的走法历史。`analyze-game --input 文件.json` 支持导入格式 `{"title":"…","moves":[…]}`，或带 `initial_fen`、`mainline` 的内置脚本。全部实际走法先验证，非法棋谱不进行搜索。

通用参数：`--eval-file` 显式指定权重；`--timeout` 设置每次协议等待的秒数上限（默认 60）；`--force` 重新计算；`--output` 指定 JSON 报告。棋谱命令还有 `--scan-depth`、`--review-depth`、`--max-reviews` 和可重复的 `--review-ply`（从 1 开始）。当前不连接网页，也不触发 LLM/TTS。

## 证据格式与缓存

默认报告位于 `.cache/analysis/analyze-game.json`、`analyze-position.json`、`analyze-suite.json`；每个独立搜索缓存在 `.cache/analysis/searches/<随机ID>.json`。可提交的小样本摘要见 [实际实验记录](experiments/expert-2026-10-20.json)，保留最终原始 info、bestmove、发送命令及 PV。完整双向通信和逐步事件留在本地缓存中。

每条搜索记录包含：引擎版本与启动握手、可执行文件/权重路径及大小/修改时间、选项、初始 FEN、全部历史走法、depth、multipv、searchmoves、目标局面、评分和合法 PV 事件。缓存按上述身份与请求字段匹配；不计算文件哈希。命中后重新解析原始输出并核验 PV；损坏记录跳过并重算。修改程序或权重会由元数据变化使旧缓存失配，这不是密码学完整性保证。

评分必须保留以下含义：

- 每个搜索的分数均从**该根局面的行棋方**看；黑方走前的正分有利于黑方。跨步曲线需另行统一视角。
- `score.kind` 区分 `cp` 与 `mate`，`bound` 区分 exact、lower、upper。只有同局面、同视角、同请求深度的 exact cp 估计才能相减；其他情况 `loss_cp=null`，不把杀棋折算成任意大分。
- `loss_cp` 为独立搜索估计差，未经棋力或跨局面校准。即使首选相同，也可能有小幅非零或负差，按原样保留；不能据此直接判失误。MultiPV 与单候选的搜索预算不同。
- PV 是主变化证据，不表示唯一应法、强制变化或已经验证所有防守。当前仅核验走法级合法性，不在 Python 内裁定长将长捉。
- 复查选择暂按较大 cp 差排序，mate/边界评分优先复查，允许指定局面；仅为步骤 2 的扫描调度，不是步骤 4 的综合关键节点检测器。

## 实验观察与下一边界

depth 8 的 10 个样本中，丢车反例最佳 `a0a9`，指定 `a0a8` 后 PV 明确给出 `a9a8` 回吃，评分估计差 1759 cp。初始局面炮吃马 `h2h9` 后有 `i9h9` 回吃，估计差 162 cp，不能据“吃到马”或“送炮”称妙手。马将军样本 `d6f7` 的将军事件通过规则验证，而最佳变化输出 mate 分数，因此没有计算 cp 差。

默认教学主线的第 5、6 半回合在 depth 10 复查中分别出现 151、188 cp 的估计差。它们可作为后续直觉模型候选对照的起点；当前仅有专家证据，不能称“已证明普通人会误判”。depth 8 的部分开局样本出现 -2、-1 cp 差，体现有限搜索波动。

下一阶段按步骤 3 限时调研 ElephantEye 的构建、传统评价、候选排名与许可证，先交付两种可运行评价配置及浅层搜索对照；只有遇到明确阻塞，才采用手工评价后备方案。进入完整演示接线前仍须补播放体验验收。
