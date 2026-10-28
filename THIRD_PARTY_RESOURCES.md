# 开源及第三方资源清单

既有资源审计日期：2026-10-28，本轮复用其元数据/许可文本证据；非重新执行全量第三方审查。源码导出没有捆绑解释器、wheel、Docker镜像、OCR模型或第三方题库。版本以 requirements-lock.txt 和下表为准。项目应用逻辑为定制代码，选定项目源码/文档/AI内容的授权条件已由本人确认；该确认不扩及第三方资源。

| 资源 | 使用方式及许可状态 | 分发决定 |
|---|---|---|
| 项目 Alpha及最小web_lab辅助源码 | 本人本轮已确认选定源码/文档/AI内容公开授权条件；MIT及版权署名已由本人确认 | 保留本候选范围，experiments不入包；自有授权范围适用根MIT，第三方资源保留各自条款 |
| 原20道 project_original 练习、测试、参考解 | 内容公开范围未确认，已排除 | 原题面、测试、oracle、参考解、生成器均不在负载 |
| FastAPI 0.118.3 | MIT；HTTP框架 | 通过依赖安装，保留许可；[上游](https://github.com/fastapi/fastapi/blob/0.118.3/LICENSE) |
| Uvicorn 0.49.0 | BSD-3-Clause；ASGI服务 | 通过依赖安装；[上游](https://github.com/Kludex/uvicorn/blob/0.49.0/LICENSE.md) |
| Pillow 12.2.0 | MIT-CMU，另有图像库组件通知 | 通过依赖安装，组件详见 wheel 内通知；[上游](https://github.com/python-pillow/Pillow/blob/12.2.0/LICENSE) |
| 传递依赖及 HTTPX 开发依赖 | 见下表；原始元数据和许可文本单独附录 | 未捆绑安装二进制，后续如打包需完整履行许可 |
| CPython 3.12 / SQLite | PSF许可 / SQLite自身声明；运行时由用户安装 | 不捆绑解释器或数据库引擎 |
| Docker Engine / Docker Desktop | Engine 与 Desktop 适用条款分开；用户自行安装 | 不分发安装包；Desktop适用条件由使用者核实 |
| GCC 14.2、G++ / Debian基础 | 编译运行基础镜像涉及GPL及系统组件多种许可 | 只分发Dockerfile及固定摘要；本轮不分发镜像。未来分发镜像需逐组件许可证、源码义务审计 |
| Temurin JDK 17.0.18 / Node 22.22.0 | JDK涉及GPL-2.0-with-classpath-exception；Node及所含组件见 NODE_LICENSE | Node许可文件原样保留；镜像/运行时二进制不分发 |
| RapidOCR 1.4.4 / ONNX Runtime 1.31.0 | 可选OCR；包通常分别按Apache-2.0 / MIT，精确模型与包覆盖范围本轮未重新闭合 | 仅给依赖坐标，不捆绑库或ONNX模型；模型再分发禁止自动进行 |
| OpenCV 5.0.0.93 / NumPy 2.5.4 / Pillow 13.0.0 | 可选OCR隔离环境，含传递二进制许可 | 未安装复验本组，精确wheel和模型许可需继续确认 |
| DeepSeek | 外部服务，服务条款不是开源许可证；目录价格为既有记录非新报价核验 | 不分发Key/授权/真实原响应，不调用 |
| Codex / AI辅助制作工具 | 辅助代码、调试、测试和材料；非项目运行依赖 | 参赛者补真实审核与贡献说明 |
| 洛谷、PTA/Pintia、LeetCode等平台 | 仅来源URL/标识规则与合成测试引用 | 不含抓取题干、题解或用户答案；人工导入内容不得随源码公开 |
| CodeNet、P3、DMOJ、exercise-10k、Exercism | 既有研究候选，不在本包内 | 未取得逐项授权的内容不导入或再分发 |

## 实际安装依赖元数据

下表来自候选组装阶段的安装元数据，版本与锁文件对应。`third_party_licenses/` 保留实际安装发行包提供的许可证/NOTICE，不是给整个项目授予许可证。上游作者/邮箱及机构名属于必须保留的第三方权利声明，不是参赛团队身份；不可机械删除。

| 包 | 版本 | 安装元数据许可 |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0) |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.118.3 | License :: OSI Approved :: MIT License |
| h11 | 0.16.0 | License :: OSI Approved :: MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | License :: OSI Approved :: BSD License |
| idna | 3.20 | BSD-3-Clause |
| pillow | 12.2.0 | MIT-CMU |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| starlette | 0.48.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.49.0 | BSD-3-Clause |

本轮无新增运行依赖。独立验证环境额外安装Playwright 1.58.0只用于浏览器检查，不属于应用依赖、不随负载分发。原未核验OCR模型、wheel、镜像条件保持，不因本人确认项目源码而放行。
