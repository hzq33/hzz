import fs from 'node:fs';
import path from 'node:path';

import { app, type Rectangle } from 'electron';

import type { ThemeMode } from './types';

interface WindowBoundsMap {
  [key: string]: Rectangle;
}

interface PersistedSettings {
  theme: ThemeMode;
  bounds: WindowBoundsMap;
}

const DEFAULTS: PersistedSettings = {
  theme: 'dark',
  bounds: {},
};

function filePath(): string {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readAll(): PersistedSettings {
  try {
    const raw = fs.readFileSync(filePath(), 'utf8');
    const parsed = JSON.parse(raw) as Partial<PersistedSettings>;
    return {
      theme: parsed.theme === 'light' || parsed.theme === 'dark' ? parsed.theme : DEFAULTS.theme,
      bounds: parsed.bounds && typeof parsed.bounds === 'object' ? parsed.bounds : {},
    };
  } catch {
    return { ...DEFAULTS, bounds: {} };
  }
}

function writeAll(next: PersistedSettings): void {
  try {
    fs.mkdirSync(path.dirname(filePath()), { recursive: true });
    fs.writeFileSync(filePath(), JSON.stringify(next, null, 2), 'utf8');
  } catch (err) {
    console.warn('[settings-store] write failed', err);
  }
}

export function loadTheme(): ThemeMode {
  return readAll().theme;
}

export function saveTheme(theme: ThemeMode): void {
  const all = readAll();
  all.theme = theme;
  writeAll(all);
}

export function loadBounds(key: string): Rectangle | undefined {
  return readAll().bounds[key];
}

export function saveBounds(key: string, bounds: Rectangle): void {
  const all = readAll();
  all.bounds[key] = bounds;
  writeAll(all);
}
