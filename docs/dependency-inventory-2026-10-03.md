# 离线依赖库存（2026-10-03）

新增 `scripts/generate_dependency_inventory.py`，使用 Python 标准库读取固定白名单文件并生成确定性 JSON。它补充发布时的依赖可追溯证据；不是正式 SBOM、实际安装环境快照、构建产物清单、漏洞报告或许可证审核。

## 用法与输出

在已按本项目锁安装的环境中运行（生成器本身只需标准库）：

```powershell
python scripts/generate_dependency_inventory.py --revision <完整的40位十六进制提交标识> --output output/dependency-inventory.json
```

输出父目录须已存在。脚本根据自身位置确定仓库根，不扫描当前目录、`node_modules`、虚拟环境、模型目录或 Git，也不联网。`--revision` 是调用方提供的标签；生成器校验格式并转为小写，不证明该提交存在、工作树干净、输入来自该提交或产物经过签名。CI 可传入 `GITHUB_SHA`，由对应检查和工作流另行提供提交证据。

JSON 记录 schema 版本 1、生成器版本 1.0.0、13 个实际输入文件的原始字节 SHA-256 和范围说明。同样的输入字节和 revision 产生相同 UTF-8 JSON 字节：键、包名、安装路径和允许哈希有稳定排序，没有生成时间或随机 UUID。Windows CRLF 与 LF checkout 的原始文件哈希可能不同；不宣称跨不同物理字节的 checkout 输出相同，也不宣称构建二进制可复现。

| 集合 | 当前来源与数量 | 输出含义 |
| --- | --- | --- |
| Python runtime | runtime 哈希锁，14 包 | 最小 API，Windows CPython 3.12 AMD64 |
| Python test | test 哈希锁，29 包 | 包含 runtime，不应将二者相加当独立总数 |
| Python toolchain | toolchain 哈希锁，35 包 | 独立维护 / 审计工具环境 |
| Python installer | installer 哈希锁，1 包 | 单列 `pip`，不并入应用或工具包集合 |
| npm frontend | frontend 的 manifest 和 v3 lock，63 个非根路径条目 | 全平台锁库存，保留开发、可选、平台、engines 等元数据 |
| npm extension-test | 扩展的 manifest 和 v3 lock，3 个非根路径条目 | 扩展验证工具；不等于随扩展交付的依赖 |
| vendored 文件 | `chrome-extension/readability.js` 和引用它的扩展 manifest | 单独文件哈希与头部原文声明；不依赖 npm 库存推断 |

Python 每包包含规范包名、版本、全部允许 SHA-256 和直接输入成员标记。允许哈希集合不等于本次实际安装选中的 wheel，锁中的多平台轮子也不扩大已验收的 Python 平台范围。npm 以安装路径作为身份，保留同名同版本的不同路径；当前未安装的 macOS / Linux / 其他平台可选条目仍保留。

`readability.js` 的 `version`、`verified_source`、`verified_license` 为 null，表示本生成器只读取文件并记录声明，不执行来源或法律验证。头部的 Arc90 1.7.1 祖先说明不能当作当前文件精确版本。外部独立研究可能获得更强的上游匹配证据；这些字段并不宣称该来源永远不可知。依赖中的 license 字符串同样只是来源元数据声明，不为本项目选择许可证。

## 校验与失败保护

- 复用既有 `parse_lock` 严格解析精确 Python pins 和每包 SHA-256，并保留全部允许哈希。runtime、test、toolchain 各直接输入必须与所属锁精确匹配；runtime 必须是 test 的同版本、同允许哈希子集。安装器只能包含 `pip`，与其他 Python 集合不重叠。维护环境保持独立，不强制其共享包版本等于应用环境。
- `-r` 仅允许引用已列入指纹的三个固定直接输入，拒绝未知文件、目录越界和循环，不隐式读取其他 requirements 文件。
- 所有 JSON 层级拒绝重复键和非标准 NaN / Infinity 常量。npm manifest、lock 顶层及 lock 根 name/version 一致，根 dependencies/devDependencies/optionalDependencies 声明一致，直接包必须存在。
- 当前扩展 npm 集合仅用于测试，额外拒绝其非空运行 / 可选根依赖，避免未来新增运行依赖时仍错误归为 extension-test；此类产品范围变化需先更新库存合同。
- npm 只接受当前项目使用的 v3 registry 锁、稳定 `x.y.z` 版本、SHA-512 SRI、普通或 scoped `node_modules` 路径。根直接声明仅支持精确版本和 caret 稳定三段版本并核对其直接解析节点；其他新语法、workspace/link/alias 等明确拒绝。它不解析传递依赖图、peer 条件或通用 SemVer，`npm ci` 仍承担实际锁安装验收。即使通过，也不证明 frontend `dist` 实际包含全部或仅有这些组件。
- 所有输入完成校验与 JSON 序列化后，在输出同目录写独立临时文件，再使用 `os.replace` 替换输出。校验、写入或替换失败不覆盖已有输出；只清理本次创建的临时文件。拒绝解析后指向任一输入或两个生成 / 校验脚本的输出路径。此项不承诺断电或文件系统损坏下的事务耐久性。

## 本地验证

Quality 工作流在既有测试、浏览器和构建检查成功后，先核对实际 HEAD 与 `GITHUB_SHA`，并拒绝暂存或工作区中的受控文件变化，再生成两份库存并比较 SHA-256。只将第一份固定文件归档为 `dependency-inventory-<完整提交SHA>`，保留 30 天；文件缺失会使步骤失败，不上传工作区目录、虚拟环境或其他诊断资料。归档使用 [官方 upload-artifact v7.0.1](https://github.com/actions/upload-artifact/tree/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)，已核对版本标签并固定完整提交。工作流的真实运行与归档结果仍须按最终项目提交验证。

锁定 Python 环境下专项：25 项测试，覆盖合成固定输入、所有原始文件指纹、直接 pins、跨集合关系、缺失 / 非法哈希、未知 includes、循环、JSON 重复键、npm 缺包 / 版本 / 格式 / 类型、精确和 caret 边界、同名不同路径、扩展测试范围变更、vendored 未验证状态、完整 revision、输入保护、旧输出保留、写入 / 替换失败及临时文件清理。最终 `scripts/tests` 联动为 `56 passed, 120 subtests passed`。

实际仓库 CLI 从无关临时 cwd 运行两次，字节完全一致；使用固定输入，不读安装环境或模型。该用例与合成用例属于库存工具验收，不是模型推理、浏览器验收、完整发布审计或精确提交的 CI 证据。全项目与对应提交 CI 由本轮总报告另行记录。

独立的本机 Windows CLI 文件系统探针还使用 `FileShare.None` 锁住自有合成旧输出：生成返回 1，旧 SHA-256 不变，自有临时文件清理；解锁后生成成功，第二次输出字节哈希相同。误将输出指向 `requirements-runtime.txt` 时返回 1，输入哈希不变。此为真实本机 I/O 验证，仍不扩展为异常断电或所有文件系统的耐久性保证。

独立复查再次通过 25 项专项与 90 个子测试，并用另外 7 组探针逐包比较全部 Python 允许哈希、实际输入指纹、全平台 npm 条目及失败保护。复查阶段曾发现空续行异常类型和扩展新增运行依赖被误归为测试工具的问题，均已在冻结前修正并加入回归；不是既有产品运行事故。

剩余治理边界包括正式交付文件范围、标准格式 schema 验证、来源证明 / 签名、许可证材料与义务审查，以及未锁定的可选模型 / 分析 / 训练环境、模型资产、浏览器和解释器二进制。库存不会据此给出“已达企业发布标准”的结论。
