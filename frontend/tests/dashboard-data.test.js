import test from 'node:test'
import assert from 'node:assert/strict'
import { dailySeries, ratioPercent, analysisSnapshot, emptyAnalysis, recordsPage, appendRecords, safeHttpUrl } from '../src/dashboard-data.js'

const day = (date, values = {}) => ({ date, count: 5, repeat_ratio: 0, positive_ratio: 1, negative_ratio: 0, neutral_ratio: 0, ...values })
const ready = () => ({ analysis_status: 'ready', window: { input_count: 5, processed_count: 5, available_count: 5 }, minimum_records: 5, generated_at: 1000, category_counts: { tools: 3, shopping: 2 }, global: { time_series: [day('2026-09-24')] }, categories: { tools: { time_series: [day('2026-09-24')] } } })

test('100% positive stays 100/0/0; actual zeroes are preserved', () => {
  const data = dailySeries([day('2026-09-24')])
  assert.deepEqual([data.positive, data.neutral, data.negative, data.repeat], [[100], [0], [0], [0]])
})
test('missing and invalid sentiment are unknown, not neutral or a default percentage', () => {
  for (const invalid of [undefined, null, -1, 2, '0', NaN]) {
    const result = dailySeries([day('2026-09-24', { negative_ratio: invalid })])
    assert.deepEqual([result.positive, result.neutral, result.negative], [[null], [null], [null]])
  }
  assert.deepEqual(dailySeries([day('2026-09-24', { neutral_ratio: 0.5 })]).positive, [null])
  assert.deepEqual(dailySeries([day('2026-09-24', { count: 0 })]).positive, [null])
})
test('aligns actual UTC dates and leaves missing dates blank', () => {
  const data = dailySeries([day('2026-09-24')], { from_ts: Date.parse('2026-09-22T23:30Z'), to_ts: Date.parse('2026-09-24T01:00Z') })
  assert.deepEqual(data.dates, ['2026-09-22', '2026-09-23', '2026-09-24'])
  assert.deepEqual(data.positive, [null, null, 100])
  assert.deepEqual(data.repeat, [null, null, 0])
})
test('sorts dates and rejects impossible dates without fabricating points', () => {
  assert.deepEqual(dailySeries([day('2026-02-30')]).dates, [])
  const data = dailySeries([day('2026-09-24'), day('2026-09-22')])
  assert.deepEqual(data.positive, [100, null, 100])
  assert.equal(ratioPercent(null), null)
})
test('tools and shopping remain separate categories from the same analysis', () => {
  assert.deepEqual(analysisSnapshot(ready()).counts, { tools: 3, shopping: 2 })
})
test('new snapshots replace absent categories rather than retaining stale series', () => {
  let state = analysisSnapshot(ready())
  assert.ok(state.series.tools)
  const next = ready(); next.categories = {}; next.category_counts = { other: 5 }
  state = analysisSnapshot(next)
  assert.equal(state.series.tools, undefined)
  state = analysisSnapshot({ ...next, analysis_status: 'empty' })
  assert.deepEqual(state.series, {})
  assert.deepEqual(state.counts, {})
})
test('failure, insufficient data and warnings never provide chart values', () => {
  for (const status of ['failed', 'unavailable', 'insufficient_data', 'empty']) {
    const state = analysisSnapshot({ ...ready(), analysis_status: status })
    assert.deepEqual(state.counts, {})
    assert.deepEqual(state.series, {})
  }
  assert.equal(analysisSnapshot({ ...ready(), pipeline_warning: 'dependency missing' }).status, 'failed')
  assert.equal(emptyAnalysis().status, 'not_requested')
})
test('incompatible responses fail visibly instead of being interpreted as empty', () => {
  assert.throws(() => analysisSnapshot({ global: { time_series: [] } }))
  assert.throws(() => analysisSnapshot({ ...ready(), category_counts: { tools: -1 } }))
  assert.throws(() => recordsPage({ items: [], total: null }))
})
test('pagination preserves real total and removes overlapping IDs', () => {
  const page = recordsPage({ items: [{ id: 2 }, { id: 1 }], page: 2, page_size: 50, total: 51 })
  assert.equal(page.total, 51)
  assert.equal(page.page, 2)
  assert.equal(page.pageSize, 50)
  assert.deepEqual(appendRecords([{ id: 3 }, { id: 2 }], page.items).map(x => x.id), [3, 2, 1])
  assert.equal(recordsPage({ items: [], total: 0, page: 1, page_size: 50 }).total, 0)
})
test('raw record links are restricted to HTTP(S)', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,x', 'file:///c:/test', '/relative', null]) assert.equal(safeHttpUrl(value), null)
  assert.equal(safeHttpUrl('https://example.invalid/page'), 'https://example.invalid/page')
})
