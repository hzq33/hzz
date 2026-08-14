/**
 * 窗口管理器：管理主面板、角色对话、功能窗口的生命周期
 */
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { app, BrowserWindow } from 'electron';

import { loadBounds, saveBounds } from './settings-store';
import { IPC, type FeatureWindowName } from './types';

const VITE_DEV_URL = 'http://localhost:3001';
const isDev = !app.isPackaged;

const WINDOW_BOUNDS: Record<string, { width: number; height: number }> = {
  panel: { width: 280, height: 580 },
  chat: { width: 520, height: 640 },
  library: { width: 820, height: 600 },
  world: { width: 1000, height: 700 },
  eval: { width: 820, height: 600 },
  settings: { width: 720, height: 640 },
};

const WEB_PREFS = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: false,
} as const;

function trackBounds(win: BrowserWindow, key: string): void {
  const persist = () => {
    if (!win.isDestroyed() && !win.isMinimized()) saveBounds(key, win.getBounds());
  };
  win.on('moved', persist);
  win.on('resized', persist);
  win.on('close', persist);
}

export class WindowManager {
  private panel: BrowserWindow | null = null;
  private chatWindows = new Map<string, BrowserWindow>();
  private featureWindows = new Map<string, BrowserWindow>();
  private quitting = false;

  prepareQuit(): void {
    this.quitting = true;
  }

  // ── 主面板 ──

  createPanel(): BrowserWindow {
    if (this.panel && !this.panel.isDestroyed()) {
      this.panel.show();
      this.panel.focus();
      return this.panel;
    }

    const bounds = { ...WINDOW_BOUNDS.panel, ...loadBounds('panel') };
    this.panel = new BrowserWindow({
      ...bounds,
      minWidth: 240,
      minHeight: 400,
      frame: false,
      autoHideMenuBar: true,
      resizable: true,
      webPreferences: {
        ...WEB_PREFS,
        preload: this.preloadPath('panel'),
      },
    });

    this.loadRenderer(this.panel, 'panel');
    trackBounds(this.panel, 'panel');
    this.panel.on('close', (e) => {
      if (this.quitting) return;
      e.preventDefault();
      this.panel?.hide();
    });

    return this.panel;
  }

  showPanel(): void {
    if (this.panel && !this.panel.isDestroyed()) {
      this.panel.show();
      this.panel.focus();
    }
  }

  // ── 角色对话窗口 ──

  openCharacterChat(characterId: string): void {
    console.info(`[WM] openCharacterChat: "${characterId}"`);
    const existing = this.chatWindows.get(characterId);
    if (existing && !existing.isDestroyed()) {
      console.info(`[WM] Reusing existing chat window for "${characterId}"`);
      existing.show();
      existing.focus();
      // 通知主面板：角色状态变为 in-chat
      this.sendToPanel(IPC.CHARACTER_STATUS_CHANGED, { characterId, status: 'in-chat' });
      return;
    }

    console.info(`[WM] Creating new chat window for "${characterId}"`);
    const bounds = { ...WINDOW_BOUNDS.chat, ...loadBounds(`chat:${characterId}`) };
    const win = new BrowserWindow({
      ...bounds,
      minWidth: 360,
      minHeight: 420,
      frame: false,
      autoHideMenuBar: true,
      webPreferences: {
        ...WEB_PREFS,
        preload: this.preloadPath('chat'),
        additionalArguments: [`--character-id=${characterId}`],
      },
    });

    this.loadRenderer(win, 'chat', { characterId });
    this.chatWindows.set(characterId, win);
    trackBounds(win, `chat:${characterId}`);

    // 通知主面板
    this.sendToPanel(IPC.CHARACTER_STATUS_CHANGED, { characterId, status: 'in-chat' });

    win.on('closed', () => {
      this.chatWindows.delete(characterId);
      this.sendToPanel(IPC.CHARACTER_STATUS_CHANGED, { characterId, status: 'online' });
    });

    // 聚焦时通知主面板清除未读
    win.on('focus', () => {
      this.sendToPanel(IPC.CHAT_WINDOW_FOCUSED, characterId);
    });
  }

  closeCharacterChat(characterId: string): void {
    const win = this.chatWindows.get(characterId);
    if (win && !win.isDestroyed()) {
      win.close();
    }
  }

  isChatWindowOpen(characterId: string): boolean {
    const win = this.chatWindows.get(characterId);
    return !!win && !win.isDestroyed();
  }

  getOpenChatCharacterIds(): string[] {
    return Array.from(this.chatWindows.keys()).filter((id) => {
      const win = this.chatWindows.get(id);
      return win && !win.isDestroyed();
    });
  }

  // ── 功能窗口 ──

  openFeatureWindow(name: FeatureWindowName): void {
    const existing = this.featureWindows.get(name);
    if (existing && !existing.isDestroyed()) {
      existing.show();
      existing.focus();
      return;
    }

    const bounds = { ...(WINDOW_BOUNDS[name] ?? { width: 800, height: 600 }), ...loadBounds(`feature:${name}`) };
    const win = new BrowserWindow({
      ...bounds,
      frame: false,
      autoHideMenuBar: true,
      webPreferences: {
        ...WEB_PREFS,
        preload: this.preloadPath('base'),
      },
    });

    // 功能窗口加载独立的 feature.html 入口，通过 query param 指定功能
    if (isDev) {
      void win.loadURL(`${VITE_DEV_URL}/feature.html?feature=${name}`);
    } else {
      void win.loadFile(path.join(__dirname, '..', 'dist', 'feature.html'), { query: { feature: name } });
    }

    this.featureWindows.set(name, win);
    trackBounds(win, `feature:${name}`);

    win.on('closed', () => {
      this.featureWindows.delete(name);
    });
  }

  // ── 全局操作 ──

  hideAllWindows(): void {
    this.panel?.hide();
    this.chatWindows.forEach((w) => !w.isDestroyed() && w.hide());
    this.featureWindows.forEach((w) => !w.isDestroyed() && w.hide());
  }

  restoreAllWindows(): void {
    this.showPanel();
    // 不自动恢复对话窗口，由用户操作触发
  }

  destroyAllWindows(): void {
    this.quitting = true;
    this.chatWindows.forEach((w) => !w.isDestroyed() && w.destroy());
    this.featureWindows.forEach((w) => !w.isDestroyed() && w.destroy());
    if (this.panel && !this.panel.isDestroyed()) this.panel.destroy();
  }

  /** 向主面板广播消息 */
  sendToPanel(channel: string, data: unknown): void {
    if (this.panel && !this.panel.isDestroyed()) {
      this.panel.webContents.send(channel, data);
    }
  }

  /** 通过 webContents 查找 characterId（IPC 回调用） */
  getCharacterIdByContents(contents: Electron.WebContents): string | null {
    for (const [characterId, win] of this.chatWindows) {
      if (!win.isDestroyed() && win.webContents === contents) {
        return characterId;
      }
    }
    return null;
  }

  /** 向所有对话窗口广播消息 */
  broadcastToChat(channel: string, data: unknown): void {
    this.chatWindows.forEach((w) => {
      if (!w.isDestroyed()) w.webContents.send(channel, data);
    });
  }

  /** 向所有窗口广播消息 */
  broadcastToAll(channel: string, data: unknown): void {
    this.sendToPanel(channel, data);
    this.broadcastToChat(channel, data);
    this.featureWindows.forEach((w) => {
      if (!w.isDestroyed()) w.webContents.send(channel, data);
    });
  }

  // ── 内部工具 ──

  /** 获取 preload 脚本的绝对路径 */
  private preloadPath(name: 'base' | 'panel' | 'chat'): string {
    return path.join(__dirname, 'preload', `${name}.js`);
  }

  /** 加载渲染进程页面 */
  private loadRenderer(
    win: BrowserWindow,
    name: 'panel' | 'chat',
    params?: Record<string, string>,
  ): void {
    if (isDev) {
      // 开发模式：从 Vite dev server 加载
      const url = new URL(`${VITE_DEV_URL}/${name}.html`);
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          url.searchParams.set(k, v);
        }
      }
      void win.loadURL(url.toString());
      // 仅在 --dev-tools 标志下打开 DevTools
      if (process.argv.includes('--dev-tools')) {
        win.webContents.openDevTools({ mode: 'detach' });
      }
    } else {
      // 生产模式：从本地文件加载
      const filePath = path.join(__dirname, '..', 'dist', `${name}.html`);
      if (params && Object.keys(params).length > 0) {
        // 带 query 参数时使用 loadURL
        const url = pathToFileURL(filePath);
        for (const [k, v] of Object.entries(params)) {
          url.searchParams.set(k, v);
        }
        void win.loadURL(url.toString());
      } else {
        void win.loadFile(filePath);
      }
    }
  }
}
