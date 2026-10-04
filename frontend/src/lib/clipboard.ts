/**
 * Clipboard helpers.
 *
 * Packaged builds load the renderer via `file://`, which is NOT a secure
 * context — so `navigator.clipboard` is `undefined` there and a direct
 * `navigator.clipboard.writeText(...)` silently fails (e.g. the message
 * copy / edit / regenerate toolbar copies on Windows). Prefer the Electron
 * main-process clipboard and fall back to the web API (Vite dev = secure).
 */

function electronApi(): Window['electronAPI'] | undefined {
  return typeof window !== 'undefined' ? window.electronAPI : undefined;
}

/** Write `text` to the OS clipboard. Returns true on success. */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  const api = electronApi();
  if (api?.clipboardWriteText) {
    try {
      await api.clipboardWriteText(text);
      return true;
    } catch {
      // fall through to the web API
    }
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Read text from the OS clipboard (server-set text; "" when unavailable). */
export async function readClipboardText(): Promise<string> {
  const api = electronApi();
  if (api?.clipboardReadText) {
    try {
      return await api.clipboardReadText();
    } catch {
      return '';
    }
  }
  try {
    return await navigator.clipboard.readText();
  } catch {
    return '';
  }
}
