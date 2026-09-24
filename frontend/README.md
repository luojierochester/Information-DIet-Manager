# Information Diet Manager 前端

Vue 3 + Vite + ECharts。本轮界面区分实时保存记录与手动运行的实验分析。

## 启动

先启动项目根目录 README 中的本地后端，再在本目录运行：

```powershell
npm ci
npm run dev -- --host 127.0.0.1
```

默认 API 地址为 `http://127.0.0.1:8000`。需要修改时，将 `.env.example` 复制为 `.env.local` 并修改 `VITE_API_BASE_URL`，然后重启 Vite。该地址会进入前端构建结果，不应包含密钥。

## 行为

- 保存记录来自 `/items`，每页 50 条；关闭记录弹窗时每 30 秒刷新首页，弹窗中显式加载更多。
- 分析仅在点击按钮时请求 `/dashboard/visualization?days=7`，开始新请求立即清空旧图表。
- 显示尚未运行、进行中、空数据、样本不足、不可用、失败、请求错误、成功快照等不同状态。
- 日期按响应中的 UTC 窗口对齐；缺失指标保留空值；合法的 0% 不用默认比例替换。
- 分类来自同一次分析，工具和购物分别显示。分类筛选只作用于实验趋势，不筛选原始记录。
- 原始记录没有逐条情感、访问次数和缓存状态，界面不会补造。
- 支持中英文、深浅主题；记录弹窗支持 Escape 关闭及焦点恢复。

## 验证

```powershell
npm test
npm run build
```

`tests/dashboard-data.test.js` 验证比例、日期缺口、状态替换、分类、分页和链接协议。浏览器人工编排验证另见 `../docs/round-1-improvements.md`；这些测试不能代替真实模型与扩展验收。
