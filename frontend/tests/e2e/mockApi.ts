import type { Page, Route } from '@playwright/test';

const JSON_HEADERS = { 'content-type': 'application/json' };
const SSE_HEADERS = {
  'cache-control': 'no-cache',
  'content-type': 'text/event-stream; charset=utf-8',
};

function fulfillJson(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
}

function fulfillSse(route: Route, events: object[]) {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
  return route.fulfill({ status: 200, headers: SSE_HEADERS, body });
}

export async function mockAgentApi(page: Page) {
  await page.route('**/api/v1/agent/health', (route) =>
    fulfillJson(route, { status: 'ready', model: 'test' }),
  );
  await page.route('**/api/v1/agent/tools', (route) => fulfillJson(route, []));
  await page.route('**/api/v1/agent/novels', (route) =>
    fulfillJson(route, {
      items: [
        {
          doc_id: 'test-volume',
          title: '测试小说第一卷',
          volume_title: '测试小说第一卷',
          series_id: 'test-series',
          series_title: '测试小说',
          volume_no: 1,
        },
      ],
    }),
  );
  await page.route(/\/api\/v1\/agent\/characters(?:\?.*)?$/, (route) =>
    fulfillJson(route, [
      {
        name: '测试角色',
        source: '测试小说',
        series_id: 'test-series',
        dialogue_count: 3,
        has_card: true,
        status: 'ready',
      },
    ]),
  );
  await page.route('**/api/v1/agent/story-analysis**', (route) =>
    fulfillJson(route, { analysis: null }),
  );
  await page.route('**/api/v1/monitor/**', (route) =>
    route.fulfill({ status: 204, body: '' }),
  );
  await page.route('**/api/v1/agent/chat/stream', (route) =>
    fulfillSse(route, [
      { type: 'reply_chunk', token: '模拟助手回复' },
      { type: 'done', session_id: 'e2e-home', elapsed_ms: 1 },
    ]),
  );
  await page.route('**/api/v1/agent/impersonate/chat/stream', (route) =>
    fulfillSse(route, [
      { type: 'reply_chunk', token: '模拟角色回复', session_id: 'e2e-imp' },
      { type: 'done', session_id: 'e2e-imp', max_history_tokens: 2048 },
    ]),
  );

  /* ── 新页面补充 mock ── */
  await page.route(/\/api\/v1\/agent\/impersonate\/sessions.*/, (route) =>
    fulfillJson(route, { items: [] }),
  );
  await page.route(/\/api\/v1\/agent\/impersonate\/history.*/, (route) =>
    fulfillJson(route, { session_id: 'e2e-imp', character: '测试角色', messages: [] }),
  );
  await page.route(/\/api\/v1\/agent\/characters\/roster.*/, (route) =>
    fulfillJson(route, {
      entities: [{ canonical_name: '测试角色', aliases: ['测角'], importance: 'main', mention_count: 5 }],
    }),
  );
  await page.route(/\/api\/v1\/agent\/characters\/roster\/series.*/, (route) =>
    fulfillJson(route, { series: [{ series_id: 'test-series', title: '测试小说' }] }),
  );
  await page.route(/\/api\/v1\/agent\/characters\/graph.*/, (route) =>
    fulfillJson(route, {
      series_id: 'test-series',
      exists: true,
      nodes: [{ id: 'a', degree: 2, relations_count: 1 }],
      edges: [],
      stats: { node_count: 1, edge_count: 0, relation_count: 0, category_dist: {}, polarity_dist: {} },
    }),
  );
  await page.route(/\/api\/v1\/agent\/characters\/candidates.*/, (route) =>
    fulfillJson(route, { series_id: 'test-series', seed_min_mentions: 3, candidates_total: 1, candidates: [] }),
  );
  await page.route(/\/api\/v1\/agent\/characters\/merge-suggestions.*/, (route) =>
    fulfillJson(route, { series_id: 'test-series', suggestions: [] }),
  );
  await page.route(/\/api\/v1\/agent\/timeline.*/, (route) =>
    fulfillJson(route, { series_id: 'test-series', exists: true, chronicle: [], by_character: {}, by_era: [] }),
  );
  await page.route(/\/api\/v1\/agent\/lorebook.*/, (route) =>
    fulfillJson(route, { series_id: 'test-series', exists: true, entries: [] }),
  );
  await page.route(/\/api\/v1\/agent\/rag-global.*/, (route) =>
    fulfillJson(route, { series_id: 'test-series', exists: false, hint: '未构建' }),
  );
  await page.route(/\/api\/v1\/agent\/rag-eval.*/, (route) =>
    fulfillJson(route, {
      total_available: 0,
      active_sessions: 0,
      cases: [],
      summary: { total: 0, zero_hit: 0, zero_hit_rate: 0, scoped: 0, scoped_rate: 0, avg_hits: 0, avg_ms: 0, avg_variants: 0, kinds: {}, channels: {}, channel_labels: {} },
    }),
  );
  await page.route(/\/api\/v1\/agent\/llm-config.*/, (route) =>
    fulfillJson(route, {
      providers: [{ key: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1' }],
      endpoints: [
        { key: 'chat', group: '对话', label: '对话模型', config: { provider: 'deepseek', model: 'deepseek-chat', api_key_masked: '***', has_api_key: true, temperature: 0.8, max_tokens: 2048, enabled: true, thinking: 'auto' } },
      ],
    }),
  );
}
