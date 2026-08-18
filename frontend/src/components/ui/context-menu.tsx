import { ClipboardPaste, Copy, Eraser, Scissors, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { t } from "../../lib/i18n";

/** 预置操作槽位：组件内部实现复制/粘贴/剪切等，免去使用者重复造轮子 */
export type ContextMenuSlotId = "copy" | "cut" | "paste" | "selectAll" | "delete" | "clear";

export interface ContextMenuItem {
  id: string;
  label: string;
  icon?: ReactNode;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  /** 在本项之前渲染一条分隔线 */
  dividerBefore?: boolean;
  onSelect: () => void;
}

interface ContextMenuProps {
  /** 菜单是否打开 */
  open: boolean;
  /** 鼠标右键坐标（clientX） */
  x: number;
  /** 鼠标右键坐标（clientY） */
  y: number;
  onClose: () => void;
  /** 预置剪贴板操作槽位，按传入顺序渲染 */
  clipboardSlots?: ContextMenuSlotId[];
  /** 自定义操作项，追加在剪贴板槽位之后 */
  items?: ContextMenuItem[];
  /** 剪贴板槽位作用的输入元素（textarea/input） */
  targetRef?: RefObject<HTMLTextAreaElement | HTMLInputElement | null>;
  /** 粘贴槽位完成后回调（可用于提取 session 引用等） */
  onSlotPaste?: (text: string) => void;
}

const SLOT_META: Record<ContextMenuSlotId, { icon: ReactNode; getShortcut: () => string }> = {
  copy: { icon: <Copy size={13} />, getShortcut: () => modKey() + "C" },
  cut: { icon: <Scissors size={13} />, getShortcut: () => modKey() + "X" },
  paste: { icon: <ClipboardPaste size={13} />, getShortcut: () => modKey() + "V" },
  selectAll: { icon: <Square size={13} />, getShortcut: () => modKey() + "A" },
  delete: { icon: <Trash2 size={13} />, getShortcut: () => (isMac() ? "⌫" : "Del") },
  clear: { icon: <Eraser size={13} />, getShortcut: () => "" },
};

const SLOT_LABEL: Record<ContextMenuSlotId, string> = {
  copy: "context_menu.copy",
  cut: "context_menu.cut",
  paste: "context_menu.paste",
  selectAll: "context_menu.select_all",
  delete: "context_menu.delete",
  clear: "context_menu.clear",
};

function isMac(): boolean {
  if (typeof navigator === "undefined") return false;
  return navigator.platform.toLowerCase().includes("mac");
}

function modKey(): string {
  return isMac() ? "⌘" : "Ctrl+";
}

/** 通过受控组件原生 setter + input 事件，让 React 受控 textarea/input 感知外部改动 */
function setElementValue(el: HTMLTextAreaElement | HTMLInputElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function readClipboardText(): Promise<string> {
  if (typeof window !== "undefined" && window.electronAPI?.clipboardReadText) {
    return window.electronAPI.clipboardReadText();
  }
  try {
    return await navigator.clipboard.readText();
  } catch {
    return "";
  }
}

async function writeClipboardText(text: string): Promise<void> {
  if (typeof window !== "undefined" && window.electronAPI?.clipboardWriteText) {
    await window.electronAPI.clipboardWriteText(text);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // ignore
  }
}

function getSelectionRange(el: HTMLTextAreaElement | HTMLInputElement) {
  return { start: el.selectionStart ?? 0, end: el.selectionEnd ?? 0 };
}

export function ContextMenu({
  open,
  x,
  y,
  onClose,
  clipboardSlots = [],
  items = [],
  targetRef,
  onSlotPaste,
}: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });

  // 打开且坐标变化时，把菜单收进视口（先按原始坐标渲染以量出尺寸，再修正）
  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    const pad = 8;
    setPos({
      x: Math.min(Math.max(pad, x), window.innerWidth - rect.width - pad),
      y: Math.min(Math.max(pad, y), window.innerHeight - rect.height - pad),
    });
  }, [open, x, y]);

  // 外部点击 / Escape / 滚动 / 缩放时关闭
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    const onDocKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onWinEvent = () => onClose();
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onDocKey);
    window.addEventListener("resize", onWinEvent);
    window.addEventListener("scroll", onWinEvent, true);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onDocKey);
      window.removeEventListener("resize", onWinEvent);
      window.removeEventListener("scroll", onWinEvent, true);
    };
  }, [open, onClose]);

  const run = useCallback(
    (fn: () => void) => {
      onClose();
      fn();
    },
    [onClose],
  );

  /** 预置剪贴板操作：基于 target 元素的选区/值执行，结束后自动关闭菜单 */
  const buildSlotItem = useCallback(
    (slot: ContextMenuSlotId): ContextMenuItem => {
      const target = targetRef?.current ?? null;
      const disabled = !target;
      const meta = SLOT_META[slot];

      const handle = () => {
        const el = targetRef?.current;
        if (!el) return;
        const range = getSelectionRange(el);
        const selected = el.value.slice(range.start, range.end);

        switch (slot) {
          case "copy":
            if (selected) void writeClipboardText(selected);
            break;
          case "cut":
            if (selected) {
              void writeClipboardText(selected);
              setElementValue(el, el.value.slice(0, range.start) + el.value.slice(range.end));
              el.setSelectionRange(range.start, range.start);
            }
            break;
          case "paste":
            void (async () => {
              const text = await readClipboardText();
              if (!text) return;
              const next = el.value.slice(0, range.start) + text + el.value.slice(range.end);
              setElementValue(el, next);
              el.setSelectionRange(range.start + text.length, range.start + text.length);
              onSlotPaste?.(text);
            })();
            break;
          case "selectAll":
            el.focus();
            el.setSelectionRange(0, el.value.length);
            break;
          case "delete":
            if (range.start !== range.end) {
              setElementValue(el, el.value.slice(0, range.start) + el.value.slice(range.end));
              el.setSelectionRange(range.start, range.start);
            }
            break;
          case "clear":
            setElementValue(el, "");
            break;
        }
      };

      const slotDisabled =
        disabled ||
        (slot === "copy" && !(target && target.value.slice(target.selectionStart ?? 0, target.selectionEnd ?? 0))) ||
        (slot === "cut" && !(target && target.value.slice(target.selectionStart ?? 0, target.selectionEnd ?? 0))) ||
        (slot === "delete" && !(target && (target.selectionStart ?? 0) !== (target.selectionEnd ?? 0)));

      return {
        id: slot,
        label: t(SLOT_LABEL[slot]),
        icon: meta.icon,
        shortcut: meta.getShortcut(),
        disabled: slotDisabled,
        onSelect: () => run(handle),
      };
    },
    [targetRef, run, onSlotPaste],
  );

  const slotItems = clipboardSlots.map(buildSlotItem);
  const allItems = [...slotItems, ...items];
  const anyDivider = allItems.some((item) => item.dividerBefore);

  if (!open) return null;

  return createPortal(
    <div
      ref={menuRef}
      className="context-menu"
      style={{ left: pos.x, top: pos.y }}
      role="menu"
      onContextMenu={(e) => e.preventDefault()}
    >
      {allItems.map((item) => (
        <div key={item.id}>
          {item.dividerBefore && anyDivider && <div className="context-menu__divider" />}
          <button
            type="button"
            role="menuitem"
            className={`context-menu__item${item.danger ? " context-menu__item--danger" : ""}`}
            disabled={item.disabled}
            onClick={() => run(item.onSelect)}
          >
            {item.icon && <span className="context-menu__icon">{item.icon}</span>}
            <span className="context-menu__label">{item.label}</span>
            {item.shortcut && <span className="context-menu__shortcut">{item.shortcut}</span>}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}
