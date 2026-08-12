/* ── Stream Event Types ── */

export interface PhaseEvent {
  type: 'phase';
  phase: string;
  message?: string;
}

export interface PlanEvent {
  type: 'plan';
  goal?: string;
  steps?: Array<{
    id: number;
    description: string;
    tool_name?: string | null;
  }>;
  reasoning?: string;
}

export interface StepResultEvent {
  type: 'step_result';
  step_id: number;
  success: boolean;
  tool_name?: string;
  output?: string;
  error?: string;
}

export interface ReplyChunkEvent {
  type: 'reply_chunk';
  token: string;
}

export interface DoneEvent {
  type: 'done';
  elapsed_ms: number;
  success?: boolean;
  session_id?: string;
  rag_mode?: boolean;
  character?: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost_usd?: number;
    model?: string;
  };
}

export interface ErrorEvent {
  type: 'error';
  phase?: string;
  message: string;
}

export interface ApprovalRequiredEvent {
  type: 'approval_required';
  approval_id: string;
  session_id?: string;
  tool_name: string;
  tool_args?: Record<string, unknown>;
  status?: string;
  timeout_seconds?: number;
}

export type StreamEventData =
  | PlanEvent
  | StepResultEvent
  | PhaseEvent
  | ReplyChunkEvent
  | DoneEvent
  | ErrorEvent
  | ApprovalRequiredEvent;

/* ── Message Types ── */

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  plan?: PlanEvent;
  stepResults?: StepResultEvent[];
  elapsed?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost_usd?: number;
    model?: string;
  };
  timestamp: number;
}

/** 扮演会话消息（含引用证据） */
export interface ImpMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  citations?: StoryEvidence[];
  elapsed?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

/* ── API Types ── */

export interface ToolInfo {
  name: string;
  description: string;
}

export interface HealthResponse {
  status: string;
  model?: string;
  error?: string | null;
  sessions_active?: number;
  sessions_max?: number;
}

export type AgentPhase = string;

export const AGENT_PHASES = ['planning', 'tool_calling', 'executing', 'replying', 'plan_failed'] as const;

export interface CharacterInfo {
  name: string;
  source?: string;
  source_work?: string;
  series_id?: string;
  character_id?: string;
  dialogue_count?: number;
  mention_count?: number;
  in_llm_seed?: boolean;
  aliases?: string[];
  has_card?: boolean;
  status?: string;
  personality?: string;
  speaking_style?: string;
  background?: string;
  catchphrases?: string[];
  sample_dialogues?: string[];
  sample_count?: number;
  source_chapters?: string[];
  source_doc_ids?: string[];
  /** main | supporting | extra（主角/配角/路人，来自 inventory） */
  importance?: string;
}

export interface NovelVolumeInfo {
  series_id: string;
  series_title?: string;
  doc_id: string;
  volume_no?: number | null;
  volume_title?: string;
  title?: string;
  source_format?: string;
  indexed_at?: string;
  block_counts?: Record<string, number>;
  chapter_count?: number;
  needs_reindex?: boolean;
  reindex_reason?: string;
}

export interface CharacterBuildJobInfo {
  job_id: string;
  input_name: string;
  canonical_name?: string;
  character_id?: string;
  state: string;
  error?: string | null;
  flags?: {
    candidates?: DisambiguationCandidate[];
    [key: string]: unknown;
  };
  evidence?: Record<string, unknown>;
  card_path?: string | null;
}

export interface DisambiguationCandidate {
  character_id: string;
  canonical_name: string;
  score?: number;
  dialogue_count?: number;
}

export interface DisambiguationRequest {
  input_name: string;
  candidates: DisambiguationCandidate[];
}

export interface ImpersonationSessionSummary {
  session_id: string;
  character: string;
  doc_id?: string | null;
  title: string;
  message_count: number;
  preview?: string;
  updated_at?: string | null;
  active?: boolean;
}

export interface ImpersonationHistory {
  session_id: string;
  character: string;
  doc_id?: string | null;
  title?: string | null;
  messages: Array<{ role: string; content: string }>;
  updated_at?: string | null;
}

export interface MemoryStats {
  max_tokens?: number | null;
  tokens_est?: number;
  summarized_turns?: number;
  summary_excerpt?: string;
}

export interface MemoryConfig {
  max_history_tokens: number;
  enable_summarization: boolean;
  summarize_keep_turns: number;
  summarize_threshold: number;
}

export interface StoryEvidence {
  doc_id?: string;
  chapter_order?: number;
  chapter_title?: string;
  block_id?: string;
  snippet?: string;
  channel?: string;
  /** Display relevance (vector similarity). Do not treat RRF rank scores as %. */
  score?: number | null;
  /** Explicit vector similarity when available (preferred over score). */
  similarity?: number | null;
  /** fact = 原著依据；style = 口吻参考。缺省时前端按 channel 推断。 */
  role?: 'fact' | 'style';
}

export interface StoryEvent {
  event_id: string;
  summary: string;
  event_type?: string;
  characters?: string[];
  confidence?: number;
  evidence?: StoryEvidence[];
  doc_id?: string;
  chapter_order?: number;
  chapter_title?: string;
}

export interface ForeshadowItem {
  foreshadow_id: string;
  content: string;
  status?: string;
  related_characters?: string[];
  introduced_chapter?: number;
  introduced_doc_id?: string;
  resolved_chapter?: number | null;
  confidence?: number;
  evidence?: StoryEvidence[];
}

export interface RelationChange {
  change_id: string;
  source: string;
  target: string;
  relation_type?: string;
  polarity?: string;
  summary?: string;
  chapter_order?: number;
  doc_id?: string;
  chapter_title?: string;
  confidence?: number;
  evidence?: StoryEvidence[];
}

export interface GraphNode {
  id: string;
  degree: number;
  relations_count: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  category: string;
  polarity: string;
  weight: number;
  confidence: number;
  relation_types: string[];
  summaries: string[];
  chapters: string[];
  category_dist: Record<string, number>;
  first_chapter_order: number | null;
  last_chapter_order: number | null;
}

export interface CharacterGraph {
  series_id: string;
  exists?: boolean;
  doc_id?: string | null;
  updated_at?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: {
    node_count: number;
    edge_count: number;
    relation_count: number;
    category_dist: Record<string, number>;
    polarity_dist: Record<string, number>;
  };
}

export interface StoryAnalysis {
  series_id: string;
  exists?: boolean;
  events?: StoryEvent[];
  foreshadows?: ForeshadowItem[];
  relations?: RelationChange[];
  stats?: Record<string, unknown>;
  updated_at?: string;
}

export interface UploadJobInfo {
  job_id: string;
  state: string;
  error?: string | null;
  progress?: {
    stage?: string;
    message?: string;
    pct?: number;
  };
  status?: string;
  doc_id?: string;
  series_id?: string;
  characters?: string[];
  hint?: string;
}

/* ── RAG 在线评估（真实对话检索复盘）── */

export interface RagEvalSummary {
  total: number;
  zero_hit: number;
  zero_hit_rate: number;
  scoped: number;
  scoped_rate: number;
  avg_hits: number;
  avg_ms: number;
  avg_variants: number;
  kinds: Record<string, number>;
  channels: Record<string, number>;
  channel_labels: Record<string, string>;
}

export interface RagEvalHit {
  global_id: string;
  block_type: string;
  doc_id: string;
  chapter_title: string;
  score?: number;
  preview: string;
}

export interface RagEvalCase {
  ts: string;
  kind: string;
  query: string;
  channel: string;
  doc_id: string;
  series_id: string;
  filters: Record<string, unknown>;
  hit_count: number;
  zero_hit: boolean;
  elapsed_ms?: number;
  query_variants?: number;
  hits: RagEvalHit[];
}

export interface RagEvalResponse {
  summary: RagEvalSummary;
  total_available: number;
  active_sessions?: number;
  cases: RagEvalCase[];
  error?: string;
}

export interface RagJudgeResult {
  query: string;
  channel: string;
  score: number | null;
  reason: string;
  ts?: string;
  preview?: string;
}

export interface RagJudgeResponse {
  results: RagJudgeResult[];
  summary: { judged: number; avg_score: number; low_count: number };
  low: RagJudgeResult[];
  error?: string;
}

/* ── LLM 调用点配置（设置页） ── */

export interface LlmProviderInfo {
  key: string;
  label: string;
  base_url: string;
  models?: string[];
}

export interface LlmEndpointConfig {
  provider: string;
  model: string;
  api_key_masked: string;
  has_api_key: boolean;
  temperature: number;
  max_tokens: number;
  enabled: boolean;
  thinking: 'auto' | 'on' | 'off';
}

export interface LlmEndpointInfo {
  key: string;
  group: string;
  label: string;
  config: LlmEndpointConfig;
}

export interface LlmConfigResponse {
  providers: LlmProviderInfo[];
  endpoints: LlmEndpointInfo[];
}

export interface LlmEndpointEdit {
  provider?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  temperature?: number;
  max_tokens?: number;
  enabled?: boolean;
  thinking?: 'auto' | 'on' | 'off';
}

export interface LlmTestResult {
  ok: boolean;
  model?: string;
  reply?: string;
  error?: string;
}

/* ── V5 世界体系：时间线 / Lorebook ───────────────────── */

export interface StoryTimeInfo {
  year?: number | null;
  period?: string;
  label?: string;
  relative?: string;
  confidence?: number;
}

export interface ChronicleEvent {
  seq: number;
  summary: string;
  event_type: string;
  characters: string[];
  confidence: number;
  doc_id: string;
  chapter_order: number;
  chapter_title: string;
  evidence: string[];
  story_time: StoryTimeInfo;
  key_event: boolean;
}

export interface EraGroup {
  era: string;
  seqs: number[];
  events_count: number;
}

export interface TimelineResponse {
  series_id: string;
  exists?: boolean;
  chronicle: ChronicleEvent[];
  by_character: Record<string, number[]>;
  by_era: EraGroup[];
  stats?: {
    event_count: number;
    key_event_count: number;
    character_count: number;
    era_count: number;
    year_annotated: number;
  };
}

export interface LorebookEntry {
  entry_id: string;
  kind: 'entity' | 'relation';
  entity: string;
  counterpart?: string;
  keys: string[];
  time_range: { year_from?: number | null; year_to?: number | null; era?: string };
  seq_from: number;
  seq_to: number;
  priority: number;
  content: string;
  source: string;
  active: boolean;
}

export interface LorebookResponse {
  series_id: string;
  exists?: boolean;
  entries: LorebookEntry[];
  stats?: {
    entity_entries: number;
    relation_entries: number;
    entity_count: number;
    event_count: number;
  };
}

/* ── GraphRAG 全局问答 ──────────────────────────────── */

export interface RagGlobalCommunity {
  id: number;
  members: string[];
  summary: string;
  core_relations: {
    source: string;
    target: string;
    weight: number;
    relation_type?: string;
    polarity?: string;
    confidence?: number;
  }[];
  key_events?: { summary: string; chapter?: string; confidence?: number }[];
}

export interface RagGlobalResponse {
  series_id: string;
  exists?: boolean;
  stale?: boolean;
  hint?: string;
  updated_at?: string;
  global_overview?: string;
  communities?: RagGlobalCommunity[];
  context?: string;
}
