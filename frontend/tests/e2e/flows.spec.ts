/**
 * Playwright 用户流（新 Aurora UI）—— mock API 下的关键交互。
 */
import { expect, test } from '@playwright/test';

import { mockAgentApi } from './mockApi';

test.beforeEach(async ({ page }) => {
  await mockAgentApi(page);
});

test.describe('用户流（Aurora 新设计）', () => {
  test('对话：发送消息并渲染流式回复', async ({ page }) => {
    await page.goto('/#/');
    await page.getByPlaceholder(/输入消息/).fill('你好');
    await page.getByTitle('发送').click();
    await expect(page.getByText('模拟助手回复')).toBeVisible();
  });

  test('知识库：书目 Tab 展示上传区与卷列表', async ({ page }) => {
    await page.goto('/#/library');
    await expect(page.getByText('知识库').first()).toBeVisible();
    await expect(page.getByText(/导入小说/)).toBeVisible();
    await expect(page.getByText(/点击或拖拽文件到此处上传/)).toBeVisible();
    // 书目列表含 mock 卷
    await expect(page.getByText('测试小说第一卷').first()).toBeVisible();
  });

  test('知识库：角色 Tab 展示角色与建卡操作', async ({ page }) => {
    await page.goto('/#/library');
    // 等待系列自动加载完成（mock novels → 自动选中第一个系列）
    await expect(page.getByText('测试小说第一卷').first()).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: /角色/ }).click();
    await expect(page.getByText('测试角色').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByPlaceholder(/搜索候选角色/)).toBeVisible();
  });

  test('知识库：名录 Tab 展示别名编辑', async ({ page }) => {
    await page.goto('/#/library');
    await page.getByRole('button', { name: /别名名录/ }).click();
    await expect(page.getByText('测试角色').first()).toBeVisible();
    await expect(page.getByText('测角')).toBeVisible();
    await expect(page.getByRole('button', { name: '保存' })).toBeVisible();
  });

  test('世界体系：剧情分析 Tab 可进入', async ({ page }) => {
    await page.goto('/#/world');
    await expect(page.getByText('世界体系').first()).toBeVisible();
    await expect(page.getByRole('button', { name: /剧情分析/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /生成索引/ })).toBeVisible();
  });

  test('评估中心：统计卡与 LLM 评分按钮', async ({ page }) => {
    await page.goto('/#/eval');
    await expect(page.getByText('评估中心').first()).toBeVisible();
    await expect(page.getByRole('button', { name: /LLM 自动评分/ })).toBeVisible();
    await expect(page.getByText('检索案例').first()).toBeVisible();
  });

  test('设置：LLM 端点卡片与保存/测试', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.getByText('设置').first()).toBeVisible();
    await expect(page.getByText('对话模型')).toBeVisible();
    await expect(page.getByRole('button', { name: '测试连接' })).toBeVisible();
  });

  test('扮演：发送消息并渲染流式回复与引用', async ({ page }) => {
    await page.goto('/#/impersonation');
    const input = page.getByPlaceholder(/说点什么/);
    await expect(input).toBeEnabled();
    await input.fill('在吗？');
    await page.getByTitle('发送').click();
    await expect(page.getByText('模拟角色回复')).toBeVisible();
  });
});
