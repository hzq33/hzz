/**
 * Electron 主进程与 Preload 共享的类型定义
 */
import { IPC } from './ipc-channels';

export { IPC };

/** 窗口名称 */
export type WindowName = 'panel' | 'chat' | 'library' | 'world' | 'eval' | 'settings';

/** 功能窗口名称（不含 panel 和 chat） */
export type FeatureWindowName = 'library' | 'world' | 'eval' | 'settings';

/** 角色联系人数据模型 */
export interface CharacterContact {
  characterId: string;
  name: string;
  title: string;
  aliases: string[];
  docId: string;
  seriesId: string;
  groupName: string;
  avatarColor: string;
  avatarText: string;
  status: 'online' | 'in-chat' | 'offline';
  lastMessage?: string;
  lastMessageTime?: number;
  unreadCount: number;
  hasChatHistory: boolean;
}

/** 角色状态变更事件 */
export interface CharacterStatusEvent {
  characterId: string;
  status: 'online' | 'in-chat' | 'offline';
}

/** 聊天消息接收事件 */
export interface ChatMessageEvent {
  characterId: string;
  preview: string;
  time: number;
}

/** 主题模式 */
export type ThemeMode = 'light' | 'dark';

/** Preload 暴露给渲染进程的 API 接口 */
export interface AuroraAPI {
  openCharacterChat(characterId: string): void;
  closeWindow(): void;
  minimizeWindow(): void;
  openFeatureWindow(name: FeatureWindowName): void;
  quitApp(): void;

  getTheme(): Promise<ThemeMode>;
  setTheme(mode: ThemeMode): Promise<void>;

  getCharacterId(): Promise<string | null>;
  notifyMessageReceived(preview: string): void;

  onCharacterStatusChanged(cb: (data: CharacterStatusEvent) => void): () => void;
  onChatMessageReceived(cb: (data: ChatMessageEvent) => void): () => void;
  onChatWindowFocused(cb: (characterId: string) => void): () => void;
  onThemeChanged(cb: (mode: ThemeMode) => void): () => void;
  onSettingsChanged(cb: () => void): () => void;
}

declare global {
  interface Window {
    aurora: AuroraAPI;
  }
}
