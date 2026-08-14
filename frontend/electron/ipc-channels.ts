/** Single source of IPC channel names for main + preload. */
export const IPC = {
  OPEN_CHARACTER_CHAT: 'aurora:open-character-chat',
  CLOSE_CHARACTER_CHAT: 'aurora:close-character-chat',
  OPEN_FEATURE_WINDOW: 'aurora:open-feature-window',
  MINIMIZE_WINDOW: 'aurora:minimize-window',
  CLOSE_WINDOW: 'aurora:close-window',
  QUIT_APP: 'aurora:quit-app',
  CHARACTER_STATUS_CHANGED: 'aurora:character-status-changed',
  CHAT_MESSAGE_RECEIVED: 'aurora:chat-message-received',
  CHAT_WINDOW_FOCUSED: 'aurora:chat-window-focused',
  THEME_CHANGED: 'aurora:theme-changed',
  SETTINGS_CHANGED: 'aurora:settings-changed',
  GET_THEME: 'aurora:get-theme',
  SET_THEME: 'aurora:set-theme',
  GET_CHARACTER_ID: 'aurora:get-character-id',
  NOTIFY_CHAT_MESSAGE: 'aurora:notify-chat-message',
} as const;

export type IpcChannel = (typeof IPC)[keyof typeof IPC];
