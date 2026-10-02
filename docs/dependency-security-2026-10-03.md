# 2026-10-03 依赖安全与稳定工具链

本轮处理实际安装入口 `frontend/` 和 `chrome-extension/` 的 JavaScript 依赖，并为最小 Python 运行/测试环境补充完整传递依赖审计。没有修改首页、图表或抽屉的视觉源代码，也没有升级应用 Python 依赖清单。

## 修复范围

| 依赖 | 原锁定版本 | 新锁定版本 | 原因 |
| --- | --- | --- | --- |
| ECharts | 6.0.0 | 6.1.0 | 修复已公开的 XSS 公告 |
| Vite | 8.0.0-beta.16 | 8.3.2 | 使用稳定版，删除重复的 beta override |
| @vitejs/plugin-vue | 6.0.4 | 6.0.9 | 与稳定 Vite 8 配套 |
| Rolldown | 1.0.0-rc.6 | 1.2.12 | 随 Vite 更新，移除候选版工具链 |
| PostCSS | 8.5.6 | 8.5.28 | 修复 CSS/source map 处理相关公告 |
| nanoid | 3.3.11 | 3.3.19 | 修复异常尺寸输入相关公告 |
| picomatch | 4.0.3 | 4.0.7 | 修复 glob 处理相关公告 |

Vue 的锁定版本保留为 3.5.29；扩展的 Playwright 保留为 1.62.1。三个直接升级的包在 `package.json` 中使用明确版本，全部传递版本记录在 lock 文件中。前端锁文件的 63 个依赖包均无预发布版本；各平台的可选原生依赖仍保留。

前端 lock 文件中原有 21 个镜像下载地址改为官方 npm 地址。更换前逐包读取官方版本元数据，要求其 integrity 与旧锁文件完全一致，未通过更换下载地址改变这些包的版本或内容。锁文件现在没有非官方 registry 的 resolved 地址，随后用 `npm ci` 验证安装。

## 风险判断

修改前向官方 npm registry 请求审计，前端返回 4 个受影响包：3 个 high、1 个 moderate。这是依赖版本匹配结果，不等于四条已经证实可利用的产品路径。

- [ECharts 公告](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx)涉及 `lines` 系列的默认 HTML tooltip。当前图表使用 `pie`、`graph`、`line`，且 tooltip 为 `richText`，未证实该公告在当前路径可利用；仍升级受影响版本。
- PostCSS 和 nanoid 在 Vue 的 compiler-sfc 依赖树中，因此 `npm audit --omit=dev` 也会报告它们。当前产品没有将采集的网页内容传入 CSS 编译器，不能仅凭 npm 的 prod 标签认定浏览器运行路径可利用。
- picomatch 位于 Vite/tinyglobby 工具依赖树中，主要涉及构建环境。开发工具依赖同样纳入审计。

Vite 8 已有[官方稳定版发布说明](https://vite.dev/blog/announcing-vite8)，本轮无需继续停留在 beta。

## 持续检查

Windows Quality 工作流在扩展和前端安装依赖后，分别执行：

```text
npm audit --package-lock-only --include=dev --include=optional --include=peer --audit-level=low --registry=https://registry.npmjs.org
```

检查完整锁文件，包括开发、可选和 peer 依赖，不仅审计当前操作系统已安装的包。低风险及以上告警会使步骤失败。网络或 registry 审计接口失败也会使步骤失败，没有忽略退出码、自动跳过或 `continue-on-error`。

Python 门禁使用独立虚拟环境安装固定工具版本 `pip-audit==2.10.1`，不向应用测试环境安装审计工具。它按照当前 Windows / Python 3.12 平台，实际解析 `requirements-test.txt` 及其包含的 `requirements-runtime.txt`，然后查询官方 PyPI 漏洞服务：

```text
python -m pip_audit --strict -r requirements-test.txt --index-url https://pypi.org/simple --vulnerability-service pypi --progress-spinner off --format json
```

这里的 `python` 为隔离审计环境中的解释器。没有使用跳过依赖解析的 `--no-deps`、`--disable-pip`，也没有忽略具体漏洞。工具安装、依赖解析、漏洞查询的失败均使工作流失败；`--strict` 使依赖收集失败无法被当作清洁结果。[pip-audit 官方说明](https://github.com/pypa/pip-audit)

## 本轮依赖验证

- 官方 registry 的前端完整锁文件审计：63 个包，0 个已知漏洞。
- 官方 registry 的扩展完整锁文件审计：0 个已知漏洞。
- Windows / Python 3.12.5、pip-audit 2.10.1 实际解析得到 28 个最小运行/测试依赖，PyPI 审计返回 0 个已知漏洞、0 个跳过项，退出码为 0；包括 7 个直接依赖与 21 个传递依赖。
- 前端 `npm ci` 成功；图表映射与原外观契约 12 项通过。
- Vite 8.3.2 构建通过；仍报告主 JavaScript 块大于 500 kB。这是尚未处理的体积警告，不将提高阈值当作性能修复。

以上是依赖变更的局部检查。整轮前端单元、实际浏览器及远端 CI 的最终结果应以本轮综合验收记录和对应提交的 Actions 为准。源码契约测试不能替代真实浏览器中的布局与交互验收。

Python 本次解析版本如下。这是当次审计快照，不是用于安装的完整锁文件。

```text
fastapi==0.141.1          uvicorn==0.53.0            pydantic==2.13.5
python-multipart==0.0.32 pytest==9.1.1             httpx==0.28.1
pandas==2.3.3            pydantic-core==2.46.5     httpcore==1.0.9
pluggy==1.6.0            annotated-doc==0.0.5      annotated-types==0.8.0
click==8.5.0             colorama==0.4.6           h11==0.16.0
iniconfig==2.3.0         numpy==2.5.3              pygments==2.21.0
python-dateutil==2.9.0.post0                        pytz==2026.4
six==1.17.0              starlette==1.7.0          anyio==4.15.1
idna==3.20               typing-extensions==4.16.0 typing-inspection==0.4.4
tzdata==2026.4           certifi==2026.7.22
```

## 尚未完成

漏洞库会继续变化，零告警仅代表本次查询结果，不构成软件无漏洞证明。Python 门禁仅覆盖当前 Windows / Python 3.12 下的最小运行/测试依赖，不包括根 `requirements.txt`、`src/lsj/requirements.txt` 的训练/模型环境，也不包括历史根目录 JavaScript 依赖。

Python 的传递依赖尚未全部固定或提供哈希，今后安装和审计可能解析出新版本；审计工具自身的传递依赖也未锁定。完整可复现锁文件、其他平台验收、模型环境验收、历史仓库内容清理、许可证、SBOM 和发行制品来源验证仍需单独完成。本轮没有重写 Git 历史或读取个人浏览数据库。
