/**
 * 书目面板：上传导入（单文件/批量）、卷列表、删除、重命名。
 */
import { useRef, useState } from 'react';

import { uploadNovel, deleteNovelVolume, renameSeries } from '@/api/novels';
import { Badge, Empty, Modal, ProgressBar, SectionCard } from '@/components/ui/aura';
import { UPLOAD_STAGE_LABELS } from '@/lib/pollJob';
import type { NovelVolumeInfo } from '@/types';

type ImportMode = 'join' | 'new';

export default function BooksPanel({
  novels,
  seriesId,
  docId,
  onChanged,
  onSelectSeries,
  onSelectDoc,
  onMessage,
  onError,
}: {
  novels: NovelVolumeInfo[];
  seriesId: string;
  docId: string;
  onChanged: () => void;
  onSelectSeries: (sid: string) => void;
  onSelectDoc: (docId: string) => void;
  onMessage: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [importMode, setImportMode] = useState<ImportMode>('join');
  const [newSeriesName, setNewSeriesName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<{ message?: string; pct?: number } | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameDraft, setRenameDraft] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const resolveOpts = (): { series_id?: string; series_title?: string } | null => {
    if (importMode === 'join') return seriesId ? { series_id: seriesId } : {};
    const title = newSeriesName.trim();
    if (!title) {
      onError('请先填写新建系列的名称');
      return null;
    }
    return { series_title: title };
  };

  const doUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    const opts = resolveOpts();
    if (!opts) return;
    setUploading(true);
    onError('');
    try {
      let lastSeries = seriesId;
      for (let i = 0; i < list.length; i += 1) {
        setProgress({ message: `批量导入 ${i + 1}/${list.length}`, pct: 0 });
        const fileOpts = i === 0 ? opts : { series_id: lastSeries || undefined, series_title: opts.series_title };
        const res = await uploadNovel(
          list[i],
          fileOpts,
          (p) => setProgress({
            message: `${i + 1}/${list.length} · ${p?.message || UPLOAD_STAGE_LABELS[p?.stage || ''] || '处理中…'}`,
            pct: p?.pct,
          }),
        );
        lastSeries = res.series_id;
        onSelectSeries(res.series_id);
        onSelectDoc(res.doc_id);
      }
      setImportMode('join');
      setNewSeriesName('');
      void onChanged();
      onMessage(list.length > 1 ? `批量导入完成：${list.length} 个文件` : resHint());
    } catch (err) {
      onError(err instanceof Error ? err.message : '导入失败：请确认文件为 EPUB/TXT/MD');
    } finally {
      setUploading(false);
      setProgress(null);
    }
  };

  const resHint = () => '上传成功：已生成角色名录，可到「角色」页勾选生成人设卡。';

  const handleRename = async () => {
    const title = renameDraft.trim();
    if (!title || !seriesId) return;
    setRenaming(true);
    try {
      const res = await renameSeries(seriesId, title);
      setRenameOpen(false);
      void onChanged();
      onMessage(`系列已重命名为「${res.series_title || title}」`);
    } catch (err) {
      onError(err instanceof Error ? err.message : '重命名失败');
    } finally {
      setRenaming(false);
    }
  };

  const handleDelete = async (vol: NovelVolumeInfo) => {
    if (!confirm(`确定删除卷「${vol.volume_title || vol.title || vol.doc_id}」？此操作不可撤销，将联动清理角色名录与人设卡。`)) return;
    try {
      await deleteNovelVolume(vol.doc_id, vol.series_id);
      if (docId === vol.doc_id) onSelectDoc('');
      void onChanged();
      onMessage('已删除该卷并清理关联数据');
    } catch (err) {
      onError(err instanceof Error ? err.message : '删除失败');
    }
  };

  const seriesVolumes = novels.filter((v) => !seriesId || v.series_id === seriesId);
  const seriesOptions = [...new Set(novels.map((v) => v.series_id).filter(Boolean))].sort();

  return (
    <div className="space-y-4">
      {/* 上传区 */}
      <SectionCard title="导入小说" desc="支持 EPUB / TXT / MD，可批量；上传后同步入库并生成候选角色名录">
        <div className="space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1 p-1 rounded-xl bg-surface-2 border border-line">
              <button
                type="button"
                className={importMode === 'join' ? 'tab-active' : 'tab'}
                onClick={() => setImportMode('join')}
              >
                加入系列
              </button>
              <button
                type="button"
                className={importMode === 'new' ? 'tab-active' : 'tab'}
                onClick={() => setImportMode('new')}
              >
                新建系列
              </button>
            </div>
            {importMode === 'join' ? (
              seriesId ? (
                <Badge tone="brand">{seriesId}</Badge>
              ) : (
                <span className="text-xs text-muted">上传后将自动创建系列</span>
              )
            ) : (
              <input
                value={newSeriesName}
                onChange={(e) => setNewSeriesName(e.target.value)}
                placeholder="新系列名称（如：关于我转生变成史莱姆这档事）"
                className="input text-sm flex-1 min-w-[220px]"
              />
            )}
          </div>

          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (!uploading) void doUpload(e.dataTransfer.files);
            }}
            onClick={() => !uploading && fileRef.current?.click()}
            className={`border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition-all duration-200 ${
              dragOver ? 'border-brand bg-brand-tint scale-[1.01]' : 'border-line hover:border-brand/50 hover:bg-surface-2'
            }`}
          >
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".epub,.txt,.md,.markdown"
              className="hidden"
              onChange={(e) => {
                if (e.target.files && !uploading) void doUpload(e.target.files);
                e.target.value = '';
              }}
            />
            <div className="text-3xl mb-2">{uploading ? '⏳' : '📤'}</div>
            {uploading ? (
              <div className="space-y-2">
                <div className="text-sm text-ink">{progress?.message || '处理中…'}</div>
                <div className="max-w-sm mx-auto">
                  <ProgressBar pct={progress?.pct ?? 0} label={progress?.pct != null ? `${Math.round(progress.pct)}%` : undefined} />
                </div>
              </div>
            ) : (
              <>
                <div className="text-sm font-medium text-ink">点击或拖拽文件到此处上传</div>
                <div className="text-xs text-muted mt-1">EPUB/TXT/MD · 支持批量 · 大型文件请耐心等待管线处理</div>
              </>
            )}
          </div>
        </div>
      </SectionCard>

      {/* 书目列表 */}
      <SectionCard
        title="书目管理"
        desc={`共 ${novels.length} 卷${seriesOptions.length ? ` · ${seriesOptions.length} 个系列` : ''}`}
        actions={
          seriesId ? (
            <button type="button" className="btn-ghost btn-sm" onClick={() => { setRenameDraft(seriesId); setRenameOpen(true); }}>
              重命名系列
            </button>
          ) : undefined
        }
      >
        {seriesOptions.length === 0 ? (
          <Empty icon="📚" title="暂无书目" desc="上传第一部小说后，系列与卷将出现在这里。" />
        ) : (
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="text-xs text-muted">系列：</span>
            <div className="flex items-center gap-1 p-1 rounded-xl bg-surface-2 border border-line flex-wrap">
              <button type="button" className={!seriesId ? 'tab-active' : 'tab'} onClick={() => onSelectSeries('')}>
                全部
              </button>
              {seriesOptions.map((s) => (
                <button key={s} type="button" className={seriesId === s ? 'tab-active' : 'tab'} onClick={() => onSelectSeries(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {seriesVolumes.length > 0 && (
          <div className="grid gap-2.5 md:grid-cols-2">
            {seriesVolumes.map((v) => (
              <div
                key={v.doc_id}
                className={`group rounded-xl border p-3.5 transition-all duration-200 cursor-pointer ${
                  docId === v.doc_id ? 'border-brand/50 bg-brand-tint shadow-glow-sm' : 'border-line bg-surface-2 hover:border-brand/30 hover:shadow-card-hover'
                }`}
                onClick={() => onSelectDoc(v.doc_id === docId ? '' : v.doc_id)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink truncate">{v.volume_title || v.title || v.doc_id}</div>
                    <div className="text-[11px] text-muted mt-1 font-mono truncate">{v.doc_id}</div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); void handleDelete(v); }}
                    className="opacity-0 group-hover:opacity-100 p-1 rounded-lg text-faint hover:text-danger hover:bg-danger/10 transition-all"
                    title="删除卷"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                  </button>
                </div>
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  {v.block_counts && Object.entries(v.block_counts).map(([k, n]) => (
                    <Badge key={k}>{k} × {n}</Badge>
                  ))}
                  {v.chapter_count != null && <Badge>章 {v.chapter_count}</Badge>}
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <Modal open={renameOpen} onClose={() => setRenameOpen(false)} title="重命名系列" width="max-w-sm">
        <div className="space-y-3">
          <input value={renameDraft} onChange={(e) => setRenameDraft(e.target.value)} className="input" placeholder="系列展示名称" />
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setRenameOpen(false)}>取消</button>
            <button type="button" className="btn-primary" disabled={renaming} onClick={() => void handleRename()}>
              {renaming ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
