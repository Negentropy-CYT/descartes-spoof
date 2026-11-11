# Spoof 搜索

搜索经典 Descartes/spoof 对象：C 为奇平方，m 为合数且 m≡1 (mod 4)，
gcd(C,m)=1，σ(C)(m+1)=2Cm，D=Cm 小于指定上界。
支持多进程搜索、自动素数上限、批次级断点续跑和离线多设备分片。

## 启动与恢复

在 PowerShell 中进入本目录：

```powershell
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Workers 12 -Fresh
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Workers 12 -Resume
```

默认深度为 4、每批 16 个子树、检查点间隔 900 秒。上界必须是完整整数。
PrimeCap 默认为 auto；NodeCap 默认为 none，无需手动估计这两个上限。
`-Fresh` 清除当前任务的检查点并从头开始；`-Resume` 从最后有效检查点恢复。
恢复时保持上界、切分深度、批大小和分片范围不变，Workers 可以改变。
首次检查点之前中断需重新开始；检查点不保存正在执行的递归位置，在途批次会重算。
同一检查点目录只允许一个协调进程使用。不同设备各自使用本地目录和同一份程序。

也可通过 `run.cmd` 使用完整命令行参数：

```powershell
.\run.cmd --help
.\run.cmd --bound 1000000000000 --workers 2 --fresh
```

## 输出

默认全部写入一个目录，例如上界 10^35 对应 `log/1e35/`：

```text
log/1e35/
  run.log
  result.json
  A.ckpt
  B.ckpt
```

A/B 检查点随保存次数交替产生。`run.log` 采用逐行 JSON，记录运行和进度。
通过 `-OutputDir 'log/myrun'`（命令行 `--output-dir log/myrun`）指定其他目录；恢复时使用同一目录。
同一上界的独立任务请指定不同目录，避免覆盖结果。
离线分片以编号区分文件，例如负责分片 0–1 时为 `s0-1.log`、`s0-1.json`、`s0-1_A.ckpt`、`s0-1_B.ckpt`。

只有 COMPLETE 且所有 open_* 为零才表示指定范围已完整搜索。
INCOMPLETE、ERROR 或 INTERRUPTED 不构成完整覆盖。日志中的临时 HIT 应以持久化结果为准。
检查点采用 CRC32 检测文件损坏，A/B 文件交替保存；只接受格式和搜索参数匹配的检查点。
结果与恢复参数不包含程序内容摘要，跨设备运行须由使用者保证程序一致。
日志写入失败会警告，计算可继续；不要用日志替代检查点恢复。

## 离线分片

例如固定为 4 个逻辑分片，两台设备分别执行：

```powershell
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Shards 4 -Owned '0:1' -Workers 12 -Fresh
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Shards 4 -Owned '2:3' -Workers 12 -Fresh
```

在界面左侧点击 Merge results，添加各设备的结果 JSON。支持从不同目录分次添加，界面编号从 0 开始；无需导入日志或检查点。检查通过后点击 Merge and save，默认文件名为 `merged.json`。保存前会重新读取并检查输入，且不允许覆盖输入文件。

使用命令行时，收集所有分片的结果 JSON 到单独目录，再执行：

```powershell
.\merge_results.ps1 -InputDir collected_results -Output merged.json
```

所有分片须使用同一程序及相同数学参数。局部分片完成不等于全局完成，必须合并全部且无重叠的分片。
各设备的 Worker 数量和负责份数可以不同；搜索上界、素数上限、节点上限、切分深度、每批子树数量和总份数须相同。合并会检查结果格式、批次数、编号覆盖、完成状态、未决项及 HIT 计数；任一输入不符合要求时拒绝保存。

## 环境与编译

入口使用本目录 `.venv` 中的 CPython 3.11 和 python-flint，Windows x64 下加载
`.runtime/compiled/descartes_search.cp311-win_amd64.pyd`，不回退到纯 Python 内核。

```powershell
.\build.ps1
# 工具链位于其他目录时：
.\build.ps1 -Toolchain 'D:\path\to\toolchain'
```

工具链目录需包含 `build_env/Scripts/python.exe`（Cython、setuptools）、
`w64devkit/bin`、`build/primesieve/source/include` 和 `build/primesieve/build/libprimesieve.a`。
编译产物写入 `.runtime`。正在搜索时不要重新编译或替换运行代码。

HIT 发现后以 `HIT_FOUND` 事件及时记录，不在批次完成时重复输出。`provisional=true` 表示发现通知，不代表已写入检查点。正式结果仍由整批完成后提交；恢复重算可能再次报告同一解。GUI 日志随读取更新，Reported nodes 包含未完成批次中已回报的节点。恢复时从检查点中的节点数重新显示。

## 桌面界面与动态负载均衡

双击 `start_gui.cmd` 打开桌面界面。GUI 每秒读取日志；关闭窗口不会停止独立搜索进程。界面的终止操作会停止搜索进程树，下次从有效检查点恢复。

默认启用动态负载均衡：常规前沿任务派发完后，将尚未执行的 DFS 续段分配给空闲进程。未完成批次的续段不单独持久化，因此中断可能损失超过一个检查点周期的计算。可用 `run.cmd --bound 1000000000000 --workers 2 --no-assist --fresh` 关闭动态负载均衡。

需要便携桌面程序时，先在 `.venv` 安装 PyInstaller，再运行 `build_desktop.ps1`。产物位于 `dist_gui/Spoof/`，须整体复制，不能单独复制 EXE。界面设置保存在 `desktop_settings.json`，运行关联信息保存在对应日志目录；这些文件不承担检查点恢复职责。

应用图标位于 `assets/icon.svg`（矢量原稿）和 `assets/icon.ico`（Windows 多尺寸图标）。桌面构建会将 ICO 嵌入 EXE 并配置窗口图标。`build_desktop.ps1` 可通过 `-Python`、`-OutputDir`、`-WorkDir` 指定构建环境、输出与临时目录。

界面的分片编号从 0 开始：将搜索分成 4 份时，两台设备可分别填写 `0-1` 和 `2-3`。命令行 `--owned` 仍使用从 0 开始的编号，检查点参数不变。新搜索和恢复仅显示本次运行事件；查看历史日志会读取已有记录。清空显示不会删除磁盘日志。

## License

MIT License, Copyright (c) 2026 Chengyuan Tang. See LICENSE. Third-party components and distribution obligations are listed in THIRD_PARTY_NOTICES.md.
