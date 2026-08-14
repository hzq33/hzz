/**
 * Electron 主进程入口
 * 管理应用生命周期、窗口创建、IPC 通信、托盘退出
 */
import { app, BrowserWindow } from 'electron';

import { registerIpcHandlers } from './ipc-handlers';
import { loadTheme, saveTheme } from './settings-store';
import { createTray } from './tray';
import { IPC, type ThemeMode } from './types';
import { WindowManager } from './window-manager';

let windowManager: WindowManager;
let currentTheme: ThemeMode = 'dark';

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

app.on('second-instance', () => {
  windowManager?.showPanel();
});

void app.whenReady().then(() => {
  currentTheme = loadTheme();
  windowManager = new WindowManager();
  registerIpcHandlers(windowManager, {
    getTheme: () => currentTheme,
    setTheme: (mode: ThemeMode) => {
      currentTheme = mode;
      saveTheme(mode);
      windowManager.broadcastToAll(IPC.THEME_CHANGED, mode);
    },
  });

  windowManager.createPanel();
  createTray(windowManager, () => {
    windowManager.prepareQuit();
    app.quit();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      windowManager.createPanel();
    } else {
      windowManager.showPanel();
    }
  });
});

app.on('window-all-closed', () => {
  // 托盘驻留；真正退出走 prepareQuit + app.quit
});

app.on('before-quit', () => {
  windowManager?.prepareQuit();
  windowManager?.destroyAllWindows();
});
