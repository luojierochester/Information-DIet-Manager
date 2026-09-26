/* Production UI + real protected API, temporary data and synthetic capability keys. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { chromium } = require('../../chrome-extension/node_modules/playwright');

const root = path.resolve(__dirname, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'idm-lifecycle-e2e-'));
const dist = path.join(temp, 'dist');
const artifacts = path.join(root, 'output/playwright/ui-restoration');
fs.mkdirSync(artifacts, { recursive: true });
const admin = randomBytes(32).toString('base64url'), collector = randomBytes(32).toString('base64url');
const headers = { Authorization: 'Bearer ' + admin };
let browser, backend, backendLog = '';
const checks = [], errors = [];
const pass = label => { checks.push(label); console.log('PASS ' + label); };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, name) {
  const started = Date.now();
  while (Date.now() - started < 15000) { if (await check()) return; await delay(100); }
  throw new Error('Timed out: ' + name);
}
async function listen(server) { await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); return server.address().port; }
async function close(server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
const ui = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  const filename = path.resolve(dist, '.' + decodeURIComponent(pathname === '/' ? '/index.html' : pathname));
  if (!filename.startsWith(dist + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) { response.writeHead(404); response.end(); return; }
  response.setHeader('Content-Type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : filename.endsWith('.svg') ? 'image/svg+xml' : 'text/html');
  response.end(fs.readFileSync(filename));
});
function command(args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, { windowsHide: true, ...options });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; }); child.stderr.on('data', chunk => { output += chunk; });
    child.on('error', reject); child.on('exit', code => code === 0 ? resolve() : reject(new Error(output)));
  });
}
async function run() {
  const uiPort = await listen(ui), uiOrigin = `http://127.0.0.1:${uiPort}`;
  const probe = http.createServer(); const apiPort = await listen(probe); await close(probe);
  const api = `http://127.0.0.1:${apiPort}`;
  backend = spawn(process.env.IDM_TEST_PYTHON || 'python', ['-m', 'uvicorn', 'src.backend_api.app:app', '--host', '127.0.0.1', '--port', String(apiPort)], {
    cwd: root, windowsHide: true, env: { ...process.env, IDM_DB_PATH: path.join(temp, 'synthetic.sqlite3'), IDM_ADMIN_TOKEN: admin,
      IDM_COLLECTOR_TOKEN: collector, IDM_FRONTEND_ORIGINS: uiOrigin, HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', PYTHONUTF8: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout.on('data', data => { backendLog += data; }); backend.stderr.on('data', data => { backendLog += data; });
  backend.on('error', error => { backendLog += error.message; });
  await until(async () => { try { return (await fetch(api + '/health')).ok; } catch { return false; } }, 'backend');
  for (let i = 0; i < 2; i++) assert.equal((await fetch(api + '/collect', { method: 'POST', headers: { Authorization: 'Bearer ' + collector, 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: 'https://example.invalid/' + i, title: 'Synthetic page ' + i, text: 'Synthetic content', tags: ['W'.repeat(40)], ts: Date.now(), source: 'plugin' }) })).status, 200);
  await command([path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'build', '--outDir', dist], {
    cwd: path.join(root, 'frontend'), env: { ...process.env, VITE_API_BASE_URL: api },
  });
  browser = await chromium.launch({ channel: 'chromium', headless: true });
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 1000 } });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(uiOrigin); await page.locator('.tech-header h1').waitFor();
  const settingsPanel = page.locator('.settings-panel');
  const savedTotal = page.getByTestId('saved-total');
  const showSettings = async () => {
    if (!await settingsPanel.isVisible()) await page.getByTestId('settings-toggle').click();
    await settingsPanel.waitFor({ state: 'visible' });
  };
  const hideSettings = async () => {
    if (await settingsPanel.isVisible()) await page.getByTestId('settings-toggle').click();
    await settingsPanel.waitFor({ state: 'hidden' });
  };
  assert.equal(await page.getByTestId('records-toggle').isDisabled(), true);
  assert.equal(await savedTotal.textContent(), '—');
  assert.equal(await settingsPanel.isVisible(), false);
  assert.equal(await page.locator('.dashboard-grid > .tech-card').count(), 4);
  assert.equal(await page.locator('.dashboard-grid .chart-container').count(), 4);
  const chartBounds = await page.locator('.dashboard-grid > .tech-card').evaluateAll(cards => cards.map(card => {
    const rect = card.getBoundingClientRect(); return { x: rect.x, y: rect.y };
  }));
  assert.ok(chartBounds[0].x < chartBounds[1].x && Math.abs(chartBounds[0].y - chartBounds[1].y) < 2);
  assert.ok(chartBounds[2].y > chartBounds[0].y && Math.abs(chartBounds[2].x - chartBounds[0].x) < 2);
  await page.screenshot({ path: path.join(artifacts, 'disconnected.png'), fullPage: true });
  pass('production page renders disconnected; no private records are loaded');

  const gridBeforeSettings = await page.locator('.dashboard-grid').boundingBox();
  await showSettings();
  assert.equal(await settingsPanel.getByTestId('admin-key').count(), 1);
  await hideSettings();
  assert.equal(await page.getByTestId('admin-key').isVisible(), false);
  const gridAfterSettings = await page.locator('.dashboard-grid').boundingBox();
  assert.deepEqual(gridAfterSettings, gridBeforeSettings);
  pass('original four-chart cyber dashboard remains in place; settings toggle does not relayout the home page');

  await showSettings();
  const language = settingsPanel.locator('#ui-language');
  const font = settingsPanel.locator('#ui-font');
  const color = settingsPanel.locator('#ui-color');
  await color.evaluate(input => { input.value = '#19d8ff'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  await until(async () => await page.locator('.dashboard-container').evaluate(element => getComputedStyle(element).getPropertyValue('--primary-color').trim()) === '#19d8ff', 'custom theme color');
  await font.selectOption("'Courier New', Courier, monospace");
  await until(async () => (await page.locator('.dashboard-container').evaluate(element => getComputedStyle(element).fontFamily)).includes('Courier'), 'custom font');
  await language.selectOption('en');
  await settingsPanel.getByRole('heading', { name: 'System UI Engine', exact: true }).waitFor();
  await language.selectOption('zh');
  await settingsPanel.getByTestId('theme-toggle').click();
  await until(async () => await page.locator('.dashboard-container.light-mode').count() === 1, 'light mode');
  await hideSettings();
  await page.screenshot({ path: path.join(artifacts, 'light-home.png'), fullPage: true });
  await showSettings();
  await settingsPanel.getByTestId('theme-toggle').click();
  await until(async () => await page.locator('.dashboard-container.light-mode').count() === 0, 'dark mode');
  await color.evaluate(input => { input.value = '#00f3ff'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  await font.selectOption("'Segoe UI', 'Microsoft YaHei', sans-serif");
  await settingsPanel.evaluate(element => { element.scrollTop = 0; });
  await delay(600); // Let the original theme transition finish before the visual review screenshot.
  await page.screenshot({ path: path.join(artifacts, 'original-settings.png'), fullPage: true });
  pass('original theme color, font, language and light/dark controls still update the dashboard');

  await page.getByTestId('admin-key').fill(collector); await page.getByTestId('connect').click();
  await page.getByText('连接失败：', { exact: false }).waitFor();
  assert.equal(await page.getByTestId('disconnect').count(), 0);
  pass('collector key cannot unlock the management UI');
  await page.getByTestId('admin-key').fill(admin); await page.getByTestId('connect').click();
  await until(async () => await savedTotal.textContent() === '2', 'paired records');
  assert.equal(await page.getByTestId('admin-key').count(), 0);
  assert.equal(await page.evaluate(key => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }).includes(key), admin), false);
  pass('admin key pairs, loads real records and is absent from browser storage and URL');
  const managementControls = ['disconnect', 'backup', 'restore-file', 'restore', 'delete-all', 'refresh-records', 'delete-record-id', 'delete-record'];
  for (const control of managementControls) {
    assert.equal(await settingsPanel.getByTestId(control).count(), 1);
  }
  await hideSettings();
  for (const control of managementControls) {
    assert.equal(await page.getByTestId(control).isVisible(), false);
  }
  await page.screenshot({ path: path.join(artifacts, 'connected-home.png'), fullPage: true });
  await showSettings();
  pass('connection and maintenance controls are contained in the original settings panel and hidden on the home page');

  // Synthetic analysis response checks rendering only. No model inference is
  // exercised by this fixture; lifecycle requests below still use the real API.
  const fixtureCategories = ['entertainment', 'learning', 'news', 'social', 'shopping', 'tools', 'other'];
  const fixtureRows = count => [
    { date: '2026-09-22', count, repeat_ratio: 0, positive_ratio: 0.5, neutral_ratio: 0.5, negative_ratio: 0 },
    { date: '2026-09-24', count, repeat_ratio: 0.25, positive_ratio: 0, neutral_ratio: 0.75, negative_ratio: 0.25 },
  ];
  const visualizationFixture = {
    analysis_status: 'ready', minimum_records: 5, generated_at: Date.UTC(2026, 8, 24, 23, 59),
    window: { from_ts: Date.UTC(2026, 8, 22), to_ts: Date.UTC(2026, 8, 24, 23, 59), input_count: 56, available_count: 56, processed_count: 56, truncated: false },
    category_counts: Object.fromEntries(fixtureCategories.map(key => [key, 8])),
    global: { time_series: fixtureRows(28) },
    categories: Object.fromEntries(fixtureCategories.map(key => [key, { time_series: fixtureRows(4) }])),
  };
  const canvasImages = () => page.locator('.chart-container').evaluateAll(elements => elements.map(element => element.querySelector('canvas')?.toDataURL() || null));
  const emptyCanvases = await canvasImages();
  await page.route(api + '/dashboard/visualization?**', route => route.fulfill({ json: visualizationFixture }));
  await hideSettings(); await page.getByTestId('run-analysis').click();
  await until(async () => (await page.getByTestId('analysis-status').textContent()).includes('实验统计已生成'), 'fixture analysis rendering');
  await until(async () => (await canvasImages()).every((image, index) => image && image !== emptyCanvases[index]), 'all four charts paint the supplied fixture');
  assert.equal(await page.locator('.chart-container canvas').count(), 4);
  assert.equal(await page.locator('.dashboard-grid h2').count(), 4);
  await delay(1000);
  await page.screenshot({ path: path.join(artifacts, 'fixture-ready-charts.png'), fullPage: true });
  await page.unroute(api + '/dashboard/visualization?**');
  await showSettings();
  pass('synthetic analysis fixture renders all four charts with seven categories, zero values and a missing date; this does not verify model inference');

  const downloadPromise = page.waitForEvent('download'); await page.getByTestId('backup').click();
  const download = await downloadPromise, backupPath = path.join(temp, 'backup.json'); await download.saveAs(backupPath);
  const backup = JSON.parse(fs.readFileSync(backupPath, 'utf8')); assert.equal(backup.items.length, 2);
  assert.equal(fs.readFileSync(backupPath, 'utf8').includes(admin), false);
  await until(async () => !(await page.getByTestId('backup').isDisabled()), 'backup complete');
  await page.screenshot({ path: path.join(artifacts, 'connected.png'), fullPage: true });
  pass('authenticated backup downloads two page records without capability keys');
  await hideSettings();
  const originalBodyOverflow = await page.evaluate(() => document.body.style.overflow);
  await page.getByTestId('records-toggle').click();
  const drawer = page.locator('.drawer-panel');
  await drawer.waitFor({ state: 'visible' });
  await until(async () => {
    const bounds = await drawer.boundingBox();
    return bounds && bounds.x > 640 && Math.abs(bounds.x + bounds.width - 1280) < 2 && Math.abs(bounds.y) < 2;
  }, 'right-side drawer layout after slide transition');
  assert.equal(await drawer.evaluate(element => getComputedStyle(element).position), 'fixed');
  assert.equal(await drawer.locator('.cyber-tag').count(), 1);
  assert.equal(await drawer.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true, 'A maximum-length unbroken tag must not overflow the drawer');
  const lockedScrollY = await page.evaluate(() => scrollY);
  await page.mouse.move(10, 500); await page.mouse.wheel(0, 500); await delay(250);
  assert.equal(await page.evaluate(() => scrollY), lockedScrollY, 'Scrolling the overlay must not move the background page');
  assert.equal(await drawer.evaluate(element => document.activeElement === element), true);
  await page.keyboard.press('Tab');
  assert.equal(await page.getByTestId('drawer-close').evaluate(element => document.activeElement === element), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.getByTestId('record-row').locator('a').last().evaluate(element => document.activeElement === element), true);
  await page.keyboard.press('Tab');
  assert.equal(await page.getByTestId('drawer-close').evaluate(element => document.activeElement === element), true);
  await page.keyboard.press('Escape'); await drawer.waitFor({ state: 'hidden' });
  assert.equal(await page.getByTestId('records-toggle').evaluate(element => document.activeElement === element), true);
  assert.equal(await page.evaluate(() => document.body.style.overflow), originalBodyOverflow);
  await page.getByTestId('records-toggle').click(); await drawer.waitFor({ state: 'visible' });
  await delay(500);
  await drawer.hover({ position: { x: 30, y: 100 } });
  await delay(350);
  const hoveredBounds = await drawer.boundingBox();
  assert.ok(Math.abs(hoveredBounds.y) < 2 && Math.abs(hoveredBounds.x + hoveredBounds.width - 1280) < 2, 'Hover must not translate the fixed drawer');
  await drawer.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: path.join(artifacts, 'records-drawer-top.png'), fullPage: true });
  pass('drawer traps keyboard focus, Escape restores focus and body scrolling, and long tags and overlay scrolling stay contained');
  await page.getByTestId('drawer-close').click();
  await page.waitForFunction(() => {
    const element = document.querySelector('.drawer-panel');
    return element && new DOMMatrixReadOnly(getComputedStyle(element).transform).m41 > 1;
  }, undefined, { timeout: 1000, polling: 'raf' });
  await drawer.waitFor({ state: 'hidden' });
  assert.equal(await page.getByTestId('records-toggle').evaluate(element => document.activeElement === element), true);
  pass('hover leaves the drawer fixed and does not suppress its rightward exit animation');
  await page.getByTestId('records-toggle').click();
  await drawer.waitFor({ state: 'visible' });
  await page.locator('.drawer-overlay').click({ position: { x: 10, y: 10 } });
  await drawer.waitFor({ state: 'hidden' });
  assert.equal(await page.getByTestId('records-toggle').evaluate(element => document.activeElement === element), true);
  await page.getByTestId('records-toggle').click();
  await drawer.waitFor({ state: 'visible' });
  pass('records open in the original fixed right-side drawer; close button and backdrop both dismiss it');
  assert.equal(await drawer.getByTestId('delete-record').count(), 0);
  await page.getByTestId('drawer-close').click();
  await drawer.waitFor({ state: 'hidden' });
  await showSettings();
  const recordToDelete = (await (await fetch(api + '/items', { headers })).json()).items[0];
  await page.getByTestId('delete-record-id').selectOption(String(recordToDelete.id));
  page.once('dialog', dialog => dialog.accept()); await page.getByTestId('delete-record').click();
  await until(async () => await savedTotal.textContent() === '1', 'single deletion');
  const remainingRecords = await (await fetch(api + '/items', { headers })).json();
  assert.equal(remainingRecords.total, 1);
  assert.equal(remainingRecords.items.some(item => item.id === recordToDelete.id), false);
  pass('record deletion in settings removes the chosen real ID and updates both database and visible count');

  const damagedPath = path.join(temp, 'damaged.json'); fs.writeFileSync(damagedPath, JSON.stringify({ ...backup, sha256: '0'.repeat(64) }));
  await page.getByTestId('restore-file').setInputFiles(damagedPath); page.once('dialog', dialog => dialog.accept()); await page.getByTestId('restore').click();
  await until(async () => (await page.getByTestId('data-result').textContent()).includes('操作') && !(await page.getByTestId('restore').isDisabled()), 'damaged restore rejected');
  assert.equal((await (await fetch(api + '/items', { headers })).json()).total, 1);
  pass('damaged backup is rejected through real upload; current data survives');
  await page.getByTestId('restore-file').setInputFiles(backupPath); page.once('dialog', dialog => dialog.accept()); await page.getByTestId('restore').click();
  await until(async () => await savedTotal.textContent() === '2', 'restore count');
  pass('valid backup restores the page records through the UI');

  // A second page must come from the protected API, not a local UI fixture.
  for (let i = 2; i < 53; i++) {
    const response = await fetch(api + '/collect', { method: 'POST', headers: { Authorization: 'Bearer ' + collector, 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: 'https://example.invalid/' + i, title: 'Synthetic page ' + i, text: 'Synthetic pagination content', ts: Date.now(), source: 'plugin' }) });
    assert.equal(response.status, 200); assert.equal((await response.json()).inserted, 1);
  }
  await page.getByTestId('refresh-records').click();
  await until(async () => await savedTotal.textContent() === '53', 'pagination seed count');
  await hideSettings();
  await page.getByTestId('records-toggle').click();
  await until(async () => await page.getByTestId('record-row').count() === 50, 'first drawer page');
  await page.getByTestId('load-more').click();
  await until(async () => await page.getByTestId('record-row').count() === 53, 'second drawer page');
  assert.equal(await page.getByTestId('load-more').count(), 0);
  const recordUrls = await page.getByTestId('record-row').locator('a').evaluateAll(links => links.map(link => link.href));
  assert.equal(recordUrls.length, 53); assert.equal(new Set(recordUrls).size, 53);
  await page.screenshot({ path: path.join(artifacts, 'records-drawer.png'), fullPage: true });
  await page.getByTestId('drawer-close').click(); await drawer.waitFor({ state: 'hidden' });
  await showSettings();
  pass('right-side drawer loads both real API pages, displays all 53 records once and removes the completed pagination action');

  const olderRecord = (await (await fetch(api + '/items?page=2&page_size=50', { headers })).json()).items.at(-1);
  assert.ok(olderRecord, 'The deletion target must come from the second real API page');
  await page.getByTestId('delete-record-id').selectOption(String(olderRecord.id));
  await delay(31000); // Cross a real 30-second polling interval without mocking the browser clock.
  assert.equal(await page.getByTestId('delete-record-id').inputValue(), String(olderRecord.id));
  assert.equal(await page.getByTestId('delete-record-id').locator('option').count(), 54);
  page.once('dialog', dialog => dialog.accept()); await page.getByTestId('delete-record').click();
  await until(async () => await savedTotal.textContent() === '52', 'second-page record deletion');
  const afterOlderDeletion = await (await fetch(api + '/items?page=2&page_size=50', { headers })).json();
  assert.equal(afterOlderDeletion.total, 52);
  assert.equal(afterOlderDeletion.items.some(item => item.id === olderRecord.id), false);
  pass('second-page selection survives a real background polling interval and can be deleted from settings');

  page.once('dialog', dialog => dialog.dismiss()); await page.getByTestId('delete-all').click();
  assert.equal((await (await fetch(api + '/items', { headers })).json()).total, 52);
  page.once('dialog', dialog => dialog.accept()); await page.getByTestId('delete-all').click();
  await until(async () => await savedTotal.textContent() === '0', 'clear all');
  pass('cancelled deletion preserves records; confirmed deletion empties the database');

  // Hold an earlier response across logout: it must not restore private data in the page.
  await page.route(api + '/items?**', async route => { await delay(500); await route.fulfill({ json: { page: 1, page_size: 50, total: 999, items: [] } }).catch(() => {}); });
  await page.getByTestId('refresh-records').click();
  await page.getByTestId('disconnect').click(); await delay(700);
  assert.equal(await savedTotal.textContent(), '—');
  assert.equal(await page.getByTestId('backup').count(), 0);
  await page.reload(); await showSettings(); assert.equal(await page.getByTestId('admin-key').inputValue(), '');
  pass('disconnect clears view and key; a stale response and reload cannot restore the session');
  await page.unroute(api + '/items?**');
  await page.getByTestId('admin-key').fill(admin); await page.getByTestId('connect').click();
  await until(async () => await savedTotal.textContent() === '0', 'reconnection');
  await page.route(api + '/items?**', route => route.fulfill({ status: 401, json: { detail: 'Local access key required' } }));
  await page.getByTestId('refresh-records').click();
  await page.getByTestId('admin-key').waitFor();
  assert.equal(await savedTotal.textContent(), '—');
  pass('server authentication rejection removes the local management session and visible data');
  await page.screenshot({ path: path.join(artifacts, 'final.png'), fullPage: true });
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(artifacts, 'verification.json'), JSON.stringify({ browser: browser.version(), checks, temporaryData: true }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, artifacts }, null, 2));
}
run().catch(error => { console.error(error); console.error(backendLog.slice(-2500)); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (backend && backend.exitCode === null) backend.kill();
  await close(ui);
});
