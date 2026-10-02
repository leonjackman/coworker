import { useEffect, type RefObject } from 'react';

/**
 * Normalize an arbitrary CSS color (rgb()/oklch()/color(srgb ...)/named) into the
 * `#rrggbb` / `rgba()` form the Electron Window Controls Overlay accepts.
 */
function normalizeCssColor(value: string): string | null {
  if (!value) return null;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d');
    if (!ctx) return value;
    ctx.fillStyle = '#000';
    ctx.fillStyle = value;
    return ctx.fillStyle;
  } catch {
    return value;
  }
}

/**
 * Windows-only: keep the native Window Controls Overlay (min/max/close) in sync
 * with the app's custom title bar. The overlay sits on top of the renderer at
 * the top-right, so its color must match the title bar background or a seam
 * appears. We mirror the bar's computed background/foreground and re-read them
 * whenever the theme changes (applyTheme rewrites root CSS variables).
 *
 * No-op on macOS/Linux and outside Electron.
 *
 * @param ref    the custom title bar element whose colors are mirrored
 * @param height overlay height in CSS px (must match the bar height)
 */
export function useTitlebarOverlay(
  ref: RefObject<HTMLElement | null>,
  height: number,
): void {
  useEffect(() => {
    const api = typeof window !== 'undefined' ? window.electronAPI : undefined;
    if (!api || api.platform !== 'win32' || typeof api.setTitlebarOverlay !== 'function') {
      return;
    }

    let frame = 0;
    const push = () => {
      const el = ref.current;
      if (!el) return;
      const style = getComputedStyle(el);
      const color = normalizeCssColor(style.backgroundColor);
      if (!color) return;
      const symbolColor = normalizeCssColor(style.color);
      const payload: { color: string; symbolColor?: string; height: number } = { color, height };
      if (symbolColor) payload.symbolColor = symbolColor;
      api.setTitlebarOverlay(payload);
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(push);
    };

    schedule();

    const observer = new MutationObserver(schedule);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'data-theme-preset', 'style', 'class'],
    });

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [ref, height]);
}
