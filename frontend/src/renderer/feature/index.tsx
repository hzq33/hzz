import React from 'react';

import ReactDOM from 'react-dom/client';

import { ErrorBoundary } from '@/components/feedback';
import { ToastProvider } from '@/components/ui';
import { ThemeProvider } from '@/lib/themeContext';
import '@/styles/index.css';

import { FeatureApp } from './FeatureApp';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <ToastProvider>
          <FeatureApp />
        </ToastProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
