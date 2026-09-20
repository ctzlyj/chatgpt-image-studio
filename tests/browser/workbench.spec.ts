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
