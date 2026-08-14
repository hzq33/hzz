/**
 * FeatureApp — 功能窗口（Electron 多窗口 / 知识库/世界体系/评估/设置）。
 *
 * 与主 SPA 不同：无侧边栏，仅渲染单个功能页面 + 自定义标题栏。
 * 通过 URL query param ?feature=xxx 指定要渲染的功能。
 */
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';

import { PageSkeleton } from '@/components/feedback';
import { Titlebar, WindowShell } from '@/components/ui';
import { useTheme } from '@/hooks/useTheme';

/* ── 懒加载页面组件 ── */

const LibraryPage = lazy(() => import('@/pages/LibraryPage'));
const WorldPage = lazy(() => import('@/pages/WorldPage'));
const EvalPage = lazy(() => import('@/pages/EvalPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));

/* ── 类型 ── */

type FeatureName = 'library' | 'world' | 'eval' | 'settings';

/* ── 图标 ── */

const BookIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

const GlobeIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

const GaugeIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 15l3.5-3.5" />
    <path d="M20.3 18a10 10 0 1 0-16.6 0" />
    <circle cx="12" cy="15" r="1" />
  </svg>
);

const GearIcon = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

/* ── 功能元数据 ── */

const FEATURES: Record<FeatureName, { title: string; icon: ReactNode }> = {
  library:  { title: '知识库',   icon: BookIcon },
  world:    { title: '世界体系', icon: GlobeIcon },
  eval:     { title: '评估中心', icon: GaugeIcon },
  settings: { title: '设置',     icon: GearIcon },
};

/* ── 主组件 ── */

export function FeatureApp() {
  const [feature, setFeature] = useState<FeatureName | null>(null);
  const { setMode } = useTheme();

  /* ── 从 URL 读取功能名称 ── */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const name = params.get('feature') as FeatureName | null;
    if (name && name in FEATURES) {
      setFeature(name);
      document.title = `Aurora Agent · ${FEATURES[name].title}`;
    }
  }, []);

  /* ── Electron 窗口默认深色模式 ── */
  useEffect(() => {
    try {
      const saved = localStorage.getItem('aurora_theme');
      if (!saved || saved === 'system') {
        setMode('dark');
      }
    } catch { /* ignore */ }
  }, [setMode]);

  /* ── 渲染 ── */

  if (!feature) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted">
        未知功能窗口
      </div>
    );
  }

  const meta = FEATURES[feature];

  return (
    <WindowShell variant="feature">
      <Titlebar
        showTheme
        icon={
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-accent text-white">
            {meta.icon}
          </div>
        }
        title={meta.title}
      />
      <div className="flex-1 overflow-y-auto">
        <Suspense fallback={<PageSkeleton rows={6} />}>
          {feature === 'library' && <LibraryPage />}
          {feature === 'world' && <WorldPage />}
          {feature === 'eval' && <EvalPage />}
          {feature === 'settings' && <SettingsPage />}
        </Suspense>
      </div>
    </WindowShell>
  );
}
