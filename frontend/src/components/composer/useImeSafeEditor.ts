import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

/**
 * IME-safe editing kernel for the chat composer.
 *
 * The composer is a React-managed `contentEditable`, which is inherently
 * hostile to third-party IMEs: the browser must keep the composition node and
 * its caret rect stable for the whole `compositionstart → compositionend`
 * window. Any DOM mutation, focus/selection write, or `preventDefault` while a
 * composition is active can abort it and hide the candidate window.
 *
 * This hook centralises *all* DOM / caret / composition work behind one
 * invariant:
 *
 *   While composing: zero DOM mutation, zero focus/selection writes, zero IME
 *   key interception; model sync is deferred to a single `compositionend`
 *   reconcile.
 *
 * It also recovers a dropped `compositionend` (a known Chromium/Electron
 * issue with third-party IMEs) by checking the native `isComposing` flag on
 * every `input` event.
 */

export interface ImeSafeEditor {
  editorRef: React.RefObject<HTMLDivElement | null>;
  /** True while an IME composition is active (mirrors the ref into state). */
  composing: boolean;
  isComposing: () => boolean;
  /** Read the composer text (skips the non-editable command chip). */
  readText: () => string;
  /** `onInput` handler: composition-aware reconcile. */
  handleInput: (event?: FormEvent<HTMLDivElement>) => void;
  handleCompositionStart: () => void;
  handleCompositionEnd: () => void;
  /** Reconcile an externally-driven `value` (edit mode / send reset). */
  syncExternalValue: (value: string) => void;
  /** Keep the React-rendered chip anchored at the head of the editor. */
  reanchorChip: () => void;
  /** Park the caret right after the chip (start of the prompt text). */
  caretAfterChip: () => void;
  /** Remove the leading "/token " currently being typed. */
  stripLeadingCommand: () => boolean;
  /** Remove every non-chip child (used when a chip-including selection is deleted/pasted over). */
  clearText: () => void;
  /** Insert text at the caret via Range (replaces deprecated execCommand). */
  insertText: (text: string) => void;
}

interface UseImeSafeEditorOptions {
  /** Called exactly once per non-composing reconcile, after normalization. */
  onReconcile: (text: string) => void;
}

/** Read the composer text, skipping the (non-editable) command chip. Block
 *  elements and <br> are normalised back to newlines. */
function readEditorText(editor: HTMLElement): string {
  const parts: string[] = [];
  const collect = (parent: Node, out: string[]) => {
    parent.childNodes.forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) {
        out.push(child.nodeValue ?? "");
        return;
      }
      if (child.nodeType !== Node.ELEMENT_NODE) return;
      const el = child as HTMLElement;
      if (el.dataset.commandChip !== undefined) return;
      if (el.tagName === "BR") {
        out.push("\n");
        return;
      }
      collect(child, out);
      if (/^(DIV|P|LI)$/.test(el.tagName)) out.push("\n");
    });
  };
  collect(editor, parts);
  return parts.join("");
}

/** Remove every child except the command chip. */
function removeNonChipChildren(editor: HTMLElement) {
  Array.from(editor.childNodes).forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.commandChip !== undefined) return;
    editor.removeChild(node);
  });
}

/** Set the composer text, keeping the chip (if any) at the head. Only used
 *  for external value changes (edit-mode hydrate / send reset) — user typing
 *  never goes through React, so the caret never jumps. */
function hydrateEditor(editor: HTMLElement, text: string) {
  removeNonChipChildren(editor);
  if (!text) return; // leave the editor truly empty so the placeholder shows
  const textNode = document.createTextNode(text);
  const chip = editor.querySelector("[data-command-chip]");
  if (chip) chip.after(textNode);
  else editor.appendChild(textNode);
}

/** Chrome leaves a <br>/empty <div> behind when a contentEditable is cleared.
 *  Normalise that back to a truly empty editor so the :empty placeholder
 *  shows and a fresh "/cmd" typed afterwards still auto-commits. */
function normalizeEmptyEditor(editor: HTMLElement) {
  let empty = true;
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    if ((walker.currentNode as Text).nodeValue?.trim()) {
      empty = false;
      break;
    }
  }
  if (!empty) return;
  removeNonChipChildren(editor);
}

/** Remove the leading "/token " text currently being typed. Returns whether
 *  any text was cut. */
function stripLeadingCommandToken(editor: HTMLElement): boolean {
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  const node = walker.nextNode();
  if (!node) return false;
  const text = node.nodeValue ?? "";
  const match = /^\/[A-Za-z0-9_.-]*\s?/.exec(text);
  if (!match) return false;
  const rest = text.slice(match[0].length);
  if (rest) node.nodeValue = rest;
  else node.parentNode?.removeChild(node);
  return true;
}

/** Place the caret right after the chip (start of the prompt text). */
function focusEditorAfterChip(editor: HTMLElement) {
  editor.focus();
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  const chip = editor.querySelector("[data-command-chip]");
  let firstText: Text | null = null;
  while (walker.nextNode()) {
    const candidate = walker.currentNode as Text;
    if (chip && chip.contains(candidate)) continue;
    firstText = candidate;
    break;
  }
  if (firstText) {
    range.setStart(firstText, 0);
    range.collapse(true);
  } else if (chip) {
    // chip is the only content — park the caret right after it so typing
    // continues the prompt instead of landing inside the chip or before it.
    range.setStartAfter(chip);
    range.collapse(true);
  } else if (editor.childNodes.length > 0) {
    const last = editor.lastChild as ChildNode;
    range.setStart(
      last,
      last.nodeType === Node.TEXT_NODE ? (last as Text).nodeValue?.length ?? 0 : last.childNodes.length,
    );
    range.collapse(true);
  } else {
    range.setStart(editor, 0);
    range.collapse(true);
  }
  selection.removeAllRanges();
  selection.addRange(range);
}

export function useImeSafeEditor({ onReconcile }: UseImeSafeEditorOptions): ImeSafeEditor {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const composingRef = useRef(false);
  const [composing, setComposing] = useState(false);
  const pendingValueRef = useRef<string | null>(null);
  const onReconcileRef = useRef(onReconcile);
  onReconcileRef.current = onReconcile;

  const isComposing = useCallback(() => composingRef.current, []);

  const readText = useCallback((): string => {
    const editor = editorRef.current;
    return editor ? readEditorText(editor) : "";
  }, []);

  const reconcileNow = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    normalizeEmptyEditor(editor);
    onReconcileRef.current(readEditorText(editor));
  }, []);

  const syncExternalValue = useCallback((value: string) => {
    // Never touch the DOM mid-composition; replay the newest value afterwards.
    if (composingRef.current) {
      pendingValueRef.current = value;
      return;
    }
    const editor = editorRef.current;
    if (!editor) return;
    if (readEditorText(editor) === value) return;
    hydrateEditor(editor, value);
  }, []);

  const handleInput = useCallback(
    (event?: FormEvent<HTMLDivElement>) => {
      const nativeIsComposing =
        (event?.nativeEvent as { isComposing?: boolean } | undefined)?.isComposing === true;
      if (nativeIsComposing) {
        // Composition active: record the state and do nothing else.
        if (!composingRef.current) {
          composingRef.current = true;
          setComposing(true);
        }
        return;
      }
      if (composingRef.current) {
        // The browser dropped compositionend — recover, then reconcile once.
        composingRef.current = false;
        setComposing(false);
      }
      reconcileNow();
    },
    [reconcileNow],
  );

  const handleCompositionStart = useCallback(() => {
    composingRef.current = true;
    setComposing(true);
  }, []);

  const handleCompositionEnd = useCallback(() => {
    composingRef.current = false;
    setComposing(false);
    reconcileNow();
    // Apply an external value that arrived while the composition was active.
    const pending = pendingValueRef.current;
    pendingValueRef.current = null;
    if (pending !== null) syncExternalValue(pending);
  }, [reconcileNow, syncExternalValue]);

  const reanchorChip = useCallback(() => {
    const editor = editorRef.current;
    if (!editor || composingRef.current) return;
    const chip = editor.querySelector("[data-command-chip]");
    if (chip && editor.firstChild !== chip) editor.prepend(chip);
  }, []);

  const caretAfterChip = useCallback(() => {
    const editor = editorRef.current;
    if (!editor || composingRef.current) return;
    focusEditorAfterChip(editor);
  }, []);

  const stripLeadingCommand = useCallback((): boolean => {
    const editor = editorRef.current;
    if (!editor || composingRef.current) return false;
    return stripLeadingCommandToken(editor);
  }, []);

  const clearText = useCallback(() => {
    const editor = editorRef.current;
    if (!editor || composingRef.current) return;
    removeNonChipChildren(editor);
  }, []);

  const insertText = useCallback(
    (text: string) => {
      const editor = editorRef.current;
      if (!editor || !text || composingRef.current) return;
      const selection = window.getSelection();
      if (!selection) return;
      const current = selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
      const range = document.createRange();
      if (current && editor.contains(current.startContainer)) {
        range.setStart(current.startContainer, current.startOffset);
        range.setEnd(current.endContainer, current.endOffset);
      } else {
        // Detached caret (e.g. after clearing a chip-containing selection):
        // fall back to the end of the editor.
        range.selectNodeContents(editor);
        range.collapse(false);
      }
      range.deleteContents();
      const node = document.createTextNode(text);
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      // Programmatic Range edits do not emit an `input` event — reconcile here.
      reconcileNow();
    },
    [reconcileNow],
  );

  // Safety net: if a composition is somehow active when this editor unmounts,
  // don't leave a stale pending value around.
  useEffect(() => {
    return () => {
      pendingValueRef.current = null;
    };
  }, []);

  return {
    editorRef,
    composing,
    isComposing,
    readText,
    handleInput,
    handleCompositionStart,
    handleCompositionEnd,
    syncExternalValue,
    reanchorChip,
    caretAfterChip,
    stripLeadingCommand,
    clearText,
    insertText,
  };
}
