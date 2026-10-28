# 代码结构与测试

这是一个本机 Python Web 应用：浏览器静态页面调用 FastAPI，学习记录写入 SQLite；运行代码时由后端调用 Docker。没有微服务、云数据库或账号同步架构。下面的路径均相对应用根目录。

## 模块职责

| 路径 | 职责 |
|---|---|
| `alpha/__main__.py`、`locking.py` | CLI 参数、学习/运行目录独占锁、回环地址、单 worker 启动 |
| `alpha/launch.py`、`start_alpha.bat` | 便捷启动器，查就绪状态并启动后台子进程；与前台 CLI 默认路径要区分 |
| `alpha/app.py`、`models.py` | HTTP 接口、请求校验、本机来源边界、执行与聊天请求协调、取消/请求标识 |
| `alpha/static/` | HTML/CSS/JavaScript 页面；`integration.js`、`round67.js` 等补充来源、原因、关联、辅导和设置流程 |
| `alpha/importing.py`、`ocr_worker.py` | 来源候选识别、图片校验和可选本地 OCR 子进程；不抓取第三方网页 |
| `question_bank/validate.py`、`alpha/bank.py` | 空/非空题库合同校验及练习入口；公开候选 `bank.json` 初始为空 |
| `alpha/store.py`、`integration_store.py`、`integration_api.py` | 题目、版本、执行证据、错题、原因修订、关联、偏好等存储与接口 |
| `alpha/runner.py`、`phase_runtime.py`、`languages.py`、`native_image/` | 当前五语言执行代理、编译/运行阶段、语言注册和镜像内监督程序 |
| `web_lab/sandbox_runtime.py` 等 | 共用 Docker 预检、隔离约束、清理与受限传输；当前入口不是旧实验页面 |
| `alpha/runtime_setup.py` | 当前五语言镜像构建、检查和验收入口 |
| `alpha/tutor.py`、`teaching_protocol.py`、`teaching_records.py` | 官方 AI 请求、上下文白名单、结构化输出核对、候选/总结及用户处理记录 |
| `alpha/round8_budget.py`、`retest_review.py` | 历史累计额度与受控复验审查；尚不是通用个人预算系统 |
| `alpha/learning_recommendations.py` | 不调用模型的本地学习建议 |
| `alpha/migrations.py` | 迁移、完整学习数据库备份、摘要核对及新库恢复 |

## 数据如何流动

用户手动录入题目（或核对来源/OCR 草稿）后，后端建立任务、来源分类和首个代码版本。工作区保存代码修改形成版本；运行请求引用已保存版本，执行代理返回结果后，证据保存该版本 ID 和源码摘要。外部提交反馈也明确标识来源。

用户将错误表现与原因绑定到版本和可选证据；错题笔记再关联原始/修复版本、标签与待复习状态。跨题关联使用两条错误记录及建立时的快照，后续决策和失效状态分别保存。复习结果追加记录并更新待复习标记，本地规则按偏好和历史产生建议。

AI 是可选分支：保存版本后用户主动提问，后端提取允许的学习上下文、预留额度，再调用官方 API。结构化事实引用与已有证据核对；原因/总结作为候选由用户决定。模型不能修改执行事实，也不能凭回复证明代码已通过测试。

## 数据存储

`--data-dir` 下的 `learning.sqlite3` 保存学习数据。表涵盖 tasks、versions、evidence、turns、mistakes、reviews、recommendations、来源、错误修订、关联决策、学习偏好、教学草稿与 AI 调用记录等；SQLite 连接启用相关完整性设置，业务写入使用短事务。

Key 只保存在 `Tutor` 服务内存中。真实响应经当前 Key 脱敏后可能写入学习目录的 `ai_raw/`；这是敏感学习资料，不是可公开日志。累计授权账本默认在应用根目录 `.local/round8-ai-budget.sqlite3`，独立于所选学习库。

`--runtime-dir` 保存本机执行清单、验收状态等；镜像在 Docker 中。完整备份命令只生成学习 SQLite 快照与 JSON 摘要，其他目录和外部账本不在其中。恢复不迁移执行授权，也不恢复 Key 或重置 AI 额度。

## 执行与 AI 关闭状态

执行器先检查本机 Docker 环境、固定镜像/策略与验收证据；未满足则拒绝执行，不在宿主机运行提交代码。编译与运行使用受约束容器，无网络、非 root、只读根文件系统、去能力与 no-new-privileges，限制 CPU、内存、进程数、输出和临时空间，并核对清理。

运行通用限制包括 128 MiB 内存、32 个进程、3 秒墙钟、合计 16 KiB 输出；原生编译阶段使用独立的较高资源限制（例如 512 MiB、20 秒墙钟）。细节以策略文件和验收为准，不能将所有阶段写成同一额度。容器依赖共享内核，仅适合可信本机单人有限练习。

新服务 AI 默认为 disabled；主动聊天会拒绝模型调用，本地学习链路继续可用。demo 只返回明确标记的固定对话。真实模式才进入 HTTPS 传输与预算预留，没有自动重试；失败、未知费用、取消和停止会保留相应记录。

## 如何运行测试

以下为公开源码的离线测试入口。工作目录为该副本的应用根目录：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -X utf8 -m question_bank.validate
.\.venv\Scripts\python.exe -X utf8 run_public_tests.py
```

`run_public_tests.py` 发现 `tests/` 并显式装载保留的执行、启动、取消和验收门禁测试；会清除敏感环境变量并阻断外部 socket 连接。它不代表真实 DeepSeek 或真实 Docker 已通过；五语言实际容器验收使用 [快速开始](QUICK_START.md) 的 `alpha.runtime_setup build/accept` 命令，并产生独立证据文件。

不要对整个旧工程随意 `unittest discover` 后把旧题库合同失败解释为本公开候选结果。当前分发的专用测试入口不依赖原 20 题、旧学习库或旧 AI 响应；历史旧合同在隔离目录保留，不是改成假通过。

## 已有历史证据

| 类别 | 报告结果 | 主要覆盖 |
|---|---|---|
| 专用回归 | 15 新增 + 57 保留 = 72 项通过 | 空库/许可元数据合同、手工任务、记录、执行与启动守卫等 |
| 浏览器 | Chromium 6 项流程 | 空首页/空库、导入、Ctrl+S、刷新、复习/设置；历史记录页面错误 0、外部请求 0 |
| 独立 HTTP | 12 项检查 | 五语言实际结果绑定、来源、版本、错题/原因/复习/关联、AI 关闭拒绝、进程重启与恢复 |
| Docker | 五语言顶层 49 项及内嵌三语言 48 项 | 实际编译运行、限制与清理；层次重叠，不简单相加 |

这些数字来自 `TARGETED_ACCEPTANCE_REPORT.md` 及其 `evidence/`，不是本轮新结果。它们不等于全量覆盖率，也不证明所有 UI 组合或学习效果。

未覆盖范围包括完整真实 AI 闭环与费用、OCR 精确安装和真实识别、其他系统/浏览器/移动硬件、长期并发压力、公网多租户安全和完整无障碍认证。本次验证记录见 [发布检查](RELEASE_VALIDATION.md)；内部审计日志和历史材料不随源码分发。
