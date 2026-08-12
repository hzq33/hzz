/**
 * 真实后端联通验证（不 mock）—— 需后端运行于 8080。
 * 验证：页面渲染 + 真实 API 数据驱动 + 关键链路可交互。
 */
import { expect, test } from '@playwright/test';

test.describe('真实后端联通（Live）', () => {
  test('首页：健康灯转为在线（真实 /health）', async ({ page }) => {
    await page.goto('/#/');
    // 轮询后侧栏健康灯应显示"在线"
    await expect(page.getByText('服务在线')).toBeVisible({ timeout: 15_000 });
  });

  test('知识库：书目 Tab 加载真实书目', async ({ page }) => {
    await page.goto('/#/library');
    await expect(page.getByText('导入小说').first()).toBeVisible();
    // 等待真实书目出现（任意系列 Tab 按钮）
    await expect(page.getByRole('button', { name: /全部$/ }).first()).toBeVisible({ timeout: 15_000 });
  });

  test('世界体系：剧情分析面板真实渲染（有系列选择）', async ({ page }) => {
    await page.goto('/#/world');
    await expect(page.getByText('世界体系').first()).toBeVisible();
    // 系列选择器出现（有数据时）
    const seriesSelect = page.locator('header select').first();
    await expect(seriesSelect).toBeVisible({ timeout: 15_000 });
  });

  test('评估中心：真实统计卡渲染', async ({ page }) => {
    await page.goto('/#/eval');
    await expect(page.getByText('评估中心').first()).toBeVisible();
    // 检索总数统计卡（真实 rag-eval）
    await expect(page.getByText('检索总数').first()).toBeVisible({ timeout: 15_000 });
  });

  test('设置：真实 LLM 端点配置加载', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.getByText('设置').first()).toBeVisible();
    // 真实端点卡（有保存/测试按钮）
    await expect(page.getByRole('button', { name: '保存' }).first()).toBeVisible({ timeout: 15_000 });
  });

  test('扮演：真实角色可加载并进入输入', async ({ page }) => {
    await page.goto('/#/impersonation');
    // 等待角色选择器出现真实已建卡角色
    const select = page.locator('header select').first();
    await expect(select).toBeVisible({ timeout: 20_000 });
    // 输入框最终可用（角色加载完成后）
    await expect(page.getByPlaceholder(/说点什么/)).toBeEnabled({ timeout: 20_000 });
  });
});
