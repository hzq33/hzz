/**
 * 真实功能链路验证（Live + 真实 LLM）：
 * 扮演对话流式回复 + 引用、通用对话流式回复。耗时较长（LLM 生成）。
 */
import { expect, test } from '@playwright/test';

test.describe('真实功能链路（Live LLM）', () => {
  test('扮演：真实角色收到 LLM 流式回复', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto('/#/impersonation');
    const input = page.getByPlaceholder(/说点什么/);
    await expect(input).toBeEnabled({ timeout: 20_000 });
    // 取第一个真实角色
    const select = page.locator('header select').first();
    const val = await select.inputValue();
    expect(val).toBeTruthy();
    await input.fill('你好，简单介绍一下你自己');
    await page.getByTitle('发送').click();
    // 等待 AI 气泡出现（流式；AI 内容用 <p.whitespace-pre-wrap> 渲染）
    await expect(page.locator('p.whitespace-pre-wrap').first()).toBeVisible({ timeout: 60_000 });
  });

  test('对话：通用助手收到 LLM 流式回复', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto('/#/');
    await page.getByPlaceholder(/输入消息/).fill('你好，用一句话介绍自己');
    await page.getByTitle('发送').click();
    // 等待 AI 回复气泡（markdown 渲染）
    await expect(page.locator('.card, [class*="bg-surface"]').first()).toBeVisible({ timeout: 60_000 });
  });
});
