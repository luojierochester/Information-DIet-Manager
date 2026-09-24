<template>
  <div class="dashboard" :class="{ light: light }">
    <header>
      <div><h1>Information Diet Manager</h1><p class="muted">{{ t.subtitle }}</p></div>
      <div class="controls">
        <select v-model="lang" aria-label="Language / 语言"><option value="zh">中文</option><option value="en">English</option></select>
        <button @click="light = !light">{{ light ? t.dark : t.light }}</button>
      </div>
    </header>
    <section class="card">
      <div class="heading"><h2>{{ t.records }}</h2><div class="controls">
        <button @click="fetchRecords()" :disabled="recordsLoading">{{ recordsLoading ? t.loading : t.refresh }}</button>
        <button ref="recordsButton" @click="openRecords">{{ t.viewRecords }}</button>
      </div></div>
      <p v-if="recordsError" role="alert" class="notice error">{{ t.recordsError }}</p>
      <div class="total"><strong>{{ records.total ?? '—' }}</strong><span>{{ t.savedPages }}</span></div>
      <p class="muted">{{ t.recordLimit }}</p>
      <p v-if="recordsUpdatedAt" class="muted">{{ t.updated }} {{ formatTime(recordsUpdatedAt) }}<span v-if="recordsError"> · {{ t.stale }}</span></p>
    </section>
    <section class="card">
      <div class="heading"><h2>{{ t.analysis }} <span class="tag">{{ t.experimental }}</span></h2>
        <button class="primary" @click="runAnalysis" :disabled="analysis.status === 'loading'">{{ analysis.status === 'loading' ? t.running : t.run }}</button>
      </div>
      <p>{{ t.analysisLimit }}</p>
      <p class="notice" :class="{ error: ['failed', 'unavailable', 'request_error'].includes(analysis.status) }" role="status" aria-live="polite" data-testid="analysis-status">{{ analysisMessage }}</p>
      <p v-if="analysis.generatedAt" class="muted">{{ t.snapshot }} {{ formatTime(analysis.generatedAt) }}</p>
      <p v-if="analysis.window.from_ts != null" class="muted">{{ t.window }} {{ formatUtc(analysis.window.from_ts) }} — {{ formatUtc(analysis.window.to_ts) }} (UTC)</p>
      <p v-if="analysis.window.truncated" role="alert" class="notice error">{{ t.truncated.replace('{n}', analysis.window.input_count).replace('{total}', analysis.window.available_count) }}</p>
      <p v-if="analysis.status === 'ready'" class="muted">{{ t.coverage.replace('{n}', analysis.window.processed_count).replace('{total}', analysis.window.available_count) }}</p>
    </section>
    <section class="chart-grid" :aria-label="t.charts">
      <article class="card"><h2>{{ t.categories }}</h2><p class="muted">{{ t.categoriesHint }}</p><div ref="pieRef" class="chart" :aria-label="t.categories" role="img"></div></article>
      <article class="card">
        <h2>{{ t.readingGuide }}</h2><ul class="guide"><li>{{ t.guide1 }}</li><li>{{ t.guide2 }}</li><li>{{ t.guide3 }}</li></ul>
        <label>{{ t.selectedCategory }} <select v-model="category" :disabled="analysis.status !== 'ready'">
          <option value="global">{{ t.allCategories }}</option>
          <option v-for="key in Object.keys(analysis.counts)" :value="key" :key="key">{{ categoryLabel(key) }}</option>
        </select></label>
      </article>
      <article class="card"><h2>{{ categoryLabel(category) }} · {{ t.repetition }}</h2><p class="muted">{{ t.repetitionHint }}</p><div ref="repeatRef" class="chart" :aria-label="t.repetition" role="img"></div></article>
      <article class="card"><h2>{{ categoryLabel(category) }} · {{ t.sentiment }}</h2><p class="muted">{{ t.sentimentHint }}</p><div ref="sentimentRef" class="chart" :aria-label="t.sentiment" role="img"></div></article>
    </section>
    <dialog ref="recordsDialog" @close="drawerOpen = false" @click="closeOnBackdrop" aria-labelledby="drawer-title">
      <div class="heading"><h2 id="drawer-title">{{ t.records }}</h2><button autofocus @click="closeRecords">{{ t.close }}</button></div>
      <p class="muted">{{ t.rawHint }}</p>
      <p v-if="recordsError" role="alert" class="notice error">{{ t.recordsError }}</p>
      <p v-if="recordsLoading && !records.items.length" role="status">{{ t.loading }}</p>
      <p v-else-if="!records.items.length && !recordsError">{{ t.noRecords }}</p>
      <ol class="record-list">
        <li v-for="item in records.items" :key="item.id">
          <time>{{ formatTime(item.ts) }}</time>
          <a v-if="safeHttpUrl(item.url)" :href="safeHttpUrl(item.url)" target="_blank" rel="noopener noreferrer">{{ item.title || t.untitled }}</a>
          <span v-else>{{ item.title || t.untitled }}</span>
          <span class="muted">{{ t.source }}: {{ sourceLabel(item.source) }}<template v-if="item.channel"> · {{ t.channel }}: {{ item.channel }}</template></span>
        </li>
      </ol>
      <div class="pagination" v-if="records.total !== null">
        <p>{{ t.loaded.replace('{n}', records.items.length).replace('{total}', records.total) }}</p>
        <button v-if="records.page * records.pageSize < records.total" @click="fetchRecords(true)" :disabled="recordsLoading">{{ recordsLoading ? t.loading : t.loadMore }}</button>
      </div>
    </dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import axios from 'axios'
import { analysisSnapshot, emptyAnalysis, dailySeries, recordsPage, appendRecords, safeHttpUrl, CATEGORY_KEYS } from './dashboard-data.js'

const lang = ref('zh'), light = ref(false)
const copy = {
  zh: {
    subtitle: '本地页面记录与实验性文本统计', dark: '深色', light: '浅色',
    records: '已保存记录 · 全部', refresh: '刷新记录', viewRecords: '查看记录', savedPages: '条已保存页面记录',
    recordLimit: '当前记录按页面去重，不能代表访问次数、阅读时长或完整浏览历史。',
    loading: '正在读取…', recordsError: '读取记录失败，请检查本地服务后重试。已有显示值来自上次读取。', updated: '读取时间：', stale: '数据可能已过时',
    analysis: '近 7 天文本统计', experimental: '实验功能', run: '运行实验分析', running: '分析中…',
    analysisLimit: '仅在点击后分析最近 7×24 小时的已保存记录。结果不用于判断信息茧房、心理状态或健康程度。',
    states: { not_requested: '尚未运行实验分析。原始记录可独立查看。', loading: '正在分析，旧图表已清空。', empty: '所选时间范围内没有记录。', insufficient_data: '所选范围样本不足，至少需要 {n} 条记录；未生成分析结论。', unavailable: '实验分析暂不可用，请检查分析依赖和配置。原始记录仍可查看。', failed: '实验分析失败，未生成有效结果。', request_error: '分析请求失败或响应格式不兼容，请检查服务后重试。', ready: '实验统计已生成。图表展示本次快照；新记录不会自动加入，请按需重新运行。' },
    snapshot: '快照生成时间：', window: '分析范围：', truncated: '范围内共有 {total} 条记录，本次仅读取按时间升序排列的前 {n} 条，不能代表全部记录。', coverage: '本次有效样本 {n} 条；时间范围内已保存记录共 {total} 条。',
    charts: '实验统计图表', categories: '实验分类分布', categoriesHint: '同一次分析的有效样本数；点击分类可切换趋势。',
    readingGuide: '如何理解这些数据', guide1: '“没有数据”和“比例为零”含义不同，缺失日期保留为空白。', guide2: '相似度只比较相邻文本，不代表全部重复访问或全部重复内容。', guide3: '文本的积极、中性、消极标签不代表用户情绪或心理状态。',
    selectedCategory: '趋势分类', allCategories: '全部分类', repetition: '相邻文本高相似比例', repetitionHint: '实验指标：相邻文本相似度 ≥ 0.85；日期按 UTC。', sentiment: '文本情感标签比例', sentimentHint: '使用实际标签比例；日期按 UTC。',
    positive: '积极', neutral: '中性', negative: '消极', noData: '暂无可展示的实验数据', missingMetric: '所选分类缺少有效指标',
    cats: { entertainment: '娱乐', learning: '学习', news: '新闻', social: '社交', shopping: '购物', tools: '工具', other: '其他' },
    close: '关闭', rawHint: '这里展示全部已保存的原始页面记录，与上方实验分类筛选独立。接口尚未提供逐条情感、访问次数或缓存状态。', noRecords: '尚无已保存记录。', untitled: '未命名页面', unknown: '未知', source: '来源', channel: '采集频道', plugin: '扩展', imported: '导入', loaded: '已显示 {n} / {total} 条记录', loadMore: '加载更多',
  },
  en: {
    subtitle: 'Local page records and experimental text statistics', dark: 'Dark', light: 'Light',
    records: 'Saved records · All', refresh: 'Refresh records', viewRecords: 'View records', savedPages: 'saved page records',
    recordLimit: 'Records are currently deduplicated by page. They do not measure visits, reading time, or complete browsing history.',
    loading: 'Loading…', recordsError: 'Could not read records. Check the local service and retry. Existing values are from the previous request.', updated: 'Read at:', stale: 'May be outdated',
    analysis: 'Text statistics · Last 7 days', experimental: 'Experimental', run: 'Run experimental analysis', running: 'Analyzing…',
    analysisLimit: 'Runs only on request, using saved records from the last 7×24 hours. These results do not assess echo chambers, mental state, or health.',
    states: { not_requested: 'Analysis has not been run. Raw records are available independently.', loading: 'Analyzing. Previous charts have been cleared.', empty: 'No records in this time window.', insufficient_data: 'At least {n} records are required in this window. No analysis was produced.', unavailable: 'Experimental analysis is unavailable. Check its dependencies and configuration. Raw records remain available.', failed: 'Experimental analysis failed. No valid result was produced.', request_error: 'Analysis request failed or the response is incompatible. Check the service and retry.', ready: 'This is an experimental snapshot. Run analysis again to include new records.' },
    snapshot: 'Snapshot generated:', window: 'Window:', truncated: 'The window contains {total} records. Only the first {n} in ascending timestamp order were read; this does not represent all records.', coverage: '{n} valid samples analyzed; {total} saved records in the window.',
    charts: 'Experimental charts', categories: 'Experimental categories', categoriesHint: 'Valid sample counts from this analysis. Click a category to filter trends.',
    readingGuide: 'Reading these statistics', guide1: 'Missing values are different from zero. Dates without data remain blank.', guide2: 'Similarity compares adjacent texts only; it does not measure all repeated visits or content.', guide3: 'Positive, neutral and negative text labels do not describe your mood or mental state.',
    selectedCategory: 'Trend category', allCategories: 'All categories', repetition: 'High adjacent-text similarity', repetitionHint: 'Experimental: adjacent-text similarity ≥ 0.85. Dates use UTC.', sentiment: 'Text sentiment proportions', sentimentHint: 'Actual label proportions. Dates use UTC.',
    positive: 'Positive', neutral: 'Neutral', negative: 'Negative', noData: 'No experimental data to display', missingMetric: 'No valid metric for this category',
    cats: { entertainment: 'Entertainment', learning: 'Learning', news: 'News', social: 'Social', shopping: 'Shopping', tools: 'Tools', other: 'Other' },
    close: 'Close', rawHint: 'All saved raw page records, independent of the experimental category filter. Per-record sentiment, visit counts and cache status are not provided.', noRecords: 'No saved records.', untitled: 'Untitled page', unknown: 'Unknown', source: 'Source', channel: 'Captured channel', plugin: 'Extension', imported: 'Import', loaded: 'Showing {n} of {total} records', loadMore: 'Load more',
  },
}
const t = computed(() => copy[lang.value])
watch(lang, value => { document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en' }, { immediate: true })
const api = axios.create({ baseURL: (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, ''), timeout: 30000 })
const records = reactive({ items: [], total: null, page: 0, pageSize: 50 })
const recordsLoading = ref(false), recordsError = ref(false), recordsUpdatedAt = ref(null)
const analysis = ref(emptyAnalysis()), category = ref('global'), drawerOpen = ref(false)
const recordsDialog = ref(null), recordsButton = ref(null)
const pieRef = ref(null), repeatRef = ref(null), sentimentRef = ref(null)
let recordController, analysisController, timer, pieChart, repeatChart, sentimentChart
let disposed = false
const analysisMessage = computed(() => (t.value.states[analysis.value.status] || t.value.states.request_error).replace('{n}', analysis.value.minimumRecords ?? '—'))
const categoryLabel = key => key === 'global' ? t.value.allCategories : (t.value.cats[key] || key)
const sourceLabel = source => source === 'plugin' ? t.value.plugin : source === 'import' ? t.value.imported : (source || t.value.unknown)
const formatTime = value => typeof value === 'number' && Number.isFinite(new Date(value).getTime()) ? new Date(value).toLocaleString(lang.value === 'zh' ? 'zh-CN' : 'en-GB') : t.value.unknown
const formatUtc = value => typeof value === 'number' && Number.isFinite(new Date(value).getTime()) ? new Date(value).toISOString().replace('T', ' ').slice(0, 16) : t.value.unknown

async function fetchRecords(append = false) {
  if (recordsLoading.value) return
  recordsLoading.value = true
  recordController = new AbortController()
  try {
    const response = await api.get('/items', { params: { page: append ? records.page + 1 : 1, page_size: 50 }, signal: recordController.signal })
    const page = recordsPage(response.data)
    Object.assign(records, { ...page, items: append ? appendRecords(records.items, page.items) : page.items })
    recordsUpdatedAt.value = Date.now()
    recordsError.value = false
  } catch (error) {
    if (!axios.isCancel(error)) recordsError.value = true
  } finally { recordsLoading.value = false }
}
async function runAnalysis() {
  if (analysis.value.status === 'loading') return
  analysis.value = emptyAnalysis('loading')
  category.value = 'global'
  analysisController = new AbortController()
  try {
    const response = await api.get('/dashboard/visualization', { params: { days: 7 }, signal: analysisController.signal })
    analysis.value = analysisSnapshot(response.data)
  } catch (error) {
    if (!axios.isCancel(error)) analysis.value = emptyAnalysis('request_error')
  }
}
function openRecords() { drawerOpen.value = true; recordsDialog.value.showModal(); fetchRecords() }
function closeRecords() { recordsDialog.value.close(); recordsButton.value?.focus() }
function closeOnBackdrop(event) {
  if (event.target !== recordsDialog.value) return
  const box = event.target.getBoundingClientRect()
  if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeRecords()
}
function updateCharts() {
  if (disposed || !pieChart) return
  const colors = ['#c784ef', '#48c9a3', '#efc35d', '#ef8292', '#a8a0ff', '#56b9e9', '#94a3b8']
  const text = light.value ? '#192b41' : '#edf4ff', line = light.value ? '#d3dce8' : '#30435e'
  const emptyGraphic = message => [{ type: 'text', left: 'center', top: 'center', style: { text: message, fill: text, fontSize: 14 } }]
  const isReady = analysis.value.status === 'ready'
  const pieData = Object.entries(analysis.value.counts).filter(([, count]) => count > 0).map(([key, count]) => ({ id: key, name: categoryLabel(key), value: count, itemStyle: { color: colors[Math.max(0, CATEGORY_KEYS.indexOf(key))] } }))
  pieChart.setOption({ textStyle: { color: text }, tooltip: { trigger: 'item', renderMode: 'richText' }, legend: { bottom: 0, textStyle: { color: text } }, graphic: pieData.length ? [] : emptyGraphic(t.value.noData), series: pieData.length ? [{ type: 'pie', radius: ['38%', '64%'], center: ['50%', '43%'], label: { show: false }, data: pieData }] : [] }, true)
  const data = analysis.value.series[category.value] || dailySeries()
  const options = (series, valid) => ({
    textStyle: { color: text }, tooltip: { trigger: 'axis', renderMode: 'richText' }, legend: { show: valid, top: 0, textStyle: { color: text } },
    grid: { left: 48, right: 20, top: 45, bottom: 58 },
    xAxis: { show: valid, type: 'category', data: data.dates, axisLabel: { color: text, rotate: 25 }, axisLine: { lineStyle: { color: line } } },
    yAxis: { show: valid, type: 'value', min: 0, max: 100, axisLabel: { color: text, formatter: '{value}%' }, splitLine: { lineStyle: { color: line } } },
    graphic: valid ? [] : emptyGraphic(isReady ? t.value.missingMetric : t.value.noData), series: valid ? series : [],
  })
  const asLine = (name, values, color) => ({ name, type: 'line', data: values, connectNulls: false, smooth: false, itemStyle: { color }, symbolSize: 7 })
  repeatChart.setOption(options([asLine(t.value.repetition, data.repeat, '#39bcd0')], data.repeat.some(value => value !== null)), true)
  sentimentChart.setOption(options([asLine(t.value.positive, data.positive, '#48c9a3'), asLine(t.value.neutral, data.neutral, '#94a3b8'), asLine(t.value.negative, data.negative, '#ef8292')], data.positive.some(value => value !== null)), true)
}
watch([analysis, category, lang, light], () => nextTick(updateCharts))
const resize = () => { pieChart?.resize(); repeatChart?.resize(); sentimentChart?.resize() }
onMounted(() => {
  pieChart = echarts.init(pieRef.value); repeatChart = echarts.init(repeatRef.value); sentimentChart = echarts.init(sentimentRef.value)
  pieChart.on('click', event => { if (event.data?.id) category.value = event.data.id })
  updateCharts(); fetchRecords(); window.addEventListener('resize', resize)
  timer = setInterval(() => { if (!drawerOpen.value) fetchRecords() }, 30000)
})
onUnmounted(() => {
  disposed = true; clearInterval(timer); recordController?.abort(); analysisController?.abort()
  window.removeEventListener('resize', resize); pieChart?.dispose(); repeatChart?.dispose(); sentimentChart?.dispose()
})
</script>

<style scoped>
.dashboard { --bg: #080f20; --card: #101c32; --text: #edf4ff; --muted: #afbdd1; --line: #30435e; --accent: #53d6e7; min-height: 100vh; box-sizing: border-box; padding: 28px; background: var(--bg); color: var(--text); font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
.dashboard.light { --bg: #eef2f7; --card: #fff; --text: #192b41; --muted: #526378; --line: #d3dce8; --accent: #006977; color-scheme: light; }
header, .heading, .controls { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
header { margin: 0 auto 24px; max-width: 1440px; } h1 { font-size: clamp(22px, 3vw, 32px); margin: 0; } h2 { font-size: 19px; margin: 0; }
p { line-height: 1.65; } .muted { color: var(--muted); font-size: 14px; } .controls { justify-content: flex-start; gap: 10px; }
button, select { font: inherit; color: var(--text); background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 9px 14px; } button { cursor: pointer; } button:hover { border-color: var(--accent); } button:disabled { opacity: .55; cursor: wait; } button:focus-visible, select:focus-visible, a:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; } .primary { border-color: var(--accent); color: var(--accent); }
.card { box-sizing: border-box; max-width: 1440px; margin: 0 auto 20px; padding: 24px; border: 1px solid var(--line); border-radius: 16px; background: var(--card); }
.total { display: flex; align-items: baseline; gap: 12px; margin: 20px 0 8px; } .total strong { font-size: 40px; color: var(--accent); } .tag { font-size: 12px; color: var(--accent); border: 1px solid var(--accent); border-radius: 6px; padding: 3px 7px; margin-left: 8px; vertical-align: middle; }
.notice { background: color-mix(in srgb, var(--accent) 8%, var(--card)); border-left: 3px solid var(--accent); padding: 12px 16px; } .error { border-left-color: #ee8d65; }
.chart-grid { max-width: 1440px; margin: auto; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; } .chart-grid .card { margin: 0; width: 100%; min-width: 0; } .chart { width: 100%; height: 300px; } .guide { color: var(--muted); padding-left: 22px; line-height: 1.9; } .guide li { margin-bottom: 18px; }
dialog { box-sizing: border-box; width: min(700px, 92vw); max-height: 86vh; padding: 24px; border: 1px solid var(--line); border-radius: 16px; background: var(--card); color: var(--text); } dialog::backdrop { background: #0009; } .record-list { padding: 0; list-style: none; } .record-list li { display: flex; flex-direction: column; gap: 8px; border-bottom: 1px solid var(--line); padding: 16px 0; overflow-wrap: anywhere; } .record-list time { font-size: 13px; color: var(--muted); } .record-list a { color: var(--accent); } .pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
@media (max-width: 760px) { .dashboard { padding: 16px; } .chart-grid { grid-template-columns: minmax(0, 1fr); } .card { padding: 18px; } .heading { align-items: flex-start; } }
</style>
