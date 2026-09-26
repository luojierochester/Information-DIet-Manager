# Information Diet Manager

本地页面记录与实验性文本统计工具。当前面向 **Windows + Chrome、每位用户在自己的电脑运行**，仍处于开发阶段。

目前可以接收扩展或导入数据、保存到 SQLite、分页查看已保存记录，并按需尝试文本分类、情感标签和相邻文本相似度分析。实验结果没有经过足以支持“健康等级”“心理状态”或“信息茧房诊断”的验证。

第三轮增加了本机管理/采集密钥、来源与权限检查、页面记录备份、逐条/全部删除和事务恢复。**升级后需要分别给界面和扩展填写密钥，默认数据库位置也已改变**，详见下方升级说明。

## 当前界面与数据口径

- **保留原外观**：恢复 `4d0bcd9` 的科技风首页、动效、四图布局与右侧抽屉。中英文、主题、高亮色、字体继续位于右上角齿轮；密钥连接、备份、恢复、删除也收进此设置面板，见 [本轮恢复说明](docs/ui-restoration-2026-09-26.md)。
- **已保存记录**：实时读取 `/items`，每页 50 条，可加载更多。记录按页面去重，不等于访问次数、精确阅读时长或完整浏览历史。
- **近 7 天实验分析**：只有点击按钮才请求 `/dashboard/visualization?days=7&force=true`，重新计算而不是复用旧缓存。分析范围是最近 7×24 小时，日趋势按 UTC；时间窗口可能跨 8 个日历日期。
- **明确状态**：没有数据、样本不足、分析不可用、分析失败分别显示。缺失指标不补零，不生成固定评分、处方或虚构逐条情感标签。
- **快照与覆盖范围**：图表来自同一次分析；新记录不会自动加入。超过分析行数上限时提示只读取最早的一部分。
- **最低样本量**：目前为 5 条，这是与默认评估器一致的运行门槛，不代表统计充分性或模型准确性保证。

## 项目结构

```text
chrome-extension/             Chrome Manifest V3 扩展
scripts/                      后端与命令行分析启动脚本
src/backend_api/              统一后端入口，转接现有 hyh 实现
src/analysis_engine/          统一分析入口，转接现有 lsj 实现
frontend/                     Vue 3 + Vite + ECharts 界面
  src/dashboard-data.js       响应校验、日期对齐与指标转换
  src/local-api.js            仅内存保存密钥的本机 API 客户端
  tests/                      前端行为测试
src/hyh/                      FastAPI + Pydantic + SQLite
  app.py                      接收、查询与分析编排
  security.py                 密钥、来源、进程与请求边界
  data_management.py          事务删除与页面记录备份/恢复
  CONTRACT.md                 API 契约及当前限制
  tests/                      隔离 SQLite 的 HTTP 契约测试
src/lsj/                      数据获取、分类、情感、相似度和评估代码
  requirements.txt            实验分析依赖清单（尚未完整锁定或验证）
docs/                         审查记录与分轮改进说明
```

## 本地开发启动（PowerShell）

下面先启动记录存储与界面，不要求下载机器学习模型。本轮验证环境为 Python 3.12、Node.js 24、Windows 和 Chrome；尚未建立完整版本兼容矩阵。

从项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime.txt

# 默认使用当前 Windows 用户的 LocalAppData 数据目录
.\.venv\Scripts\python.exe scripts/run_backend.py
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --strictPort
```

打开终端打印的前端地址。服务首次启动会创建两把不同的随机密钥，并且**只打印密钥文件路径，不打印密钥值**。打开该本地文件，点击界面右上角齿轮，将 `admin_token` 的值粘贴到“连接本机服务”中的“本机管理密钥”；将 `collector_token` 的值粘贴到扩展设置。前端密钥仅保存在当前页面内存，刷新后需要重新连接。不要把密钥写进 URL、前端环境变量、截图或 Git。

默认数据库是 `%LOCALAPPDATA%\InformationDietManager\idm.sqlite3`，密钥文件在旁边，名为 `idm.credentials.json`。父目录会自动创建。可通过 `IDM_DB_PATH` 覆盖路径；请使用当前用户的私有 NTFS 目录，不要使用共享、网络或公开同步目录。Windows 密钥文件仅给当前用户及 SYSTEM 显式权限；SQLite 和下载的备份没有应用层加密。

前端默认连接 `http://127.0.0.1:8000`；可参考 `frontend/.env.example` 设置 `VITE_API_BASE_URL`，只允许回环地址，修改后重启或重建前端。默认允许的界面来源为 `localhost` / `127.0.0.1` 的 HTTP 5173、4173 端口。如改前端端口，须在启动后端前设置 `IDM_FRONTEND_ORIGINS` 为对应的完整来源，多个值以逗号分隔；不允许通配符或远程来源。使用 `--strictPort` 可避免 Vite 自动换端口造成连接失败。

启动器只监听 `127.0.0.1`，默认单进程且不热重载；开发时可显式加 `--reload`。不要公开代理此服务。同一数据库只允许一个 IDM 服务进程。在线 `/docs`、`/redoc`、`/openapi.json` 已关闭，接口说明见 [API 契约](src/hyh/CONTRACT.md)。

### 从旧版本升级

旧数据库不会自动迁移或删除。若之前没有设置 `IDM_DB_PATH`，旧版本使用的是 `src/hyh/data/idm.sqlite3`；新版本会打开上述用户目录中的数据库，因此首次看到空列表不代表旧数据被删除。仓库仍跟踪旧数据库，当前默认运行已不再使用它；仓库内容及历史清理仍待单独处理。

需要迁移时，先暂停扩展并备份原数据库文件；停止后端，将 `IDM_DB_PATH` 暂时设为旧文件的完整路径，用新后端和新生成的管理密钥下载页面记录备份。再停止后端，移除该环境变量或改成新的私有路径，重启后用新路径对应的管理密钥执行“替换恢复”。恢复会覆盖目标中的全部页面记录。超过 10,000 条 / 20 MiB 或不满足当前字段契约的旧记录会明确拒绝，不能把这一途径视为任意历史数据库的自动迁移工具。

扩展需在 `chrome://extensions/` 重新加载并保存 `collector_token`。升级保留有效开关和待同步队列；未填密钥时不会发送，已开启的采集仍可能把新记录留在扩展本地。

### 备份、删除与密钥轮换

连接后可在右上角设置面板下载页面记录备份、删除单条/全部记录、从备份替换恢复。单条删除从已加载记录中选择，更多记录可先在右侧抽屉加载。删除或恢复前先暂停扩展并清空待同步队列，避免旧内容重新上传；浏览新页面后仍可再次采集。删除会同时清空分析缓存，不能删除已经下载到其他位置的备份，也不保证磁盘取证级擦除。

备份是包含网页内容的明文 JSON，带格式版本和 SHA-256 校验，最多 10,000 条 / 20 MiB。校验用于发现意外损坏，不是防篡改签名。备份不含密钥、扩展队列和分析历史；恢复重新生成内部 ID、创建时间和向量。格式错误、重复页面或写入失败会回滚整个恢复事务。连接中断时操作可能已经提交，需刷新核对后再决定重试。

需要撤销旧密钥时，先停止使用同一数据库的服务，然后运行：

```powershell
.\.venv\Scripts\python.exe scripts/rotate_local_keys.py
```

重启服务，并分别在界面和扩展更新密钥。脚本沿用 `IDM_DB_PATH`，不会修改页面记录；服务仍在运行时会拒绝轮换。高级配置可同时提供两个不同的 `IDM_ADMIN_TOKEN` / `IDM_COLLECTOR_TOKEN`，此时须自行更新这两个环境变量；不能通过空值关闭认证。详见 [安全边界与恢复说明](docs/local-security-and-recovery.md)。

### Chrome 扩展

打开 `chrome://extensions/`，开启开发者模式，选择“加载已解压的扩展程序”，加载项目根目录的 **`chrome-extension/`**。扩展声明最低 Chrome 120；本轮实际浏览器验证为 Windows 上的 Chrome for Testing 151，并未验证所有中间版本。

新安装默认关闭。在弹窗中确认本机接口、排除敏感域名，再明确开启；已有有效开关设置会保留。无效旧配置迁移为安全默认值并暂停。更新扩展后请刷新原有网页，使新版内容脚本生效。

只向扩展填写 `collector_token`，不要填写管理密钥。有效采集密钥只允许提交记录和检查自身角色，无权读取、删除、导出或分析已有数据。它保存在扩展的受限本地存储中，并非操作系统密钥库；仅显示“已填写”不代表服务已验证。401/403 会保留记录；修正密钥或接口后可重试。

- 仅采集可见的顶层 HTTP/HTTPS 页面；不采集无痕、回环地址、排除域名和含密码输入框的页面。提取前从副本移除表单及可编辑区域。
- 保存标题与最多 1,000 字符正文；清除 URL 凭据、片段和常见令牌参数，不保存 referrer 或任意网页元数据。**这不是通用敏感信息检测**，正文、标题和其他 URL 参数仍可能含隐私，应主动排除邮箱、管理后台等网站。
- 只向 `localhost`、`127.0.0.1` 或 `[::1]` 上的 `/collect` 发送，禁止重定向。扩展仍需要广泛的 HTTP/HTTPS 主机权限来读取用户选择浏览的页面；尚未改成逐站授权。
- 默认队列容量 300 条，满时拒收新增并显示累计拒收次数；旧记录保留。网络失败自动退避重试；格式拒绝进入“需要处理”，不会因超过四次重试而删除。页面关闭前仍未成功入队的内容可能无法补采。
- 只有后端明确确认“新增”或“已存在”才从队列移除。暂停保留队列；清空仅删除扩展待确认数据，不能撤回服务端已接收的请求。修改排除域名不会删除已有队列或后端数据。
- 记录按**清理参数后的页面地址**去重，仍不保留重复访问/正文版本，也不采集准确阅读时长。不能把记录数当作访问次数或完整浏览历史。

扩展数据状态保存在 `idm_state_v1`，迁移成功后移除旧存储键。降级到旧扩展不会识别新队列，暂不支持直接降级恢复；升级前如需保留关键待同步数据，应先确认完成同步。

### 实验分析

基础服务可以独立保存和查看记录。完整分析依赖 `src/lsj/` 中的代码、额外依赖和模型配置，目前仍存在导入及依赖问题；仅安装其 `requirements.txt` 不能视为完整推理环境已经可用。

依赖缺失或分析运行失败时，界面会显示不可用/失败，保留原始记录入口。第二轮已验证合成页面采集到本地数据库的链路；真实模型推理、训练和结论有效性仍未通过验收。

## 验证

```powershell
# 项目根目录：HTTP 契约与现有静态契约测试，无模型下载
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest src/hyh/tests src/lsj/tests -q

# 扩展状态机、消息边界和内容脚本回归，无浏览器依赖
node --test chrome-extension/tests/*.test.cjs

# 扩展真实浏览器验收：独立配置、临时数据库、合成页面
npm ci --prefix chrome-extension
Push-Location chrome-extension
npx playwright install chromium --no-shell
Pop-Location
$env:IDM_TEST_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm --prefix chrome-extension run test:e2e

# 前端目录
cd frontend
npm test
npm run build
npm run test:e2e
```

测试使用临时数据库和合成密钥。模型输出相关契约测试使用合成结果，与真实推理验收分开记录。扩展与前端验收共用 `chrome-extension` 中锁定的 Playwright 和下载的 Chrome for Testing，不会操作日常 Chrome 配置；它们会在系统临时目录保留合成资料，截图和结果分别写入被 Git 忽略的 `output/playwright/round2/` 和 `output/playwright/round3/`。前端浏览器测试会启动临时后端、构建并服务真实生产页面。

Windows CI 会执行上述 Python、扩展与前端单元/浏览器测试及构建。最新结果、限制及后续优先级见 [第三轮改进记录](docs/round-3-improvements.md)；历史基线见 [第二轮](docs/round-2-improvements.md) 和 [第一轮](docs/round-1-improvements.md)。

## 当前发布边界

当前是本地开发预览，尚未达到严格企业级发布标准。已建立基础采集可靠性、API 访问保护、页面记录删除与事务恢复及自动验收。逐站权限、保留周期、重复访问模型、真实算法验收、完整依赖锁定、仓库/许可证和发布治理仍未完成。现有安全机制不防御同一用户权限的恶意进程、已被控制的浏览器或操作系统。旧 `/analyze/run`、`/analyze/run_full`、`/dashboard/summary` 仍保留原有语义，前端已停止使用这些接口作为展示依据。

## 开发工作流

每完成一轮独立修复，运行相应验证、检查待提交差异，然后提交并推送到 `origin`。推送前同步远端更新并保留已有工作；不提交真实浏览数据、密钥或临时诊断产物。具体协作约定见 [AGENTS.md](AGENTS.md)。

统一入口也可通过 `python scripts/run_backend.py` 和 `python scripts/run_analysis.py --help` 使用；历史 `src.hyh` / `src.lsj` 入口为兼容保留。
