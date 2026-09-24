// Boundary between API payloads and chart values. Missing measurements stay null.
export const CATEGORY_KEYS = ['entertainment', 'learning', 'news', 'social', 'shopping', 'tools', 'other']

export function ratioPercent(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? Math.round(value * 1000000) / 10000
    : null
}

function dateNumber(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const ms = Date.parse(`${value}T00:00:00Z`)
  return Number.isFinite(ms) && new Date(ms).toISOString().slice(0, 10) === value ? ms : null
}

export function dailySeries(rows = [], window = {}) {
  const byDate = new Map(rows.filter(row => dateNumber(row.date) !== null).map(row => [row.date, row]))
  const suppliedDates = [...byDate.keys()].sort()
  const start = Number.isFinite(window.from_ts) ? dateNumber(new Date(window.from_ts).toISOString().slice(0, 10)) : dateNumber(suppliedDates[0])
  const end = Number.isFinite(window.to_ts) ? dateNumber(new Date(window.to_ts).toISOString().slice(0, 10)) : dateNumber(suppliedDates.at(-1))
  const dates = []
  if (start !== null && end !== null && end >= start && end - start <= 91 * 86400000) {
    for (let day = start; day <= end; day += 86400000) dates.push(new Date(day).toISOString().slice(0, 10))
  }
  const result = { dates, repeat: [], positive: [], neutral: [], negative: [] }
  for (const date of dates) {
    const row = byDate.get(date)
    const hasSamples = Number.isInteger(row?.count) && row.count > 0
    result.repeat.push(hasSamples ? ratioPercent(row.repeat_ratio) : null)
    const proportions = ['positive_ratio', 'neutral_ratio', 'negative_ratio'].map(key => row?.[key])
    const valid = hasSamples && proportions.every(value => ratioPercent(value) !== null)
      && Math.abs(proportions.reduce((sum, value) => sum + value, 0) - 1) <= 0.00001
    ;['positive', 'neutral', 'negative'].forEach((key, index) => {
      result[key].push(valid ? ratioPercent(proportions[index]) : null)
    })
  }
  return result
}

export function emptyAnalysis(status = 'not_requested') {
  return { status, window: {}, generatedAt: null, minimumRecords: null, counts: {}, series: {}, warning: false }
}

export function analysisSnapshot(payload) {
  const statuses = ['empty', 'insufficient_data', 'unavailable', 'failed', 'ready']
  if (!payload || !statuses.includes(payload.analysis_status) || !payload.window) {
    throw new Error('Unsupported analysis response')
  }
  const snapshot = {
    ...emptyAnalysis(payload.analysis_status), window: { ...payload.window },
    generatedAt: payload.generated_at, minimumRecords: payload.minimum_records,
    warning: Boolean(payload.pipeline_warning),
  }
  // A warning must never become a successful-looking measurement.
  if (snapshot.warning && snapshot.status === 'ready') snapshot.status = 'failed'
  if (snapshot.status !== 'ready') return snapshot
  if (!payload.category_counts || !Array.isArray(payload.global?.time_series)) {
    throw new Error('Incomplete analysis response')
  }
  for (const [key, count] of Object.entries(payload.category_counts)) {
    if (!Number.isInteger(count) || count < 0) throw new Error('Invalid category count')
    snapshot.counts[key] = count
  }
  snapshot.series.global = dailySeries(payload.global.time_series, payload.window)
  for (const [key, category] of Object.entries(payload.categories || {})) {
    snapshot.series[key] = dailySeries(category.time_series, payload.window)
  }
  return snapshot
}

export function recordsPage(payload) {
  if (!payload || !Array.isArray(payload.items) || !Number.isInteger(payload.total) || payload.total < 0
    || !Number.isInteger(payload.page) || payload.page < 1 || !Number.isInteger(payload.page_size) || payload.page_size < 1) {
    throw new Error('Invalid records response')
  }
  return { total: payload.total, page: payload.page, pageSize: payload.page_size, items: payload.items }
}

export function appendRecords(current, incoming) {
  return [...new Map([...current, ...incoming].map(item => [item.id, item])).values()]
}

export function safeHttpUrl(value) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}
