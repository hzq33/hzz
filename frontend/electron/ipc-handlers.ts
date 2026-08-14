/**
 * IPC 处理器：注册主进程的 IPC 通道
 */
import { app, ipcMain, BrowserWindow } from 'electron';

import { IPC, type FeatureWindowName, type ThemeMode } from './types';

import type { WindowManager } from './window-manager';

export interface ThemeCallbacks {
  getTheme: () => ThemeMode;
  setTheme: (mode: ThemeMode) => void;
}

export function registerIpcHandlers(
  wm: WindowManager,
  themeCallbacks: ThemeCallbacks,
): void {
  ipcMain.on(IPC.OPEN_CHARACTER_CHAT, (_event, characterId: string) => {
    wm.openCharacterChat(characterId);
  });

  ipcMain.on(IPC.CLOSE_CHARACTER_CHAT, (_event, characterId: string) => {
    wm.closeCharacterChat(characterId);
  });

  ipcMain.on(IPC.OPEN_FEATURE_WINDOW, (_event, name: FeatureWindowName) => {
    wm.openFeatureWindow(name);
  });

  ipcMain.on(IPC.MINIMIZE_WINDOW, (event) => {
    BrowserWindow.fromWebContents(event.sender)?.minimize();
  });

  ipcMain.on(IPC.CLOSE_WINDOW, (event) => {
    BrowserWindow.fromWebContents(event.sender)?.close();
  });

  ipcMain.on(IPC.QUIT_APP, () => {
    wm.prepareQuit();
    app.quit();
  });

  ipcMain.handle(IPC.GET_THEME, () => themeCallbacks.getTheme());

  ipcMain.handle(IPC.SET_THEME, (_event, mode: ThemeMode) => {
    themeCallbacks.setTheme(mode);
  });

  ipcMain.handle(IPC.GET_CHARACTER_ID, (event) => {
    return wm.getCharacterIdByContents(event.sender);
  });

  ipcMain.on(IPC.NOTIFY_CHAT_MESSAGE, (event, preview: string) => {
    const characterId = wm.getCharacterIdByContents(event.sender);
    if (!characterId) return;
    wm.sendToPanel(IPC.CHAT_MESSAGE_RECEIVED, {
      characterId,
      preview: String(preview || '').slice(0, 120),
      time: Date.now(),
    });
  });

  ipcMain.on(IPC.CHAT_MESSAGE_RECEIVED, (_event, data: { characterId: string; preview: string; time: number }) => {
    wm.sendToPanel(IPC.CHAT_MESSAGE_RECEIVED, data);
  });

  ipcMain.on(IPC.CHAT_WINDOW_FOCUSED, (_event, characterId: string) => {
    wm.sendToPanel(IPC.CHAT_WINDOW_FOCUSED, characterId);
  });
}
