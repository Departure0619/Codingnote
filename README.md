# AIC 编程错题学习工作台（Alpha）

面向大学生与编程自学者，把不同平台遇到的题目、代码修改、运行结果和错误原因保存在同一个本地工作台中，便于回看和复习。

同样的循环边界错误，可能分别出现在 PTA、洛谷和自主练习里。单个平台的错题本往往只能看到各自的提交记录；这里可以按知识标签整理这些题目，用具体的错误记录建立跨题关联，回看“当时写了什么、实际发生什么、为什么出错、后来怎样修正”。关联由用户核对，不能仅凭标签相同就断言原因相同。

状态：**Alpha / REAL_AI_PARTIAL**。真实 AI 教学闭环尚未完整验收。此处“跨平台”指题目来源平台；当前有安装验收依据的操作系统是 Windows。

## 能做什么

| 能力 | 使用方式 |
|---|---|
| 跨平台收集 | 手动填写题干、来源链接、平台与原题号；链接识别提供来源候选，不自动抓取题面 |
| 分类与原因 | 设置一级知识类型和二级标签，记录错误表现、原因及其确认状态 |
| 代码与证据 | 保存代码版本；运行结果绑定当时的版本，避免把旧结果当作新代码的证明 |
| 五语言工作区 | Python、C17、C++17、Java 17、JavaScript；实际运行需完成本机 Docker 构建与验收 |
| 错题与关联 | 保存原始/修复版本、笔记和标签；基于两条错误记录提出并核对关联 |
| 复习 | 标记待复习，记录“已理解 / 仍不确定 / 需要重做”，查看本地规则生成的建议 |
| 可选 AI | DeepSeek 辅导、错误原因候选和总结草稿已有实现；真实教学闭环仅部分验收 |
| 数据迁移 | 本地 SQLite 学习库，提供完整离线备份与恢复；网页脱敏导出另有用途 |

初始题库为空，请导入自己有权使用的内容。公开候选不包含原有 20 道题及其参考解、测试数据和生成器。

## 快速开始

项目仓库：[Departure0619/Codingnote](https://github.com/Departure0619/Codingnote)。下载源码 ZIP 并解压，或执行 `git clone https://github.com/Departure0619/Codingnote.git`、`cd Codingnote`，然后在包含 `alpha/` 与 `requirements-lock.txt` 的应用根目录打开 PowerShell。当前有验收依据的环境为 Windows、64 位 Python 3.12；其他系统的验证边界见安装指南。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -X utf8 -m question_bank.validate
.\.venv\Scripts\python.exe -X utf8 -m alpha --port 8039 --data-dir data/public-demo --runtime-dir data/public-runtime --no-browser
```

打开 [本机工作台](http://127.0.0.1:8039/)，点击“导入题目”，填写内容并保存进入工作区。先保存代码版本，再运行、记录原因和错题笔记。前台终端按 `Ctrl+C` 停止服务。

没有 Docker 也可建题、保存代码、整理错题和复习；代码运行需额外设置。完整步骤见 [从零开始](docs/QUICK_START.md)。

## AI 与费用

新服务默认关闭 AI，不读取旧 `.env` 或旧 Key。可以一直不启用 AI；模拟模式只演示对话记录，不分析代码正确性，也不生成诊断或总结。

优先介绍 DeepSeek 官方 API，因为当前真实调用实现对接的就是该官方服务。用户须自行获取 Key、主动授权数据发送和费用，并承担服务商可能收取的费用；不承诺免费。Key 保存在当前服务内存中，重启需重新填写。

配置不自动测试连接，也不代表已经成功调用。当前候选仍受历史累计预算与受控复验限制，不能视为通用、自由设额的 AI 接入产品。详见 [DeepSeek 配置与费用](docs/DEEPSEEK_API_SETUP.md)。

真实 AI 教学闭环仅部分验收：真实总结编辑保存及部分 C++ 解释/总结等仍未完成验收。OCR 安装与真实识别、其他浏览器和移动硬件、其他操作系统、长期压力并发、完整无障碍验证也有未验证范围。见 [功能与限制](docs/FEATURES_AND_LIMITATIONS.md)。

## 文档导航

- [安装、使用、停止与恢复](docs/QUICK_START.md)
- [DeepSeek 官方 API 配置](docs/DEEPSEEK_API_SETUP.md)
- [功能支持范围与验证边界](docs/FEATURES_AND_LIMITATIONS.md)
- [代码结构与测试](docs/ARCHITECTURE_AND_TESTING.md)
- [安全与隐私](docs/SECURITY_AND_PRIVACY.md)

## 许可证与制作说明

项目自有源码、测试与文档按 [MIT](LICENSE) 提供，版权行：`Copyright (c) 2026 Departure0619`。适用范围见 [许可范围](LICENSE_SCOPE.md) 与 [逐文件分类](FILE_LICENSE_SCOPE.csv)。[第三方资源与通知](THIRD_PARTY_RESOURCES.md)、`third_party_licenses/` 和 `alpha/native_image/NODE_LICENSE` 保留各自许可与署名；项目 MIT 不为外部题面、模型或镜像授予再分发权。

AI 辅助参与了代码、调试、测试和文档制作。应用中的 AI 教学能力及其未验收范围与制作工具的参与分别披露，不据此宣称学习效果、参赛结果或获奖。当前源码版本标识：`alpha-public-20261028`。
