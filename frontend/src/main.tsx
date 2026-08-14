import React from 'react';

import ReactDOM from 'react-dom/client';

import App from '@/App';
import '@/styles/index.css';
import { ErrorBoundary } from '@/components/feedback';
import { ToastProvider } from '@/components/ui';
import { config } from '@/lib/config';
import { setupGlobalErrorHandler } from '@/lib/errors';
import { initSentry, initPerformanceMonitoring } from '@/lib/monitor';
import { ThemeProvider } from '@/lib/themeContext';

/* ── 应用启动序列 ── */
setupGlobalErrorHandler();
void initSentry();
void initPerformanceMonitoring();

if (config.debug) {
  console.info('[main] Aurora Agent 启动', {
    mode: config.mode,
    version: config.version,
  });
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
