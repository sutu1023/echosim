import { test, expect } from '@playwright/test';

test('normal SC-UI-01 SC-GATE-01 service baseline', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('服务已就绪')).toBeVisible();
  await expect(page.getByRole('button', { name: '重新检查' })).toBeEnabled();
  await expect(page.getByText('本阶段没有推演或模型调用，不需要 API Key。')).toBeVisible();
});
test('failure SC-UI-02 SC-UI-03 SC-UI-04 recover from failed request', async ({ page }) => {
  await page.route('**/health', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'DEPENDENCY_UNAVAILABLE', data: null }),
    }),
  );
  await page.goto('/');
  await expect(page.getByText('暂未就绪')).toBeVisible();
  await page.unroute('**/health');
  await page.getByRole('button', { name: '重新检查' }).click();
  await expect(page.getByText('服务已就绪')).toBeVisible();
});
test('failure SC-UI-05 unknown path has return route', async ({ page }) => {
  await page.goto('/not-a-page');
  await expect(page.getByRole('heading', { name: '页面不存在', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '返回运行档案' }).click();
  await expect(page.getByText('服务已就绪')).toBeVisible();
});
for (const width of [375, 768, 1280])
  test(`boundary SC-UI-06 layout ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await expect(page.getByText('服务已就绪')).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    if (width < 1024) {
      await page.getByRole('button', { name: '目录', exact: true }).click();
      await expect(page.getByRole('link', { name: '运行档案', exact: true })).toBeVisible();
    }
    await page.screenshot({
      path: `../artifacts/screenshots/${test.info().project.name}-${width}.png`,
      fullPage: true,
    });
  });
test('boundary SC-UI-06 keyboard reaches retry control', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('服务已就绪')).toBeVisible();
  const retry = page.getByRole('button', { name: '重新检查' });
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press('Tab');
    if (await retry.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(retry).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByText('服务已就绪')).toBeVisible();
});
