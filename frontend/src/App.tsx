import { lazy, Suspense } from 'react';

import { HashRouter, Routes, Route } from 'react-router-dom';

import { PageSkeleton } from '@/components/feedback';
import Sidebar from '@/components/layout/Sidebar';

/* 路由级懒加载：首屏仅加载对话页 */
const ChatPage = lazy(() => import('@/pages/ChatPage'));
const ImpersonationPage = lazy(() => import('@/pages/ImpersonationPage'));
const LibraryPage = lazy(() => import('@/pages/LibraryPage'));
const WorldPage = lazy(() => import('@/pages/WorldPage'));
const EvalPage = lazy(() => import('@/pages/EvalPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));

function load(page: React.ReactNode) {
  return <Suspense fallback={<PageSkeleton rows={6} />}>{page}</Suspense>;
}

export default function App() {
  return (
    <HashRouter>
      <div className="flex h-screen bg-bg text-ink">
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <Routes>
            <Route path="/" element={load(<ChatPage />)} />
            <Route path="/impersonation" element={load(<ImpersonationPage />)} />
            <Route path="/library" element={load(<LibraryPage />)} />
            <Route path="/world" element={load(<WorldPage />)} />
            <Route path="/eval" element={load(<EvalPage />)} />
            <Route path="/settings" element={load(<SettingsPage />)} />
            <Route path="*" element={load(<ChatPage />)} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}
