/**
 * Shared preload bridge. Main process compiles this to CJS so preload can
 * `require` sibling modules (sandbox is off on BrowserWindows).
 */
import { contextBridge, ipcRenderer } from 'electron';

import { IPC } from '../ipc-channels';

import type { FeatureWindowName, ThemeMode } from '../types';

export type PreloadKind = 'base' | 'panel' | 'chat';

function extractCharacterId(): string | null {
  const arg = process.argv.find((a) => a.startsWith('--character-id='));
  return arg ? arg.split('=')[1] : null;
}

export function exposeAurora(kind: PreloadKind): void {
  contextBridge.exposeInMainWorld('aurora', {
    closeWindow: () => ipcRenderer.send(IPC.CLOSE_WINDOW),
    minimizeWindow: () => ipcRenderer.send(IPC.MINIMIZE_WINDOW),
    quitApp: () => ipcRenderer.send(IPC.QUIT_APP),

    openCharacterChat: (characterId: string): void => {
      if (kind === 'panel') ipcRenderer.send(IPC.OPEN_CHARACTER_CHAT, characterId);
    },
    openFeatureWindow: (name: FeatureWindowName): void => {
      if (kind === 'panel') ipcRenderer.send(IPC.OPEN_FEATURE_WINDOW, name);
    },

    getCharacterId: (): Promise<string | null> => {
      if (kind !== 'chat') return Promise.resolve(null);
      const fromArg = extractCharacterId();
      if (fromArg) return Promise.resolve(fromArg);
      return ipcRenderer.invoke(IPC.GET_CHARACTER_ID);
    },

    notifyMessageReceived: (preview: string): void => {
      if (kind !== 'chat') return;
      ipcRenderer.send(IPC.NOTIFY_CHAT_MESSAGE, preview);
    },

    getTheme: (): Promise<ThemeMode> => ipcRenderer.invoke(IPC.GET_THEME),
    setTheme: (mode: ThemeMode): Promise<void> => ipcRenderer.invoke(IPC.SET_THEME, mode),

    onThemeChanged: (cb: (mode: ThemeMode) => void): (() => void) => {
      const listener = (_e: unknown, mode: ThemeMode) => cb(mode);
      ipcRenderer.on(IPC.THEME_CHANGED, listener);
      return () => {
        ipcRenderer.removeListener(IPC.THEME_CHANGED, listener);
      };
    },
    onSettingsChanged: (cb: () => void): (() => void) => {
      const listener = () => cb();
      ipcRenderer.on(IPC.SETTINGS_CHANGED, listener);
      return () => {
        ipcRenderer.removeListener(IPC.SETTINGS_CHANGED, listener);
      };
    },
    onCharacterStatusChanged: (cb: (data: { characterId: string; status: string }) => void): (() => void) => {
      const listener = (_e: unknown, data: { characterId: string; status: string }) => cb(data);
      ipcRenderer.on(IPC.CHARACTER_STATUS_CHANGED, listener);
      return () => {
        ipcRenderer.removeListener(IPC.CHARACTER_STATUS_CHANGED, listener);
      };
    },
    onChatMessageReceived: (cb: (data: { characterId: string; preview: string; time: number }) => void): (() => void) => {
      const listener = (_e: unknown, data: { characterId: string; preview: string; time: number }) => cb(data);
      ipcRenderer.on(IPC.CHAT_MESSAGE_RECEIVED, listener);
      return () => {
        ipcRenderer.removeListener(IPC.CHAT_MESSAGE_RECEIVED, listener);
      };
    },
    onChatWindowFocused: (cb: (characterId: string) => void): (() => void) => {
      const listener = (_e: unknown, characterId: string) => cb(characterId);
      ipcRenderer.on(IPC.CHAT_WINDOW_FOCUSED, listener);
      return () => {
        ipcRenderer.removeListener(IPC.CHAT_WINDOW_FOCUSED, listener);
      };
    },
  });
}
