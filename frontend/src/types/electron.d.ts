/**
 * Electron Preload API 类型声明（渲染进程侧）
 */
export type ElectronThemeMode = 'light' | 'dark';
export type FeatureWindowName = 'library' | 'world' | 'eval' | 'settings';

export interface CharacterStatusEvent {
  characterId: string;
  status: 'online' | 'in-chat' | 'offline';
}

export interface ChatMessageEvent {
  characterId: string;
  preview: string;
  time: number;
}

export interface AuroraAPI {
  openCharacterChat(characterId: string): void;
  closeWindow(): void;
  minimizeWindow(): void;
  openFeatureWindow(name: FeatureWindowName): void;
  quitApp(): void;

  getTheme(): Promise<ElectronThemeMode>;
  setTheme(mode: ElectronThemeMode): Promise<void>;

  getCharacterId(): Promise<string | null>;
  notifyMessageReceived(preview: string): void;

  onCharacterStatusChanged(cb: (data: CharacterStatusEvent) => void): () => void;
  onChatMessageReceived(cb: (data: ChatMessageEvent) => void): () => void;
  onChatWindowFocused(cb: (characterId: string) => void): () => void;
  onThemeChanged(cb: (mode: ElectronThemeMode) => void): () => void;
  onSettingsChanged(cb: () => void): () => void;
}

declare global {
  interface Window {
    aurora?: AuroraAPI;
  }
}
