// Shared helpers for the embedded browser's personal-data UI.

export function hostOf(url: string): string {
  if (!url) return '';
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export function originOf(url: string): string {
  if (!url) return '';
  try {
    return new URL(url).origin;
  } catch {
    return '';
  }
}

export function displayTitle(title: string, url: string): string {
  const trimmed = (title || '').trim();
  if (trimmed) return trimmed;
  return hostOf(url) || url;
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? Math.round(value) : value.toFixed(1)} ${units[exponent]}`;
}

export function formatTime(iso: string): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

export function dayLabel(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startOfDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startOfToday.getTime() - startOfDay.getTime()) / 86400000);
  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return 'earlier_week';
  return 'earlier';
}

export function permissionLabelKey(permission: string): string {
  const map: Record<string, string> = {
    media: 'browser.permission_media',
    geolocation: 'browser.permission_geolocation',
    notifications: 'browser.permission_notifications',
    'clipboard-read': 'browser.permission_clipboard',
    'clipboard-sanitized-write': 'browser.permission_clipboard',
    fullscreen: 'browser.permission_fullscreen',
    midi: 'browser.permission_midi',
    midiSysex: 'browser.permission_midi',
    pointerLock: 'browser.permission_pointer_lock',
    openExternal: 'browser.permission_open_external',
    'display-capture': 'browser.permission_display_capture',
    'window-management': 'browser.permission_window_management',
    serial: 'browser.permission_serial',
    hid: 'browser.permission_hid',
    usb: 'browser.permission_usb',
  };
  return map[permission] || 'browser.permission_generic';
}
