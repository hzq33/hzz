import { useEffect, useMemo, useRef, useState } from 'react';

import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d';

type GraphMethods = ForceGraphMethods<Record<string, unknown>, Record<string, unknown>>;

import { fetchCharacterGraph } from '@/api/characters';
import type { CharacterGraph, GraphEdge, GraphNode } from '@/types';

/* ── category → color / label ── */
const CATEGORY_COLORS: Record<string, string> = {
  family: '#f59e0b',
  lover: '#ec4899',
  friend: '#10b981',
  rival: '#8b5cf6',
  enemy: '#ef4444',
  mentor: '#3b82f6',
  colleague: '#06b6d4',
  other: '#94a3b8',
};

const CATEGORY_LABELS: Record<string, string> = {
  family: '亲属',
  lover: '恋人',
  friend: '朋友',
  rival: '竞争',
  enemy: '敌对',
  mentor: '师徒',
  colleague: '同僚',
  other: '其他',
};

const POLARITY_LABELS: Record<string, string> = {
  positive: '正向',
  negative: '负向',
  neutral: '中性',
};

type Selection =
  | { type: 'node'; id: string }
  | { type: 'edge'; edge: GraphEdge }
  | null;

export function RelationshipGraph({
  seriesId,
  docId,
}: {
  seriesId: string;
  docId?: string;
}) {
  const [data, setData] = useState<CharacterGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minWeight, setMinWeight] = useState(1);
  const [sel, setSel] = useState<Selection>(null);
  const fgRef = useRef<GraphMethods | undefined>(undefined);

  useEffect(() => {
    if (!seriesId) return;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    fetchCharacterGraph(seriesId, { docId, minWeight }, ctrl.signal)
      .then((g) => {
        setData(g);
        setSel(null);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name !== 'AbortError') {
          setError(e.message);
        }
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [seriesId, docId, minWeight]);

  const graphData = useMemo(() => {
    if (!data?.nodes.length) return { nodes: [], links: [] };
    return {
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges.map((e) => ({ ...e })),
    };
  }, [data]);

  // Zoom to fit once after data loads.
  useEffect(() => {
    if (!data || !data.nodes.length || !fgRef.current) return;
    const t = setTimeout(() => fgRef.current?.zoomToFit(400, 60), 300);
    return () => clearTimeout(t);
  }, [data]);

  // Edges connected to selected node (for side panel).
  const nodeEdges = useMemo<GraphEdge[]>(() => {
    if (!data || !sel || sel.type !== 'node') return [];
    return data.edges
      .filter((e) => e.source === sel.id || e.target === sel.id)
      .sort((a, b) => b.weight - a.weight);
  }, [data, sel]);

  const stats = data?.stats;

  if (!seriesId) {
    return <div className="flex-1 flex items-center justify-center text-sm text-slate-400">请先选择系列</div>;
  }
  if (loading && !data) {
    return <div className="flex-1 flex items-center justify-center text-sm text-slate-400">加载关系图谱…</div>;
  }
  if (error) {
    return <div className="flex-1 flex items-center justify-center text-sm text-rose-500">{error}</div>;
  }
  if (!data || !data.exists) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-center px-6">
        <p className="text-sm text-slate-500">该系列尚未生成关系数据。</p>
        <p className="text-xs text-slate-400">请先在「关系与事件」标签页构建剧情分析，再查看关系图谱。</p>
      </div>
    );
  }
  if (!data.nodes.length) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-slate-400">
        未提取到关系记录（可尝试降低 min_weight）。
      </div>
    );
  }

  return (
    <div className="flex flex-1 min-h-0 gap-2">
      {/* ── graph canvas ── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* toolbar */}
        <div className="flex items-center gap-3 px-1 pb-1 flex-wrap text-xs text-slate-500">
          <span>
            {stats?.node_count ?? 0} 角色 · {stats?.edge_count ?? 0} 关系 · {stats?.relation_count ?? 0} 记录
          </span>
          <label className="flex items-center gap-1">
            最小边权
            <input
              type="range"
              min={1}
              max={5}
              value={minWeight}
              onChange={(e) => setMinWeight(Number(e.target.value))}
              className="w-20 accent-blue-500"
            />
            <span className="tabular-nums">{minWeight}</span>
          </label>
          <button
            onClick={() => fgRef.current?.zoomToFit(400, 60)}
            className="px-2 py-0.5 rounded border border-slate-200 hover:bg-slate-50"
          >
            居中
          </button>
          {/* legend */}
          <div className="flex items-center gap-2 ml-auto flex-wrap">
            {Object.entries(CATEGORY_LABELS).map(([k, label]) => {
              const cnt = stats?.category_dist?.[k] ?? 0;
              if (!cnt && k === 'other') return null;
              return (
                <span key={k} className="flex items-center gap-1" title={`${label}：${cnt}`}>
                  <span
                    className="inline-block w-2.5 h-2.5 rounded-full"
                    style={{ background: CATEGORY_COLORS[k] }}
                  />
                  {label}
                  {cnt > 0 && <span className="text-slate-400">{cnt}</span>}
                </span>
              );
            })}
          </div>
        </div>

        <div className="flex-1 min-h-0 rounded-lg border border-slate-200 overflow-hidden bg-slate-50">
          <ForceGraph2D
            ref={fgRef}
            graphData={graphData}
            nodeRelSize={5}
            cooldownTicks={120}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
              const n = node as GraphNode & { x?: number; y?: number };
              const r = Math.max(4, Math.min(13, 3 + n.degree));
              const x = n.x ?? 0;
              const y = n.y ?? 0;
              const isSel = sel?.type === 'node' && sel.id === n.id;
              ctx.beginPath();
              ctx.arc(x, y, r, 0, 2 * Math.PI);
              ctx.fillStyle = isSel ? '#1e40af' : '#3b82f6';
              ctx.fill();
              ctx.strokeStyle = '#fff';
              ctx.lineWidth = 1.5;
              ctx.stroke();
              if (globalScale > 1.3) {
                ctx.font = `${10 / globalScale}px ui-sans-serif, system-ui, sans-serif`;
                ctx.textAlign = 'center';
                ctx.fillStyle = '#1e293b';
                ctx.fillText(n.id, x, y + r + 9 / globalScale);
              }
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
              const n = node as GraphNode & { x?: number; y?: number };
              const r = Math.max(4, Math.min(13, 3 + n.degree)) + 4;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, 2 * Math.PI);
              ctx.fill();
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            linkColor={(link: any) =>
              CATEGORY_COLORS[(link as GraphEdge).category] ?? '#94a3b8'
            }
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            linkWidth={(link: any) => Math.max(0.6, Math.min(4.5, (link as GraphEdge).weight))}
            linkDirectionalParticles={0}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            onNodeClick={(n: any) => {
              setSel({ type: 'node', id: (n as GraphNode).id });
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            onLinkClick={(l: any) => {
              setSel({ type: 'edge', edge: l as GraphEdge });
            }}
            onBackgroundClick={() => setSel(null)}
          />
        </div>
      </div>

      {/* ── side detail panel ── */}
      <aside className="w-64 shrink-0 overflow-y-auto rounded-lg border border-slate-200 bg-white p-3 text-xs">
        {!sel && (
          <div className="text-slate-400">
            <p className="font-medium text-slate-600 mb-1">角色关系图谱</p>
            <p>点击节点查看该角色所有关系；点击连线查看关系详情。</p>
            <p className="mt-2">节点大小 = 关联角色数；连线粗细 = 关系记录数；颜色 = 关系类型。</p>
          </div>
        )}

        {sel?.type === 'node' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-slate-700 text-sm">{sel.id}</span>
              <button onClick={() => setSel(null)} className="text-slate-400 hover:text-slate-600">✕</button>
            </div>
            <p className="text-slate-400 mb-2">
              {nodeEdges.length} 条关系
            </p>
            <ul className="space-y-1.5">
              {nodeEdges.map((e) => {
                const other = e.source === sel.id ? e.target : e.source;
                return (
                  <li
                    key={`edge-${String(e.source)}-${String(e.target)}`}
                    className="p-1.5 rounded border border-slate-100 hover:bg-slate-50 cursor-pointer"
                    onClick={() => setSel({ type: 'edge', edge: e })}
                  >
                    <div className="flex items-center gap-1.5">
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ background: CATEGORY_COLORS[e.category] }}
                      />
                      <span className="font-medium text-slate-700">{other}</span>
                      <span className="ml-auto text-slate-400">×{e.weight}</span>
                    </div>
                    {e.summaries[0] && (
                      <p className="text-slate-500 mt-0.5 line-clamp-2">{e.summaries[0]}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {sel?.type === 'edge' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-slate-700 text-sm">
                {sel.edge.source} ↔ {sel.edge.target}
              </span>
              <button onClick={() => setSel(null)} className="text-slate-400 hover:text-slate-600">✕</button>
            </div>
            <dl className="space-y-1.5">
              <Row label="类型" value={CATEGORY_LABELS[sel.edge.category] ?? sel.edge.category} />
              <Row label="极性" value={POLARITY_LABELS[sel.edge.polarity] ?? sel.edge.polarity} />
              <Row label="记录数" value={String(sel.edge.weight)} />
              <Row label="置信度" value={sel.edge.confidence.toFixed(2)} />
              {sel.edge.relation_types.length > 0 && (
                <Row label="原始类型" value={sel.edge.relation_types.join('、')} />
              )}
              {sel.edge.chapters.length > 0 && (
                <Row label="涉及章节" value={sel.edge.chapters.join('、')} />
              )}
            </dl>
            {sel.edge.summaries.length > 0 && (
              <div className="mt-2">
                <p className="text-slate-400 mb-1">摘要</p>
                <ul className="space-y-1 list-disc pl-4 text-slate-600">
                  {sel.edge.summaries.map((s) => <li key={`sum-${s.slice(0, 14)}`}>{s}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-slate-400 shrink-0">{label}</dt>
      <dd className="text-slate-700 break-all">{value}</dd>
    </div>
  );
}
