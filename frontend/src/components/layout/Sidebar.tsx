import { NavLink } from 'react-router-dom';

import { useHealth } from '@/hooks/useHealth';
import { useTheme } from '@/hooks/useTheme';

/* ── Icons ── */

const icons: Record<string, React.ReactNode> = {
  chat: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
  ),
  mask: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
  ),
  book: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
  ),
  globe: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
  ),
  gauge: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 15l3.5-3.5" /><path d="M20.3 18a10 10 0 1 0-16.6 0" /><circle cx="12" cy="15" r="1" /></svg>
  ),
  gear: (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
  ),
};

const NAV = [
  { to: '/', label: '对话', icon: 'chat', end: true },
  { to: '/impersonation', label: '角色扮演', icon: 'mask' },
  { to: '/library', label: '知识库', icon: 'book' },
  { to: '/world', label: '世界体系', icon: 'globe' },
  { to: '/eval', label: '评估中心', icon: 'gauge' },
  { to: '/settings', label: '设置', icon: 'gear' },
];

export default function Sidebar() {
  const { resolved, toggle } = useTheme();
  const { status } = useHealth();

  return (
    <aside className="w-[230px] shrink-0 h-full flex flex-col border-r border-line bg-surface/70 backdrop-blur-md">
      {/* Logo */}
      <div className="px-5 pt-5 pb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-brand to-accent flex items-center justify-center shadow-lg shadow-brand/30">
            <span className="text-white font-bold text-lg">A</span>
          </div>
          <div>
            <div className="text-sm font-bold text-ink tracking-wide">Aurora Agent</div>
            <div className="text-[10px] text-faint">小说智能体工作台</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200
               ${
                 isActive
                   ? 'bg-brand/12 text-brand-strong dark:text-brand shadow-soft'
                   : 'text-muted hover:text-ink hover:bg-surface-3'
               }`
            }
          >
            <span className="opacity-90">{icons[item.icon]}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer: health + theme */}
      <div className="p-3 border-t border-line space-y-1.5">
        <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface-2">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                status === 'ok' ? 'bg-ok' : status === 'down' ? 'bg-danger' : 'bg-warn animate-pulse'
              }`}
            />
            <span className="text-xs text-muted">服务{status === 'ok' ? '在线' : status === 'down' ? '离线' : '检测中'}</span>
          </div>
          <button
            type="button"
            onClick={toggle}
            className="p-1.5 rounded-lg text-muted hover:text-ink hover:bg-surface-3 transition-colors"
            title={resolved === 'dark' ? '切换到浅色' : '切换到深色'}
          >
            {resolved === 'dark' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" /></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>
            )}
          </button>
        </div>
      </div>
    </aside>
  );
}
