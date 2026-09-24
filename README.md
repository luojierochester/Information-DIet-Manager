# Information Diet Manager

本地页面记录与实验性文本统计工具。当前面向 **Windows + Chrome、每位用户在自己的电脑运行**，仍处于开发阶段。

目前可以接收扩展或导入数据、保存到 SQLite、分页查看已保存记录，并按需尝试文本分类、情感标签和相邻文本相似度分析。实验结果没有经过足以支持“健康等级”“心理状态”或“信息茧房诊断”的验证。

## 当前界面与数据口径

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
  tests/                      前端行为测试
src/hyh/                      FastAPI + Pydantic + SQLite
  app.py                      接收、查询与分析编排
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

# 使用个人数据目录，避免写入仓库随附的数据库
$dataDir = Join-Path $env:LOCALAPPDATA 'InformationDietManager'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$env:IDM_DB_PATH = Join-Path $dataDir 'idm.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn src.backend_api.app:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开终端打印的前端地址。API 文档在 `http://127.0.0.1:8000/docs`。前端默认连接 `http://127.0.0.1:8000`；可参考 `frontend/.env.example` 设置 `VITE_API_BASE_URL`，修改后重启前端。

`IDM_DB_PATH` 的父目录必须已存在。不设置该变量时，后端默认使用 `src/hyh/data/idm.sqlite3`。仓库目前仍跟踪该数据库，移除随附数据属于后续仓库清理工作。

### Chrome 扩展

打开 `chrome://extensions/`，开启开发者模式，选择“加载已解压的扩展程序”，加载项目根目录的 **`chrome-extension/`**。扩展声明最低 Chrome 120；本轮实际浏览器验证为 Windows 上的 Chrome for Testing 151，并未验证所有中间版本。

新安装默认关闭。在弹窗中确认本机接口、排除敏感域名，再明确开启；已有有效开关设置会保留。无效旧配置迁移为安全默认值并暂停。更新扩展后请刷新原有网页，使新版内容脚本生效。

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
```

测试使用临时数据库。模型输出相关契约测试使用合成结果，与真实推理验收分开记录。真实扩展验收使用 Playwright 下载的 Chrome for Testing，不会操作日常 Chrome 配置；它会在系统临时目录保留合成测试资料，截图和结果写入被 Git 忽略的 `output/playwright/round2/`。

Windows CI 会执行上述 Python、扩展单元/浏览器、前端测试和构建。详细结果、限制及后续优先级见 [第二轮改进记录](docs/round-2-improvements.md)；之前的界面修复见 [第一轮改进记录](docs/round-1-improvements.md)。

## 当前发布边界

当前是本地开发预览，尚未达到企业级发布标准。第二轮补齐了采集可靠性的主要回归和自动检查；接口配对认证/CORS、数据删除与恢复、重复访问模型、真实算法验收、完整依赖锁定、许可证及发布治理仍是缺口。仅绑定回环地址不能替代 API 访问保护。旧 `/analyze/run`、`/analyze/run_full`、`/dashboard/summary` 仍保留原有语义，前端已停止使用这些接口作为展示依据。

## 开发工作流

每完成一轮独立修复，运行相应验证、检查待提交差异，然后提交并推送到 `origin`。推送前同步远端更新并保留已有工作；不提交真实浏览数据、密钥或临时诊断产物。具体协作约定见 [AGENTS.md](AGENTS.md)。

统一入口也可通过 `python scripts/run_backend.py` 和 `python scripts/run_analysis.py --help` 使用；历史 `src.hyh` / `src.lsj` 入口为兼容保留。
