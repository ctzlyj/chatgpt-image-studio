import { test, expect } from '@playwright/test';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1xoAAAAASUVORK5CYII=', 'base64');

test('plain generation, history, download, original preview and edit', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '自定义生图', exact: true })).toBeVisible();
  await page.getByLabel('你的提示词').fill('浏览器测试：白色陶瓷杯');
  await page.getByRole('button', { name: '发送内容预览' }).click();
  await expect(page.getByRole('dialog', { name: '发送内容预览' }).locator('pre')).toHaveText('浏览器测试：白色陶瓷杯');
  await page.getByRole('button', { name: '关闭预览' }).click();
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.card-prompt').first()).toHaveText('浏览器测试：白色陶瓷杯');
  await expect(page.locator('.result-card.success').first()).toBeVisible();
  await page.locator('.image-button').first().click();
  await page.getByRole('button', { name: '原尺寸查看' }).click();
  await expect(page.locator('.lightbox-canvas img')).toHaveJSProperty('clientWidth', 512);
  const imageDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载原图' }).click();
  expect((await imageDownload).suggestedFilename()).toContain('原图');
  await page.getByRole('button', { name: '关闭大图' }).click();
  await page.reload();
  await expect(page.locator('.result-card.success').first()).toBeVisible();
  await page.getByRole('button', { name: '继续修改', exact: true }).first().click();
  await page.getByLabel('修改要求').fill('背景改成浅灰色');
  await page.getByRole('button', { name: '发送内容预览' }).click();
  await expect(page.locator('.prompt-preview pre')).toContainText('[商品保真规则]');
  await page.getByRole('button', { name: '关闭预览' }).click();
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.card-prompt').first()).toHaveText('背景改成浅灰色');
  await expect(page.locator('.result-card.success').first()).toBeVisible();
  const zipDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '整批下载' }).click();
  expect((await zipDownload).suggestedFilename()).toContain('任务清单');
  expect(errors).toEqual([]);
});

test('queue preserves partial successes and overflow is rejected without locking form', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '提示词队列', exact: true }).click();
  await page.getByLabel('你的提示词').fill(Array(11).fill('task').join('\n'));
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.getByRole('alert')).toContainText('最多 10');
  await expect(page.getByRole('button', { name: '开始生成' })).toBeEnabled();
  await page.getByLabel('你的提示词').fill('first\nFAIL_FIXTURE\nlast');
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.result-card.success')).toHaveCount(2);
  await expect(page.locator('.result-card.failed')).toHaveCount(1);
});

test('file and clipboard references, per-image mode, settings and mobile layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '逐图修改', exact: true }).click();
  await page.getByLabel('参考图文件', { exact: true }).setInputFiles({ name: 'product.png', mimeType: 'image/png', buffer: png });
  await expect(page.locator('.reference').first()).toBeVisible();
  await page.getByRole('group', { name: '通用参考图上传区', exact: true }).evaluate((element, encoded) => {
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'pasted-style.png', { type: 'image/png' }));
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, png.toString('base64'));
  await expect(page.locator('.reference')).toHaveCount(2);
  await page.getByLabel('你的提示词').fill('保持商品不变，替换背景');
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.card-prompt').first()).toHaveText('保持商品不变，替换背景');
  await expect(page.locator('.result-card.success').first()).toBeVisible();
  await page.getByRole('button', { name: '已配置连接' }).click();
  await expect(page.getByRole('dialog', { name: '连接设置' })).toBeVisible();
  await expect(page.getByLabel('网页登录凭证', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: '检查已保存连接' }).click();
  await expect(page.locator('.settings-message')).toContainText('Test fixture');
  await page.getByRole('button', { name: '关闭连接设置' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test('lost submit receipt survives page reload without duplicate submission', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('你的提示词').fill('receipt-recovery-fixture');
  let submissions = 0;
  await page.route('**/api/batches**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/batches') {
      submissions++;
      await route.fetch();
      await route.abort();
    } else if (path.startsWith('/api/batches/')) {
      await route.abort();
    } else { await route.continue(); }
  });
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.pending-notice')).toBeVisible();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.unroute('**/api/batches**');
  await page.reload();
  await expect(page.locator('.pending-notice')).toBeVisible();
  await expect(page.getByRole('button', { name: '开始生成' })).toBeDisabled();
  await page.getByRole('button', { name: '查询回执' }).click();
  await expect(page.locator('.pending-notice')).toHaveCount(0);
  await expect(page.locator('.card-prompt').first()).toHaveText('receipt-recovery-fixture');
  expect(submissions).toBe(1);
});

test('connection accepts a pasted whole multiline session JSON without storing profile data in browser', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '已配置连接' }).click();
  const input = page.getByLabel('网页登录凭证', { exact: true });
  await expect(input).toHaveAttribute('type', 'password');
  const session = JSON.stringify({ user: { name: 'PRIVATE_BROWSER_FIXTURE', email: 'fixture@example.test' }, expires: '2099-01-01', accessToken: 'synthetic-browser-session-fixture' }, null, 2);
  await input.evaluate((element, text) => {
    const transfer = new DataTransfer();
    transfer.setData('text/plain', text);
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, session);
  const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/settings') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '保存设置', exact: true }).click();
  const response = await savedResponse;
  expect(response.request().postDataJSON().access_token).toBe(session);
  expect(response.status()).toBe(200);
  expect(await response.text()).not.toContain('synthetic-browser-session-fixture');
  await expect(input).toHaveValue('');
  await expect(page.locator('.settings-message')).toContainText('已保存');
  const browserStorage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(browserStorage).not.toContain('PRIVATE_BROWSER_FIXTURE');
  expect(browserStorage).not.toContain('synthetic-browser-session-fixture');
  await page.reload();
  await page.getByRole('button', { name: '已配置连接' }).click();
  await expect(page.getByLabel('网页登录凭证', { exact: true })).toHaveValue('');
});

test('account pool imports full JSON, refreshes, edits, disables, backs up and restores', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '号池管理', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '号池管理' })).toBeVisible();
  await page.getByRole('button', { name: '导入账号', exact: true }).click();
  await page.getByLabel('导入账号备注').fill('浏览器号池回归');
  const payload = JSON.stringify({ accessToken: 'synthetic-pool-browser-fixture', user: { name: 'PRIVATE_IMPORT_PROFILE' } }, null, 2);
  await page.getByLabel('号池导入内容').evaluate((element, text) => {
    const transfer = new DataTransfer(); transfer.setData('text/plain', text);
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, payload);
  await page.getByRole('button', { name: '导入粘贴内容' }).click();
  await expect(page.getByRole('status')).toContainText('已新增 1');
  await page.getByLabel('搜索账号').fill('浏览器号池回归');
  const row = page.locator('.account-table tbody tr');
  await expect(row).toHaveCount(1);
  await page.getByLabel('全选当前筛选账号').check();
  await page.getByRole('button', { name: '刷新选中' }).click();
  await expect(row).toContainText('synthetic@example.test');
  await expect(row).toContainText('可用');
  await row.getByRole('button', { name: '编辑', exact: true }).click();
  await page.getByLabel('编辑账号名称').fill('浏览器号池回归-已编辑');
  await page.getByRole('button', { name: '保存账号', exact: true }).click();
  await expect(row).toContainText('浏览器号池回归-已编辑');
  await page.getByRole('button', { name: '停用', exact: true }).click();
  await expect(row).toContainText('已停用');
  await page.getByLabel('全选当前筛选账号').check();
  await page.getByRole('button', { name: '加密导出', exact: true }).click();
  await page.getByLabel('备份口令', { exact: true }).fill('synthetic-browser-backup-password');
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出选中账号', exact: true }).click();
  const download = await downloadEvent;
  const backupPath = await download.path();
  expect(backupPath).toBeTruthy();
  await page.getByRole('button', { name: '关闭备份', exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除选中', exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole('button', { name: '恢复备份', exact: true }).click();
  await page.getByLabel('备份口令', { exact: true }).fill('synthetic-browser-backup-password');
  await page.getByLabel('恢复备份文件').setInputFiles(backupPath!);
  await page.getByRole('button', { name: '恢复到号池' }).click();
  await expect(page.getByRole('status')).toContainText('已恢复 1');
  await page.getByLabel('搜索账号').fill('');
  await expect(page.locator('.account-table tbody tr')).not.toHaveCount(0);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toContain('synthetic-pool-browser-fixture');
  expect(storage).not.toContain('PRIVATE_IMPORT_PROFILE');
  await page.screenshot({ path: 'test-results/account-pool-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: '导入账号', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/account-pool-mobile.png', fullPage: true });
  await page.getByRole('button', { name: '关闭号池管理' }).click();
  await page.reload();
  await page.getByRole('button', { name: '号池管理', exact: true }).click();
  await expect(page.locator('.account-table tbody tr')).not.toHaveCount(0);
});

test('remote CPA and sub2api configuration lists and imports through the backend', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '号池管理', exact: true }).click();
  await page.getByRole('button', { name: 'CPA / sub2api 导入' }).click();
  for (const kind of ['cpa', 'sub2api']) {
    await page.getByLabel('导入服务类型').selectOption(kind);
    await page.getByLabel('服务器名称', { exact: true }).fill(`回归-${kind}`);
    await page.getByLabel('服务器地址', { exact: true }).fill('https://example.test');
    await page.getByLabel('服务器管理密钥').fill('synthetic-management-key');
    await page.getByRole('button', { name: '保存服务器', exact: true }).click();
    await expect(page.getByLabel('服务器管理密钥')).toHaveValue('');
    const server = page.locator('.source-list > div').filter({ hasText: `回归-${kind}` });
    await server.getByRole('button', { name: '读取账号列表' }).click();
    await expect(page.locator('.remote-items > label')).toHaveCount(1);
    await page.getByLabel('全选远程筛选结果').check();
    await page.getByRole('button', { name: '导入选中（1）' }).click();
    await expect(page.locator('.import-job').last()).toContainText('已完成', { timeout: 15000 });
  }
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toContain('synthetic-management-key');
  await page.getByRole('button', { name: /^账号与调度/ }).click();
  await expect(page.getByRole('dialog', { name: '号池管理' })).toBeVisible();
});

test('the interface states the real output size and labels enlarged deliveries', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('画布比例').selectOption('1:1');
  await expect(page.locator('.dimension-note').first()).toContainText('1254 × 1254');
  await expect(page.locator('.control-row').last().locator('.task-counter')).toContainText('1254 × 1254');
  await expect(page.locator('.control-row').last().locator('.task-counter')).toContainText('原生输出');

  await page.getByLabel('交付放大').selectOption('2');
  await expect(page.locator('.control-row').last().locator('.task-counter')).toContainText('2508 × 2508');
  await expect(page.locator('.control-row').last().locator('.task-counter')).toContainText('放大后交付');
  await expect(page.locator('.dimension-note').last()).toContainText('非原生像素');

  await page.getByLabel('你的提示词').fill('浏览器测试：交付放大标注');
  await page.getByRole('button', { name: '开始生成' }).click();
  await expect(page.locator('.result-card.success').first()).toBeVisible();
  await page.locator('.image-button').first().click();
  const enlarged = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 2× 放大' }).click();
  expect((await enlarged).suggestedFilename()).toContain('非原生像素');
  const nativeDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载原图' }).click();
  expect((await nativeDownload).suggestedFilename()).toContain('原图');
});
