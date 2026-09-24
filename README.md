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
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn pydantic python-multipart

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

打开 `chrome://extensions/`，开启开发者模式，选择“加载已解压的扩展程序”，加载项目根目录的 **`chrome-extension/`**。在弹窗中确认本地 API 地址和采集开关。

扩展会读取 URL、标题及页面文本等信息。当前采集队列和重复访问的数据模型仍有已知缺陷；请先在测试页面验证，不把结果当作完整浏览历史。扩展的时长采集尚未通过准确性验收。

### 实验分析

基础服务可以独立保存和查看记录。完整分析依赖 `src/lsj/` 中的代码、额外依赖和模型配置，目前仍存在导入及依赖问题；仅安装其 `requirements.txt` 不能视为完整推理环境已经可用。

依赖缺失或分析运行失败时，界面会显示不可用/失败，保留原始记录入口。本轮没有宣称真实模型推理、训练或采集全链路已经修复。

## 验证

```powershell
# 项目根目录：HTTP 契约与现有静态契约测试，无模型下载
.\.venv\Scripts\python.exe -m pip install pytest httpx pandas
.\.venv\Scripts\python.exe -m pytest src/hyh/tests src/lsj/tests -q

# 前端目录
cd frontend
npm test
npm run build
```

测试使用临时数据库。模型输出相关契约测试使用合成结果，与真实推理验收分开记录。浏览器验证与本轮边界见 [第一轮改进记录](docs/round-1-improvements.md)。

## 当前发布边界

当前是本地开发预览，尚未达到企业级发布标准。采集可靠性、重复访问保留、隐私控制、接口访问保护、模型可运行性、依赖锁定和持续集成仍需逐轮完善。旧 `/analyze/run`、`/analyze/run_full`、`/dashboard/summary` 仍保留原有语义，本轮前端已停止使用这些接口作为展示依据。

## 开发工作流

每完成一轮独立修复，运行相应验证、检查待提交差异，然后提交并推送到 `origin`。推送前同步远端更新并保留已有工作；不提交真实浏览数据、密钥或临时诊断产物。具体协作约定见 [AGENTS.md](AGENTS.md)。

统一入口也可通过 `python scripts/run_backend.py` 和 `python scripts/run_analysis.py --help` 使用；历史 `src.hyh` / `src.lsj` 入口为兼容保留。
