# Modular Agent Frontend

> React 18 + TypeScript 5 + Vite 5 + Zustand 5 + Tailwind CSS
>
> Agent 对话 + 角色扮演 + 知识库管理 三合一前端

---

## 目录

- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [可用脚本](#可用脚本)
- [目录结构](#目录结构)
- [环境变量](#环境变量)
- [开发指南](#开发指南)
- [构建与部署](#构建与部署)
- [测试](#测试)
- [代码规范](#代码规范)
- [监控与可观测性](#监控与可观测性)
- [安全策略](#安全策略)
- [常见问题](#常见问题)

---

## 环境要求

| 工具 | 版本 | 说明 |
|------|------|------|
| Node.js | ≥ 22.0.0 | 见 [.nvmrc](./.nvmrc) |
| npm | ≥ 10.0.0 | 随 Node 22 附带 |
| 浏览器 | 现代浏览器 | Chrome / Firefox / Safari / Edge 最近 2 个版本 |

推荐使用 [nvm](https://github.com/nvm-sh/nvm) 管理版本：

```bash
nvm use        # 自动读取 .nvmrc
```

---

## 快速开始

```bash
# 1. 安装依赖
npm install

# 2. 复制环境变量模板
cp .env.example .env.local
# 按需修改 .env.local 中的配置

# 3. 启动开发服务器
npm run dev
# 默认监听 http://localhost:3001
```

开发服务器会自动代理 `/api/v1/agent/*` 到后端（默认 `http://localhost:8080`，可在 `.env.local` 中通过 `VITE_PROXY_TARGET` 覆盖）。

---

## 可用脚本

| 脚本 | 用途 |
|------|------|
| `npm run dev` | 启动 Vite 开发服务器（热更新） |
| `npm run build` | 类型检查 + 生产构建到 `dist/` |
| `npm run build:analyze` | 构建并生成 bundle 体积分析报告（`dist/stats.html`） |
| `npm run preview` | 本地预览生产构建产物 |
| `npm run preview:prod` | 以生产模式预览（端口 4000） |
| `npm run lint` | ESLint 检查（0 警告阈值） |
| `npm run lint:fix` | ESLint 自动修复 |
| `npm run typecheck` | TypeScript 类型检查（不产出文件） |
| `npm run test` | 运行 Vitest 单元测试 |
| `npm run test:watch` | 单元测试监听模式 |
| `npm run test:coverage` | 单元测试 + 覆盖率报告 |
| `npm run test:e2e` | 运行 Playwright E2E 测试（需先 `npm i -D @playwright/test`） |
| `npm run format` | Prettier 格式化全部源文件 |
| `npm run format:check` | Prettier 格式校验（不写入） |
| `npm run check-all` | 一键执行 lint + typecheck + test |
| `npm run clean` | 清理 dist / coverage / 缓存等产物 |

---

## 目录结构

```
frontend/
├── src/
│   ├── components/          # 可复用组件
│   │   ├── chat/            # 聊天相关（消息气泡 / 输入栏 / Plan 卡片等）
│   │   ├── feedback/        # 反馈类（ErrorBoundary / 骨架屏 / 错误兜底）
│   │   ├── knowledge/       # 知识库（角色卡片）
│   │   ├── layout/          # 布局（Sidebar）
│   │   ├── ui/              # 通用 UI 原子组件
│   │   └── index.ts         # 统一出口
│   ├── hooks/               # 自定义 Hooks（SSE 流处理）
│   ├── lib/                 # 业务无关的基础设施
│   │   ├── monitor/         # 监控（Sentry + Web Vitals）
│   │   ├── api.ts           # API 客户端
│   │   ├── config.ts        # 运行时配置（环境变量派生）
│   │   ├── constants.ts     # 常量（API 端点 / 存储 key）
│   │   └── errors.ts        # 错误处理工具
│   ├── pages/               # 路由页面（懒加载）
│   ├── store/               # Zustand 状态管理
│   ├── styles/              # 全局样式
│   ├── types/               # TypeScript 类型定义
│   ├── App.tsx              # 根组件（路由 + 布局）
│   └── main.tsx             # 应用入口（启动序列）
├── src/**/__tests__/        # 与源码同置的单元测试（如 lib/__tests__）
├── tests/e2e/               # Playwright E2E
├── .env.example             # 环境变量模板
├── .browserslistrc          # 浏览器兼容目标
├── eslint.config.js         # ESLint Flat Config
├── vite.config.ts           # Vite 配置（构建 / 代理 / 分包）
├── vitest.config.ts         # Vitest 配置
├── tailwind.config.js       # Tailwind 配置
├── nginx.conf               # 生产 nginx 配置（含安全头）
├── Dockerfile               # 多阶段构建（builder + nginx）
└── package.json
```

---

## 环境变量

所有客户端变量必须以 `VITE_` 前缀开头才会暴露给浏览器。完整模板见 [.env.example](./.env.example)。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_APP_TITLE` | `Modular Agent` | 应用标题 |
| `VITE_APP_VERSION` | `0.0.0` | 应用版本（用于 Sentry release） |
| `VITE_API_BASE_URL` | `/api/v1/agent` | API 基础路径 |
| `VITE_SENTRY_DSN` | （空） | Sentry DSN，留空则禁用监控 |
| `VITE_TRACES_SAMPLE_RATE` | `0.1` | 性能采样率（0~1） |
| `VITE_DEBUG` | `false` | 调试模式（输出详细日志） |
| `VITE_ENABLE_PWA` | `false` | 是否启用 PWA |
| `VITE_ENABLE_MOCK` | `false` | 是否启用 Mock 数据 |
| `VITE_DEFAULT_LOCALE` | `zh-CN` | 默认语言 |

### 本地覆盖

```bash
cp .env.example .env.local
# 编辑 .env.local（已被 .gitignore 忽略）
```

### 环境文件加载顺序

Vite 按以下顺序合并环境变量（后者覆盖前者）：

1. `.env` — 默认值
2. `.env.{mode}` — 模式特定（development / production / test）
3. `.env.local` — 本地覆盖（不入版本控制）
4. `.env.{mode}.local` — 模式特定的本地覆盖

---

## 开发指南

### 状态管理

使用 [Zustand](https://github.com/pmndrs/zustand) 管理全局状态，store 位于 `src/store/`：

- `chatStore.ts` — 通用助手对话状态
- `impersonationStore.ts` — 角色扮演对话状态

在组件中使用：

```tsx
import { useChatStore } from '@/store/chatStore';

const messages = useChatStore((s) => s.messages);
const send = useChatStore((s) => s.send);
```

### API 调用

API 客户端位于 [src/lib/api.ts](./src/lib/api.ts)，已封装类型安全的请求函数：

```ts
import { fetchHealth, fetchCharacters, streamChat } from '@/lib/api';

// 普通请求
const health = await fetchHealth();

// SSE 流式对话
await streamChat(message, sessionId, {
  onEvent: (event) => { /* 处理事件 */ },
  onError: (err) => { /* 处理错误 */ },
});
```

### 错误处理

统一使用 `getErrorMessage` 提取错误消息（避免 `any` 类型）：

```ts
import { getErrorMessage } from '@/lib/errors';

try {
  await api();
} catch (err) {
  setError(getErrorMessage(err, '操作失败'));
}
```

### 添加新页面

1. 在 `src/pages/` 创建组件（默认导出）
2. 在 [App.tsx](./src/App.tsx) 中用 `React.lazy` 注册路由：

```tsx
const NewPage = lazy(() => import('@/pages/NewPage'));

<Route
  path="/new"
  element={
    <Suspense fallback={<PageSkeleton rows={6} />}>
      <NewPage />
    </Suspense>
  }
/>
```

3. 在 [Sidebar.tsx](./src/components/layout/Sidebar.tsx) 的 `links` 数组中添加导航项

### 路径别名

已配置 `@/*` 指向 `src/*`：

```ts
import { useChatStore } from '@/store/chatStore';
import type { ChatMessage } from '@/types';
```

---

## 构建与部署

### 本地构建

```bash
npm run build
# 产物输出到 dist/
```

### Bundle 体积分析

```bash
npm run build:analyze
# 自动打开 dist/stats.html（treemap 可视化）
```

### Docker 构建

[Dockerfile](./Dockerfile) 采用多阶段构建：

```bash
docker build -t agent-frontend ./frontend
docker run -p 8080:80 agent-frontend
# 访问 http://localhost:8080
```

### 生产部署架构

```
┌─────────────┐     ┌──────────────────────────┐     ┌─────────────────┐
│   Browser   │────▶│   nginx (静态 + 反代)     │────▶│  Agent Backend  │
│             │     │   - 静态资源 / 安全头     │     │  (FastAPI)      │
└─────────────┘     │   - /api/v1/agent/* 反代  │     │  :8080          │
                    └──────────────────────────┘     └─────────────────┘
```

nginx 配置见 [nginx.conf](./nginx.conf)，已包含：

- CSP / HSTS / X-Frame-Options 等安全响应头
- `/assets/` 长期缓存（immutable）
- `index.html` 不缓存（保证发布即生效）
- `/api/v1/agent/` 反向代理（含 WebSocket 支持，用于 SSE）

---

## 测试

### 单元测试

使用 [Vitest](https://vitest.dev/) + jsdom：

```bash
npm run test              # 单次运行
npm run test:watch        # 监听模式
npm run test:coverage     # 覆盖率报告
npm run test:ui           # 可视化界面
```

测试文件约定：
- 放置于 `src/**/__tests__/`（与源码同置）
- 命名 `*.test.ts` / `*.test.tsx`

### E2E 测试（可选）

使用 [Playwright](https://playwright.dev/)：

```bash
# 首次使用需安装
npm i -D @playwright/test
npx playwright install

# 运行
npm run test:e2e
npm run test:e2e:ui       # 可视化界面
```

配置见 [playwright.config.ts](./playwright.config.ts)。

---

## 代码规范

### ESLint

配置见 [eslint.config.js](./eslint.config.js)，采用 Flat Config：

- TypeScript 严格类型检查（`recommendedTypeChecked`）
- React Hooks 规则
- Import 排序（builtin → external → internal → type）
- 禁止 `no-floating-promises` / `no-misused-promises`
- `unsafe-*` 系列规则全部为 error 级（0 警告阈值）

```bash
npm run lint              # 检查
npm run lint:fix          # 自动修复
```

### Prettier

配置见 [.prettierrc.json](./.prettierrc.json)，含 Tailwind CSS 插件。

```bash
npm run format            # 格式化
npm run format:check      # 校验
```

### Git Hooks

通过 [Husky](https://typicode.github.io/husky/) 配置：

| Hook | 作用 |
|------|------|
| `pre-commit` | 运行 lint-staged（对暂存文件执行 eslint + prettier） |
| `commit-msg` | 校验提交信息符合 Conventional Commits |
| `pre-push` | 推送前检查 |

### 提交信息规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

允许的 type（见 [commitlint.config.js](./commitlint.config.js)）：

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构（既不是 feat 也不是 fix） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `build` | 构建系统或外部依赖变更 |
| `ci` | CI 配置 |
| `chore` | 杂项（不修改 src 或测试） |
| `revert` | 回滚提交 |

示例：

```
feat(chat): 支持流式输出中断
fix(knowledge): 修复角色卡编辑保存失败
docs: 更新使用说明
```

---

## 监控与可观测性

### Sentry 错误监控

- 配置 `VITE_SENTRY_DSN` 环境变量启用
- 动态导入，DSN 为空时自动 no-op，不影响构建体积
- 已过滤已知噪音（ResizeObserver loop / Network request failed）

### Web Vitals 性能监控

- 自动采集 LCP / FID / CLS / FCP / TTFB / INP
- 通过 `sendBeacon` 上报到 `/api/v1/monitor/web-vitals`
- 业务侧可调用 `trackTiming(name, duration)` 自定义埋点

### ErrorBoundary

- 捕获子组件树渲染异常，避免白屏
- 提供「重试」与「刷新」两种恢复路径
- 支持 `resetKeys` 自动重置

### 全局错误处理器

在 [main.tsx](./src/main.tsx) 中最早安装：

- `window.error` 捕获同步异常
- `unhandledrejection` 捕获未 await 的 Promise

---

## 安全策略

### 内容安全策略（CSP）

nginx 已配置严格 CSP，仅允许同源资源加载。**新增外部资源时需同步更新 [nginx.conf](./nginx.conf)**。

### XSS 防护

LLM 输出的 markdown 通过 `rehype-sanitize` 净化，禁止 `<script>` / `<iframe>` 等危险标签。

### 安全响应头清单

| Header | 作用 |
|--------|------|
| `Content-Security-Policy` | 限制资源加载来源 |
| `Strict-Transport-Security` | 强制 HTTPS |
| `X-Frame-Options: SAMEORIGIN` | 防点击劫持 |
| `X-Content-Type-Options: nosniff` | 防 MIME 嗅探 |
| `Referrer-Policy` | 控制 Referrer 泄露 |
| `Permissions-Policy` | 禁用不必要的浏览器 API |

---

## 常见问题

### Q: 启动开发服务器后 API 请求 404？

A: 检查后端服务是否启动（默认 `http://localhost:8080`）。如后端端口不同，在 `.env.local` 中设置 `VITE_PROXY_TARGET`。

### Q: ESLint 报 `parserOptions.project` 错误？

A: 该错误通常因为 `.js` 配置文件未被 tsconfig 包含。本项目已在 [eslint.config.js](./eslint.config.js) 中为配置文件单独配置 `disableTypeChecked`，正常情况不会触发。若新增配置文件报错，将其文件名加入对应 `files` 数组即可。

### Q: 构建产物体积过大？

A: 运行 `npm run build:analyze` 查看 treemap 报告。常见原因：
- 引入了大体积依赖（如 moment.js，推荐用 dayjs 替代）
- 未使用路由懒加载（见 [App.tsx](./src/App.tsx) 中的 `React.lazy` 模式）

### Q: 部署后页面空白？

A: 检查：
1. nginx 是否正确配置 SPA fallback（`try_files $uri $uri/ /index.html`）
2. 浏览器控制台是否有 CSP 违规报错
3. 静态资源路径是否正确（`vite.config.ts` 中 `base: './'` 使用相对路径）

### Q: 如何禁用 Sentry？

A: 将 `VITE_SENTRY_DSN` 留空即可。模块会自动进入 no-op 模式，不影响功能。

### Q: 如何更新浏览器兼容目标？

A: 修改 [.browserslistrc](./.browserslistrc)，Autoprefixer / Vite / ESLint 会自动读取。修改后需重启 `npm run dev`。

### Q: Husky 钩子未生效？

A: 运行 `npm run prepare` 重新安装钩子。如手动安装了依赖但 `prepare` 脚本未触发（某些 npm 版本的已知问题），可手动执行 `npx husky .husky`。

---

## 相关文档

- [Vite 官方文档](https://vitejs.dev/)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/)
- [Zustand GitHub](https://github.com/pmndrs/zustand)
- [Tailwind CSS](https://tailwindcss.com/)
- [ESLint Flat Config](https://eslint.org/docs/latest/use/configure/configuration-files)
- [Conventional Commits](https://www.conventionalcommits.org/)

