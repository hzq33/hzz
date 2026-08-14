/**
 * 设置页（Aurora 重新设计）—— LLM 调用点配置 + 服务健康状态。
 */
import { useCallback, useEffect, useState } from 'react';

import { fetchHealth } from '@/api/health';
import { fetchLlmConfig, saveLlmEndpoint, testLlmEndpoint, fetchMemoryConfig, saveMemoryConfig } from '@/api/settings';
import { Badge, Button, SectionCard, SelectInput, SettingRow, Spinner, TextInput, Toggle } from '@/components/ui';
import { useToast } from '@/components/ui/Toast';
import { toUserErrorMessage } from '@/lib/errors';
import type { LlmConfigResponse, LlmEndpointInfo, LlmEndpointEdit, MemoryConfig } from '@/types';

function EndpointCard({
  ep,
  providers,
  onSaved,
}: {
  ep: LlmEndpointInfo;
  providers: LlmConfigResponse['providers'];
  onSaved: () => void;
}) {
  const [edit, setEdit] = useState<LlmEndpointEdit>({
    provider: ep.config.provider,
    model: ep.config.model,
    temperature: ep.config.temperature,
    max_tokens: ep.config.max_tokens,
    enabled: ep.config.enabled,
    thinking: ep.config.thinking,
  });
  const [keyDirty, setKeyDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState('');
  const toast = useToast();

  const providerMeta = providers.find((p) => p.key === edit.provider);
  const baseUrl = edit.base_url ?? providerMeta?.base_url ?? '';

  const buildPayload = (): LlmEndpointEdit => ({
    provider: edit.provider,
    base_url: baseUrl,
    model: edit.model,
    temperature: edit.temperature,
    max_tokens: edit.max_tokens,
    enabled: edit.enabled,
    thinking: edit.thinking,
    ...(keyDirty && edit.api_key ? { api_key: edit.api_key } : {}),
  });

  const save = async () => {
    setSaving(true);
    setMsg('');
    try {
      await saveLlmEndpoint(ep.key, buildPayload());
      setKeyDirty(false);
      setMsg('已保存 ✓');
      toast.push('调用点已保存', 'ok');
      onSaved();
    } catch (e) {
      setMsg(`保存失败: ${toUserErrorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setMsg('');
    try {
      const r = await testLlmEndpoint(ep.key, buildPayload());
      setMsg(r.ok ? `连接成功 ✓ ${r.model || ''}` : `连接失败: ${r.error || ''}`);
    } catch (e) {
      setMsg(`测试失败: ${toUserErrorMessage(e)}`);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface-2 p-4 space-y-3 card-hover">
      <SettingRow label={ep.label}>
        <Badge tone={edit.enabled ? 'ok' : 'neutral'}>{edit.enabled ? '启用' : '停用'}</Badge>
        <Toggle
          checked={!!edit.enabled}
          onChange={(enabled) => setEdit((v) => ({ ...v, enabled }))}
          label="启用该调用点"
        />
      </SettingRow>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <SelectInput
          label="服务商"
          value={edit.provider}
          onChange={(e) => setEdit((v) => ({ ...v, provider: e.target.value }))}
        >
          {providers.map((p) => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </SelectInput>
        <TextInput
          label="模型"
          value={edit.model ?? ''}
          onChange={(e) => setEdit((v) => ({ ...v, model: e.target.value }))}
          placeholder="model id"
        />
        {providerMeta?.base_url ? (
          <div className="md:col-span-2">
            <TextInput
              label="Base URL"
              className="font-mono text-[10px]"
              value={baseUrl}
              disabled
            />
          </div>
        ) : null}
        <TextInput
          label="API Key（留空保持不变）"
          type="password"
          placeholder={ep.config.has_api_key ? '••••••••（已配置）' : '未配置'}
          onChange={(e) => {
            setEdit((v) => ({ ...v, api_key: e.target.value }));
            setKeyDirty(true);
          }}
        />
        <div className="grid grid-cols-2 gap-2">
          <TextInput
            label="Temperature"
            type="number"
            step="0.1"
            value={edit.temperature ?? 0}
            onChange={(e) => setEdit((v) => ({ ...v, temperature: Number(e.target.value) }))}
          />
          <TextInput
            label="Max Tokens"
            type="number"
            value={edit.max_tokens ?? 0}
            onChange={(e) => setEdit((v) => ({ ...v, max_tokens: Number(e.target.value) }))}
          />
        </div>
        <SelectInput
          label="思考模式"
          value={edit.thinking ?? 'auto'}
          onChange={(e) => setEdit((v) => ({ ...v, thinking: e.target.value as 'auto' | 'on' | 'off' }))}
        >
          <option value="auto">自动</option>
          <option value="on">开启</option>
          <option value="off">关闭</option>
        </SelectInput>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" disabled={saving} onClick={() => void save()}>
          {saving ? <Spinner size={11} className="text-white" /> : null}
          保存
        </Button>
        <Button tone="soft" size="sm" disabled={testing} onClick={() => void test()}>
          {testing ? <Spinner size={11} className="text-brand" /> : null}
          测试连接
        </Button>
        {msg && <span className={`text-xs ${msg.includes('失败') ? 'text-danger' : 'text-ok'}`}>{msg}</span>}
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [cfg, setCfg] = useState<LlmConfigResponse | null>(null);
  const [memCfg, setMemCfg] = useState<MemoryConfig | null>(null);
  const [memSaving, setMemSaving] = useState(false);
  const [memMsg, setMemMsg] = useState('');
  const [health, setHealth] = useState<string>('检测中…');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [c, h, m] = await Promise.all([fetchLlmConfig(), fetchHealth(), fetchMemoryConfig()]);
      setCfg(c);
      setHealth(h.status === 'ok' ? '在线' : h.status);
      setMemCfg(m);
    } catch (e) {
      setError(toUserErrorMessage(e, '无法加载配置'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups: Record<string, LlmEndpointInfo[]> = {};
  (cfg?.endpoints || []).forEach((ep) => {
    (groups[ep.group] = groups[ep.group] || []).push(ep);
  });

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-brand/10 blur-3xl" />

      <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-brand to-accent flex items-center justify-center shadow-sm shadow-brand/30">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
        </div>
        <div className="flex-1">
          <h2 className="text-sm font-semibold">设置</h2>
          <p className="text-[11px] text-faint">LLM 调用点配置与连接测试</p>
        </div>
        <Badge tone={health.includes('在线') ? 'ok' : 'warn'}>服务 {health}</Badge>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5 relative z-0">
        <div className="max-w-3xl mx-auto space-y-4">
          {error && (
            <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-2.5 text-sm text-danger">{error}</div>
          )}
          {loading ? (
            <div className="py-12 text-center text-muted text-sm">加载中…</div>
          ) : (
            <>
              {/* 记忆与上下文配置 */}
              <SectionCard title="记忆与上下文" desc="角色扮演会话的上下文窗口与自动压缩">
                <div className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <TextInput
                      label="上下文上限 (tokens)"
                      type="number"
                      min={100}
                      max={200000}
                      step={100}
                      value={memCfg?.max_history_tokens ?? ''}
                      onChange={(e) =>
                        setMemCfg((s) => (s ? { ...s, max_history_tokens: Number(e.target.value) } : s))
                      }
                    />
                    <TextInput
                      label="保留最近完整轮数"
                      type="number"
                      min={1}
                      max={100}
                      value={memCfg?.summarize_keep_turns ?? ''}
                      onChange={(e) =>
                        setMemCfg((s) => (s ? { ...s, summarize_keep_turns: Number(e.target.value) } : s))
                      }
                    />
                    <TextInput
                      label="压缩阈值 (0.1-1.0)"
                      type="number"
                      min={0.1}
                      max={1}
                      step={0.05}
                      value={memCfg?.summarize_threshold ?? ''}
                      onChange={(e) =>
                        setMemCfg((s) => (s ? { ...s, summarize_threshold: Number(e.target.value) } : s))
                      }
                    />
                  </div>
                  <SettingRow label="启用上下文压缩">
                    <Toggle
                      checked={Boolean(memCfg?.enable_summarization)}
                      onChange={(enable_summarization) =>
                        setMemCfg((s) => (s ? { ...s, enable_summarization } : s))
                      }
                      label="启用上下文压缩"
                    />
                  </SettingRow>
                  <p className="text-[11px] text-faint -mt-2">早期轮次折叠为摘要，减少遗忘与前后矛盾。</p>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={memSaving || !memCfg}
                      onClick={() => {
                        void (async () => {
                          if (!memCfg) return;
                          setMemSaving(true);
                          setMemMsg('');
                          try {
                            const saved = await saveMemoryConfig(memCfg);
                            setMemCfg(saved);
                            setMemMsg('已保存（新会话生效）');
                          } catch (e) {
                            setMemMsg(`保存失败：${toUserErrorMessage(e, '未知错误')}`);
                          } finally {
                            setMemSaving(false);
                          }
                        })();
                      }}
                    >
                      {memSaving ? '保存中…' : '保存配置'}
                    </button>
                    {memMsg && (
                      <span className={`text-xs ${memMsg.includes('失败') ? 'text-danger' : 'text-ok'}`}>
                        {memMsg}
                      </span>
                    )}
                  </div>
                </div>
              </SectionCard>
              {Object.entries(groups).map(([group, eps]) => (
                <SectionCard key={group} title={group} desc={`${eps.length} 个调用点`}>
                  <div className="space-y-3">
                    {eps.map((ep) => (
                      <EndpointCard key={ep.key} ep={ep} providers={cfg?.providers || []} onSaved={() => void load()} />
                    ))}
                  </div>
                </SectionCard>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
