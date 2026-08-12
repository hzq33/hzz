/**
 * Playwright smoke（新 Aurora UI）—— UI shell + 页面导航（mock API）。
 */
import { expect, test } from '@playwright/test';

import { mockAgentApi } from './mockApi';

test.beforeEach(async ({ page }) => {
  await mockAgentApi(page);
});

test.describe('SPA smoke（Aurora 新设计）', () => {
  test('首页对话壳渲染（主题/侧栏/输入）', async ({ page }) => {
    await page.goto('/#/');
    await expect(page.getByText('Aurora Agent').first()).toBeVisible();
    await expect(page.getByText('通用助手').first()).toBeVisible();
    await expect(page.getByPlaceholder(/输入消息/)).toBeVisible();
    // 深/浅主题切换按钮存在
    await expect(page.getByTitle(/切换到/)).toBeVisible();
  });

  test('侧栏导航可达全部 6 个模块', async ({ page }) => {
    await page.goto('/#/');
    const navs: Array<[string, string]> = [
      ['角色扮演', '/#/impersonation'],
      ['知识库', '/#/library'],
      ['世界体系', '/#/world'],
      ['评估中心', '/#/eval'],
      ['设置', '/#/settings'],
    ];
    for (const [label, hash] of navs) {
      await page.getByRole('link', { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(hash.replace('#', '#')));
    }
  });

  test('主题切换：浅色 ↔ 深色', async ({ page }) => {
    await page.goto('/#/');
    const toggle = page.getByTitle(/切换到/);
    // 深色模式时 html.dark
    await toggle.click();
    await expect(page.locator('html.dark')).toHaveCount(1);
    await toggle.click();
    await expect(page.locator('html.dark')).toHaveCount(0);
  });

  test('欢迎快捷操作存在', async ({ page }) => {
    await page.goto('/#/');
    await expect(page.getByRole('button', { name: /搜索最新AI新闻/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /搜索小说剧情/ })).toBeVisible();
  });
});
