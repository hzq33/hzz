/**
 * 主题 hook —— 独立文件（满足 react-refresh 规则：文件只导出组件）。
 */
import { useContext } from 'react';

import { ThemeContext, type ThemeContextValue } from '@/lib/themeContext';

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
