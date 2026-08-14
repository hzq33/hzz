/**
 * PanelApp — QQ 风格角色好友列表面板（Electron 多窗口 / 窄窗口 ~280px）。
 *
 * 功能：
 *  - 挂载时 fetchCharacters，过滤可扮演角色（has_card || status==='ready'）
 *  - 按 series_id 分组，组头显示系列名
 *  - 每个联系人：渐变头像 + 状态点 + 最后消息 + 未读徽章 + 时间
 *  - 搜索栏按 name / aliases 过滤
 *  - 点击联系人 → window.aurora?.openCharacterChat(name)
 *  - 底栏 4 个功能按钮 + 主题切换
 *  - 窗口控制：最小化
 *  - IPC 事件监听：状态变更 / 消息接收 / 窗口聚焦
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { fetchCharacters } from '@/api/characters';
import { Empty, IconMoon, IconSun, Spinner, Titlebar, WindowShell } from '@/components/ui';
import { ContactRow, type ContactStatus } from '@/components/ui/ContactRow';
import { ContextMenu, type MenuItem } from '@/components/ui/Dropdown';
import { SearchField } from '@/components/ui/SearchField';
import { useTheme } from '@/hooks/useTheme';
import type { CharacterInfo } from '@/types';
import type {
  CharacterStatusEvent,
  ChatMessageEvent,
  FeatureWindowName,
} from '@/types/electron';

/* ── 联系人运行时状态 ── */

interface ContactState {
  status: ContactStatus;
  lastMessage: string;
  lastMessageTime: number;
  unreadCount: number;
}

const DEFAULT_STATE: ContactState = {
  status: 'online',
  lastMessage: '',
  lastMessageTime: 0,
  unreadCount: 0,
};

const STATES_KEY = 'aurora_contact_states';

function loadContactStates(): Map<string, ContactState> {
  try {
    const raw = localStorage.getItem(STATES_KEY);
    if (!raw) return new Map();
    const obj = JSON.parse(raw) as Record<string, ContactState>;
    return new Map(Object.entries(obj));
  } catch {
    return new Map();
  }
}

function saveContactStates(m: Map<string, ContactState>): void {
  try {
    const obj: Record<string, ContactState> = {};
    m.forEach((v, k) => {
      obj[k] = v;
    });
    localStorage.setItem(STATES_KEY, JSON.stringify(obj));
  } catch {
    /* noop */
  }
}

/* ── 图标 ── */

const BookIcon = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const GlobeIcon = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

const GaugeIcon = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 15l3.5-3.5" />
    <path d="M20.3 18a10 10 0 1 0-16.6 0" />
    <circle cx="12" cy="15" r="1" />
  </svg>
);

const GearIcon = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

const FOOTER_FEATURES: { name: FeatureWindowName; label: string; icon: JSX.Element }[] = [
  { name: 'library', label: '知识库', icon: BookIcon },
  { name: 'world', label: '世界体系', icon: GlobeIcon },
  { name: 'eval', label: '评估', icon: GaugeIcon },
  { name: 'settings', label: '设置', icon: GearIcon },
];

const WarnIcon = (
  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

const RetryIcon = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 4 23 10 17 10" />
    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
  </svg>
);

/* ── 主组件 ── */

export function PanelApp() {
  const { resolved, toggle, setMode } = useTheme();

  const [characters, setCharacters] = useState<CharacterInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [contactStates, setContactStates] = useState<Map<string, ContactState>>(() => loadContactStates());
  const [menu, setMenu] = useState<{ x: number; y: number; name: string } | null>(null);

  /* ── Electron 窗口默认深色模式 ── */
  useEffect(() => {
    try {
      const saved = localStorage.getItem('aurora_theme');
      if (!saved || saved === 'system') {
        setMode('dark');
      }
    } catch { /* ignore */ }
  }, [setMode]);

  /* ── 拉取角色列表（可重试） ── */
  const loadCharacters = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const all = await fetchCharacters({ signal });
      // 显示所有角色（未建卡的角色也会显示，但聊天时可能提示需要建卡）
      setCharacters(all);
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : '加载角色列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── 挂载时拉取角色列表 ── */
  useEffect(() => {
    const controller = new AbortController();
    void loadCharacters(controller.signal);
    return () => { controller.abort(); };
  }, [loadCharacters]);

  /* ── 角色列表变化时同步联系人状态 Map ── */
  useEffect(() => {
    if (characters.length === 0) return;
    setContactStates((prev) => {
      const next = new Map(prev);
      const validNames = new Set<string>();
      for (const c of characters) {
        validNames.add(c.name);
        if (!next.has(c.name)) {
          // 初始化：默认在线、0 未读
          next.set(c.name, { ...DEFAULT_STATE });
        }
      }
      // 清理已不存在的联系人
      for (const key of Array.from(next.keys())) {
        if (!validNames.has(key)) next.delete(key);
      }
      return next;
    });
  }, [characters]);

  useEffect(() => {
    saveContactStates(contactStates);
  }, [contactStates]);

  /* ── IPC 事件监听（带清理） ── */
  useEffect(() => {
    const api = window.aurora;
    if (!api) return;

    // 角色状态变更
    const unsubStatus = api.onCharacterStatusChanged((data: CharacterStatusEvent) => {
      setContactStates((prev) => {
        const next = new Map(prev);
        const existing = next.get(data.characterId) ?? { ...DEFAULT_STATE };
        next.set(data.characterId, { ...existing, status: data.status });
        return next;
      });
    });

    // 收到聊天消息 → 更新最后消息 & 未读 +1
    const unsubMessage = api.onChatMessageReceived((data: ChatMessageEvent) => {
      setContactStates((prev) => {
        const next = new Map(prev);
        const existing = next.get(data.characterId) ?? { ...DEFAULT_STATE };
        next.set(data.characterId, {
          ...existing,
          lastMessage: data.preview,
          lastMessageTime: data.time,
          unreadCount: existing.unreadCount + 1,
        });
        return next;
      });
    });

    // 聊天窗口聚焦 → 清零未读
    const unsubFocus = api.onChatWindowFocused((characterId: string) => {
      setContactStates((prev) => {
        const next = new Map(prev);
        const existing = next.get(characterId);
        if (existing) {
          next.set(characterId, { ...existing, unreadCount: 0 });
        }
        return next;
      });
    });

    return () => {
      unsubStatus?.();
      unsubMessage?.();
      unsubFocus?.();
    };
  }, []);

  /* ── 搜索过滤 ── */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return characters;
    return characters.filter((c) => {
      const byName = c.name.toLowerCase().includes(q);
      const byAlias = c.aliases?.some((a) => a.toLowerCase().includes(q));
      return byName || byAlias;
    });
  }, [characters, query]);

  /* ── 按 series_id 分组 ── */
  const groups = useMemo(() => {
    const map = new Map<string, { label: string; items: CharacterInfo[] }>();
    for (const c of filtered) {
      const key = c.series_id || '__other__';
      if (!map.has(key)) {
        const label = c.source_work || c.source || c.series_id || '其他';
        map.set(key, { label, items: [] });
      }
      map.get(key)!.items.push(c);
    }
    return Array.from(map.values());
  }, [filtered]);

  /* ── 事件处理 ── */
  const handleContactClick = useCallback((name: string) => {
    window.aurora?.openCharacterChat(name);
  }, []);

  const handleFeature = useCallback((name: FeatureWindowName) => {
    window.aurora?.openFeatureWindow(name);
  }, []);

  /* ── 渲染 ── */
  return (
    <WindowShell variant="panel">
      <Titlebar
        icon={
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-accent shadow-lg shadow-brand/30">
            <span className="text-base font-bold text-white">A</span>
          </div>
        }
        title="Aurora Agent"
        subtitle="角色列表"
      />

      {/* ── 搜索栏 ── */}
      <div className="border-b border-line/60 px-3 py-2 backdrop-blur-sm">
        <SearchField value={query} onChange={setQuery} placeholder="搜索角色..." />
      </div>

      {/* ── 联系人列表 ── */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex animate-fade-in items-center justify-center py-16 text-brand">
            <Spinner size={22} />
          </div>
        ) : error ? (
          <div className="animate-fade-in flex flex-col items-center px-4 py-12 text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-danger/10 text-danger">
              {WarnIcon}
            </div>
            <div className="mb-1 text-sm font-medium text-ink">无法连接到服务</div>
            <div className="mb-4 max-w-[200px] text-xs text-muted leading-relaxed">{error}</div>
            <button
              type="button"
              onClick={() => { void loadCharacters(); }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand/10 px-3 py-1.5 text-xs font-medium text-brand transition-all duration-200 hover:bg-brand/20 hover:shadow-glow-sm active:scale-95"
            >
              {RetryIcon}
              重试
            </button>
          </div>
        ) : groups.length === 0 ? (
          <div className="animate-fade-in">
            <Empty
              icon="🎭"
              title="还没有可扮演的角色"
              desc="导入小说后，系统会自动提取并生成可扮演的角色卡片"
            />
          </div>
        ) : (
          <div className="pb-2">
            {groups.map((group) => (
              <div key={group.label}>
                {/* 分组头 */}
                <div className="sticky top-0 z-10 bg-surface/80 px-3 py-1.5 backdrop-blur-md">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-faint">
                    {group.label}
                    <span className="ml-1.5 text-faint/60">{group.items.length}</span>
                  </span>
                </div>
                {/* 联系人 */}
                {group.items.map((c, idx) => {
                  const state = contactStates.get(c.name);
                  const status: ContactStatus = state?.status ?? 'online';
                  return (
                    <ContactRow
                      key={c.name}
                      name={c.name}
                      preview={state?.lastMessage}
                      time={state?.lastMessageTime}
                      unread={state?.unreadCount ?? 0}
                      status={status}
                      delayMs={Math.min(idx * 40, 400)}
                      onClick={() => handleContactClick(c.name)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setMenu({ x: e.clientX, y: e.clientY, name: c.name });
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 底栏：功能按钮 + 主题切换 ── */}
      <footer className="flex items-center justify-between glass-strong border-t border-line/60 px-2 py-1.5">
        <div className="flex items-center gap-0.5">
          {FOOTER_FEATURES.map((f) => (
            <button
              key={f.name}
              type="button"
              onClick={() => handleFeature(f.name)}
              aria-label={f.label}
              title={f.label}
              className="rounded-lg p-2 text-muted transition-all duration-200 hover:bg-surface-3 hover:text-brand hover:shadow-glow-sm"
            >
              {f.icon}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={toggle}
          aria-label="切换主题"
          title={resolved === 'dark' ? '切换到浅色' : '切换到深色'}
          className="rounded-lg p-2 text-muted transition-all duration-200 hover:bg-surface-3 hover:text-ink"
        >
          {resolved === 'dark' ? IconSun : IconMoon}
        </button>
      </footer>

      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={
            [
              { id: 'open', label: '打开对话', onClick: () => handleContactClick(menu.name) },
              {
                id: 'library',
                label: '打开知识库',
                onClick: () => window.aurora?.openFeatureWindow('library'),
              },
            ] satisfies MenuItem[]
          }
        />
      ) : null}
    </WindowShell>
  );
}
