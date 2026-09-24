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
const artifacts = path.join(root, 'output/playwright/round3');
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
    body: JSON.stringify({ url: 'https://example.invalid/' + i, title: 'Synthetic page ' + i, text: 'Synthetic content', ts: Date.now(), source: 'plugin' }) })).status, 200);
  await command([path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'build', '--outDir', dist], {
    cwd: path.join(root, 'frontend'), env: { ...process.env, VITE_API_BASE_URL: api },
  });
  browser = await chromium.launch({ channel: 'chromium', headless: true });
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 1000 } });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(uiOrigin); await page.getByRole('heading', { name: 'Information Diet Manager' }).waitFor();
  assert.equal(await page.getByRole('button', { name: '查看记录', exact: true }).isDisabled(), true);
  await page.screenshot({ path: path.join(artifacts, 'disconnected.png'), fullPage: true });
  pass('production page renders disconnected; no private records are loaded');
  await page.getByTestId('admin-key').fill(collector); await page.getByTestId('connect').click();
  await page.getByText('连接失败：', { exact: false }).waitFor();
  assert.equal(await page.getByTestId('disconnect').count(), 0);
  pass('collector key cannot unlock the management UI');
  await page.getByTestId('admin-key').fill(admin); await page.getByTestId('connect').click();
  await until(async () => await page.locator('.total strong').textContent() === '2', 'paired records');
  assert.equal(await page.getByTestId('admin-key').count(), 0);
  assert.equal(await page.evaluate(key => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }).includes(key), admin), false);
  pass('admin key pairs, loads real records and is absent from browser storage and URL');

  const downloadPromise = page.waitForEvent('download'); await page.getByTestId('backup').click();
  const download = await downloadPromise, backupPath = path.join(temp, 'backup.json'); await download.saveAs(backupPath);
  const backup = JSON.parse(fs.readFileSync(backupPath, 'utf8')); assert.equal(backup.items.length, 2);
  assert.equal(fs.readFileSync(backupPath, 'utf8').includes(admin), false);
  await until(async () => !(await page.getByTestId('backup').isDisabled()), 'backup complete');
  await page.screenshot({ path: path.join(artifacts, 'connected.png'), fullPage: true });
  pass('authenticated backup downloads two page records without capability keys');
  await page.getByRole('button', { name: '查看记录', exact: true }).click();
  await page.getByRole('button', { name: '删除此记录', exact: true }).first().waitFor();
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', { name: '删除此记录', exact: true }).first().click();
  await until(async () => await page.locator('.total strong').textContent() === '1', 'single deletion');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  pass('record deletion updates both database and visible count');

  const damagedPath = path.join(temp, 'damaged.json'); fs.writeFileSync(damagedPath, JSON.stringify({ ...backup, sha256: '0'.repeat(64) }));
  await page.getByTestId('restore-file').setInputFiles(damagedPath); page.once('dialog', dialog => dialog.accept()); await page.getByTestId('restore').click();
  await until(async () => (await page.getByTestId('data-result').textContent()).includes('操作') && !(await page.getByTestId('restore').isDisabled()), 'damaged restore rejected');
  assert.equal((await (await fetch(api + '/items', { headers })).json()).total, 1);
  pass('damaged backup is rejected through real upload; current data survives');
  await page.getByTestId('restore-file').setInputFiles(backupPath); page.once('dialog', dialog => dialog.accept()); await page.getByTestId('restore').click();
  await until(async () => await page.locator('.total strong').textContent() === '2', 'restore count');
  pass('valid backup restores the page records through the UI');
  page.once('dialog', dialog => dialog.dismiss()); await page.getByTestId('delete-all').click();
  assert.equal((await (await fetch(api + '/items', { headers })).json()).total, 2);
  page.once('dialog', dialog => dialog.accept()); await page.getByTestId('delete-all').click();
  await until(async () => await page.locator('.total strong').textContent() === '0', 'clear all');
  pass('cancelled deletion preserves records; confirmed deletion empties the database');

  // Hold an earlier response across logout: it must not restore private data in the page.
  await page.route(api + '/items?**', async route => { await delay(500); await route.fulfill({ json: { page: 1, page_size: 50, total: 999, items: [] } }).catch(() => {}); });
  await page.getByRole('button', { name: '刷新记录', exact: true }).click();
  await page.getByTestId('disconnect').click(); await delay(700);
  assert.equal(await page.locator('.total strong').textContent(), '—');
  assert.equal(await page.getByTestId('backup').count(), 0);
  await page.reload(); assert.equal(await page.getByTestId('admin-key').inputValue(), '');
  pass('disconnect clears view and key; a stale response and reload cannot restore the session');
  await page.unroute(api + '/items?**');
  await page.getByTestId('admin-key').fill(admin); await page.getByTestId('connect').click();
  await until(async () => await page.locator('.total strong').textContent() === '0', 'reconnection');
  await page.route(api + '/items?**', route => route.fulfill({ status: 401, json: { detail: 'Local access key required' } }));
  await page.getByRole('button', { name: '刷新记录', exact: true }).click();
  await page.getByTestId('admin-key').waitFor();
  assert.equal(await page.locator('.total strong').textContent(), '—');
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
