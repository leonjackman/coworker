export function hasOpenOverlay(): boolean {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"], [role="menu"], [role="listbox"]'));
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target === document.body) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable;
}
