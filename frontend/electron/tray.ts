import { Menu, Tray, app, nativeImage } from 'electron';

import type { WindowManager } from './window-manager';

function trayImage() {
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const i = (y * size + x) * 4;
      buf[i] = 99;
      buf[i + 1] = 102;
      buf[i + 2] = 241;
      buf[i + 3] = 255;
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

export function createTray(wm: WindowManager, onQuit: () => void): Tray | null {
  try {
    const tray = new Tray(trayImage());
    tray.setToolTip('Aurora Agent');
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: '打开面板', click: () => wm.showPanel() },
        { label: '设置', click: () => wm.openFeatureWindow('settings') },
        { type: 'separator' },
        { label: '退出', click: onQuit },
      ]),
    );
    tray.on('click', () => wm.showPanel());
    app.on('before-quit', () => {
      try {
        tray.destroy();
      } catch {
        /* already destroyed */
      }
    });
    return tray;
  } catch (err) {
    console.warn('[tray] failed to create', err);
    return null;
  }
}
