# 从零开始使用

从 [Codingnote 仓库](https://github.com/Departure0619/Codingnote) 下载源码 ZIP 并解压，或运行 `git clone https://github.com/Departure0619/Codingnote.git`。以下命令均从包含 `alpha/` 与 `requirements-lock.txt` 的源码根目录运行。

## 1. 准备环境

| 环境 | 要求与验证范围 |
|---|---|
| 操作系统 | 当前安装和浏览器验收来自 Windows 环境；候选说明以 Windows 10/11 为使用目标。没有逐个 Windows 版本矩阵；macOS/Linux 与移动硬件未验收 |
| Python | 使用 64 位 Python 3.12。锁文件及历史新虚拟环境以此验证；没有证明其他 Python 版本可用，代码未提供更宽的版本保证 |
| 浏览器 | 历史验证为 Chromium、1440×900；其他浏览器和尺寸未全面验证 |
| Python 包 | 主安装使用 `requirements-lock.txt`；Web 直接依赖含 FastAPI 0.118.3、Uvicorn 0.49.0、Pillow 12.2.0，锁文件还列出传递依赖 |
| Docker（运行代码时） | Docker CLI、Linux Engine、cgroup v2、默认 seccomp 和本机非远程上下文；Windows 通常使用 Docker Desktop、WSL2 与虚拟化。未声明固定最低 Docker 版本，以预检和本机验收为准 |
| 网络 | 安装包、首次拉取固定摘要基础镜像及真实 AI 调用需要相应网络；本地录入与复习不需要 AI 网络 |

不需要在宿主机安装 GCC、JDK 或 Node，也不需要 npm 构建前端。工具链在执行镜像中。GitHub Pages 无法运行本 Python 服务。

## 2. 安装并启动（Windows PowerShell）

确认终端当前目录包含 `alpha`、`question_bank`、`requirements-lock.txt`，再执行：

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -X utf8 -m question_bank.validate
.\.venv\Scripts\python.exe -X utf8 -m alpha --port 8039 --data-dir data/public-demo --runtime-dir data/public-runtime --no-browser
```

首条命令应显示 Python 3.12。全程显式使用虚拟环境解释器，无需激活脚本。学习数据和执行环境分别放在新目录中；不要指向已有正式学习库或其他候选数据。

打开 [工作台](http://127.0.0.1:8039/)。应能看到首页、初始空题库和 AI 关闭状态。可在另一个 PowerShell 窗口检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8039/api/status
```

核对 `product` 为 `Alpha`、`data_dir` 是本次目录。执行环境尚未就绪不代表网页启动失败。

本指南使用前台入口以便停止。`start_alpha.bat` 与 `python -m alpha.launch` 是另一组启动入口：默认端口 8027、学习目录 `data/alpha`、默认运行目录 `data/alpha-runtime-round35`，会后台启动子进程。不要把它们与本指南的 8039 目录混用；关闭浏览器并不会停止后台服务。

### macOS / Linux：源码对应命令，尚未验收

以下仅是按相同 Python 入口转换的步骤，不代表这些系统已通过安装或执行验收。需要自行满足 Linux Docker Engine 预检要求；macOS Docker Desktop 是否满足全部门禁仍待验证。

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements-lock.txt
./.venv/bin/python -m pip check
./.venv/bin/python -X utf8 -m question_bank.validate
./.venv/bin/python -X utf8 -m alpha --port 8039 --data-dir data/public-demo --runtime-dir data/public-runtime --no-browser
```

后续 Python 命令在这些系统上将 `.\.venv\Scripts\python.exe` 换为 `./.venv/bin/python`；不使用 Windows `.bat`。

## 3. 创建第一道题与导入外部题目

1. 点击“导入题目”，选择“手动录入”。填写自己编写或有权使用的标题、题干、输入输出说明。
2. 填写来源平台、原题号和可选来源链接。自主练习可以留空来源链接，不要编造平台题号。
3. 填写一级知识类型和二级知识标签（逗号分隔），选择语言，可填初始代码。
4. 点击“保存并进入工作区”。记录成为本地学习任务，不等于增加了内置题库资产。

导入已有外部题目时，在“链接待处理”方式中填写来源链接和自己核对后的正文。来源识别覆盖 PTA、牛客、洛谷、LeetCode、Codeforces 的指定域名与部分路径，只给出待确认的平台/题号；不访问网页、不登录平台、不同步提交记录、不自动抓取题干。未知来源可以手工填写。

截图方式提供本地预览与可选本地 OCR。没有 OCR 可手工转录。启用识别会把图片交给本机服务处理，草稿必须核对后应用；不能将识别结果当作原题保证。可选安装见本页末尾。

## 4. 保存代码、运行与复习

1. 在工作区修改题目/代码并保存；`Ctrl+S` 可保存工作区。代码发生变化会保留旧版本，刷新后核对版本记录。
2. 运行前先保存。输入标准输入，可填期望输出；查看编译状态、标准输出、标准错误、是否执行及限制状态。单个样例输出一致不能证明整题通过。
3. 没有本机执行环境时，仍可填写外部判题结果；它是用户录入记录，不是工作台重新判题的证明。
4. 在“错误原因记录”选择出错版本和可选执行证据，填写实际表现、原因与确认状态，保存。原因可以修订并保留历史。
5. 在错题笔记中选择原始版本、可选修复版本，写总结与标签，标记“需要复习”后保存。单独保存原因不会代替这一步。
6. 打开“待复习”或错题库，回到题目独立重做，再记录“已理解 / 仍不确定 / 需要重做”和笔记。“已理解”会清除待复习标记，其他两种结果保留。

设置里的复习频率为轻量 7 天、标准 3 天、密集 1 天，用于本地建议筛选，不是系统定时推送。全局和本题学习偏好可影响建议；关闭主动建议不会删除错题。

跨题关联需先有两条错误记录。在“手工提出关联”选择本题错误，填写另一题错误记录 ID 和依据；在关联详情比较双方快照并确认、拒绝、忽略或撤销。原因/版本变化后关联可能失效，需要重新核对。

## 5. 启用 Docker 执行

先用 `Ctrl+C` 停止上述服务，避免运行目录锁冲突。启动 Docker 的 Linux Engine，然后在相同应用目录执行：

```powershell
docker version
.\.venv\Scripts\python.exe -X utf8 -m alpha.runtime_setup check --data-dir data/public-runtime
.\.venv\Scripts\python.exe -X utf8 -m alpha.runtime_setup build --data-dir data/public-runtime
.\.venv\Scripts\python.exe -X utf8 -m alpha.runtime_setup accept --data-dir data/public-runtime --evidence output/public-runtime-acceptance.json
```

首次 `check` 返回未就绪（退出码 2）是预期；按顺序完成构建、五语言验收，再用第 2 节同一启动命令启动。`check` 就绪及状态中的 `execution.ready` 可用于核对。不要使用旧 `execute_setup` 代替当前五语言 `runtime_setup`。

构建按固定摘要检查/拉取 GCC、Python、Temurin、Node 基础镜像；验收会实际运行容器。保留 `output/public-runtime-acceptance.json`，运行门禁会核对它及环境指纹。新机器、镜像或策略变化须重新验收，不复制旧收据开通运行。

## 6. 停止、完整备份与新目录恢复

在前台服务终端按 `Ctrl+C`，等待进程退出。浏览器关闭不等于停止。备份目标文件必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m alpha.migrations backup --database data/public-demo/learning.sqlite3 --output backups/public-snapshot.sqlite3
.\.venv\Scripts\python.exe -X utf8 -m alpha.migrations rollback --backup backups/public-snapshot.sqlite3 --restore-to data/restored-demo/learning.sqlite3
.\.venv\Scripts\python.exe -X utf8 -m alpha --port 8039 --data-dir data/restored-demo --runtime-dir data/public-runtime --no-browser
```

同时保管 `public-snapshot.sqlite3` 和同名 `public-snapshot.json` 摘要清单。目标数据库必须不存在，不会覆盖或合并已有库；恢复后核对题目、版本、运行证据、原因和复习记录。`rollback` 是当前恢复子命令名称。

这是完整学习数据库快照，不是整个数据目录归档：`ai_raw/` 原始响应文件、独立累计授权账本、执行镜像和验收文件不在此 SQLite 快照中。网页“导出学习记录（脱敏）”不能用于完整恢复。新目录恢复不授予新 AI 额度；新机器执行另需验收。备份内容可能含私人代码和学习历史，请私下保存。

## 7. 排查问题

| 现象 | 检查方式 |
|---|---|
| 找不到 Python 或版本不符 | 用 `python --version` 确认 3.12；用正确解释器重建本次虚拟环境，不修改系统环境来迁就旧项目 |
| 安装失败/依赖冲突 | 确认在新虚拟环境中按锁文件安装，检查网络及 `pip check`；不要混入 OCR 专用包 |
| 无法导入 alpha | 当前目录应是候选应用根目录，并使用本次虚拟环境；不是工程旧根目录或文档目录 |
| 端口占用 | 停止自己启动的服务，或将启动参数改为未占用端口并同步浏览器地址；不结束不明进程 |
| 学习/运行目录被占用 | 同一目录只允许单进程使用；先退出该服务再维护，不靠删除锁文件规避 |
| Docker 不可用 | 检查 CLI 与 Engine 均启动、Linux 容器模式、WSL2/虚拟化、cgroup v2、默认 seccomp；按 `check` 的真实原因处理 |
| 镜像拉取失败 | 检查网络、镜像仓库访问与固定摘要可用性，再重新构建；不任意更换镜像来绕过核对 |
| 构建成功仍不能运行 | 对同一个 `--runtime-dir` 完成 `accept`；保留验收证据路径，不搬用旧环境文件 |
| AI 保存失败或无法调用 | 见 [DeepSeek 指南](DEEPSEEK_API_SETUP.md)，不要反复点击制造潜在收费请求 |

可选 OCR 需要独立核实包/模型许可和系统支持；本候选未验收安装与识别。源码预期安装在独立目录：

```powershell
.\.venv\Scripts\python.exe -m pip install --target .local/ocr --only-binary=:all: -r requirements-ocr.txt
```

不要将该依赖文件合并安装到主环境（Pillow 版本不同）。重启后检查导入页可用性；不可用时继续手工录题。
