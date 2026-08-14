/* ── API Endpoints ── */

export const API = {
  CHAT_STREAM: '/api/v1/agent/chat/stream',
  HEALTH: '/api/v1/agent/health',
  TOOLS: '/api/v1/agent/tools',
  TOOL_APPROVE: '/api/v1/agent/tools/approve',
  HISTORY: (sessionId: string) => `/api/v1/agent/history?session_id=${sessionId}`,
  // Impersonation
  CHARACTERS: '/api/v1/agent/characters',
  CHARACTERS_GRAPH: '/api/v1/agent/characters/graph',
  CHARACTERS_CANDIDATES: '/api/v1/agent/characters/candidates',
  CHARACTERS_BUILD: '/api/v1/agent/characters/build',
  CHARACTERS_MERGE: '/api/v1/agent/characters/merge',
  CHARACTERS_MERGE_SUGGESTIONS: '/api/v1/agent/characters/merge-suggestions',
  CHARACTER_JOB: (jobId: string) => `/api/v1/agent/characters/jobs/${jobId}`,
  CHARACTER_JOBS: '/api/v1/agent/characters/jobs',
  UPLOAD: '/api/v1/agent/upload',
  NOVELS: '/api/v1/agent/novels',
  STORY_ANALYSIS: '/api/v1/agent/story-analysis',
  STORY_ANALYSIS_BUILD: '/api/v1/agent/story-analysis/build',
  STORY_ANALYSIS_JOB: (jobId: string) => `/api/v1/agent/story-analysis/jobs/${jobId}`,
  // V5 世界体系
  TIMELINE: '/api/v1/agent/timeline',
  LOREBOOK: '/api/v1/agent/lorebook',
  RAG_GLOBAL: '/api/v1/agent/rag-global',
  RAG_GLOBAL_BUILD: '/api/v1/agent/rag-global/build',
  RAG_GLOBAL_JOB: (jobId: string) => `/api/v1/agent/rag-global/jobs/${jobId}`,
  IMP_CHAT: '/api/v1/agent/impersonate/chat',
  IMP_CHAT_STREAM: '/api/v1/agent/impersonate/chat/stream',
  IMP_HISTORY: (sessionId: string) => `/api/v1/agent/impersonate/history?session_id=${sessionId}`,
  IMP_RESET: '/api/v1/agent/impersonate/reset',
  IMP_REGENERATE: '/api/v1/agent/impersonate/regenerate',
  IMP_SESSIONS: '/api/v1/agent/impersonate/sessions',
  IMP_SESSION: (sessionId: string) =>
    `/api/v1/agent/impersonate/sessions/${encodeURIComponent(sessionId)}`,
  UPLOAD_JOB: (jobId: string) => `/api/v1/agent/upload/jobs/${jobId}`,
  NOVEL_SERIES: (seriesId: string) =>
    `/api/v1/agent/novels/series?series_id=${encodeURIComponent(seriesId)}`,
  DELETE_NOVEL: (docId: string) => `/api/v1/agent/novels/${encodeURIComponent(docId)}`,
  // LLM 调用点配置（设置页）
  LLM_CONFIG: '/api/v1/agent/llm-config',
  LLM_CONFIG_TEST: '/api/v1/agent/llm-config/test',
  MEMORY_CONFIG: '/api/v1/agent/memory-config',
} as const;

/* ── Storage Keys ── */

export const STORAGE_KEY = {
  SESSION_ID: 'agent_session_id',
} as const;

/* ── Welcome Quick Actions ── */

export const QUICK_ACTIONS = [
  {
    label: '搜索最新AI新闻',
    icon: '🔍',
    prompt: '用网络搜索查找今天的人工智能要闻，列出 3 条并注明来源。不要编造。',
  },
  {
    label: '列出项目文件',
    icon: '📁',
    prompt: '用文件工具列出项目根目录的主要文件和文件夹，简要说明各目录用途。',
  },
  {
    label: '搜索小说剧情',
    icon: '📖',
    prompt: '用 novel_search 检索已导入小说的主线剧情，概括库里有哪些作品以及各自核心冲突。没有入库则直接说明。',
  },
  { label: '去工作台开始扮演', icon: '🎭', href: '/impersonation' },
] as const;

/* ── Phase Display Names ── */

export const PHASE_LABELS: Record<string, string> = {
  planning: '正在规划…',
  tool_calling: '正在调用工具…',
  executing: '正在执行…',
  replying: '正在生成回复…',
  plan_failed: '规划失败',
};
