// Guest preload for the embedded browser's login forms.
//
// Runs in an isolated world inside each <webview> guest (attached by main via
// the `will-attach-webview` hook). It exposes NOTHING to the page: it only
// reads form fields and talks to the main process over a strict IPC channel.
// Passwords captured here go straight to the main-process vault; the page
// world never receives stored credentials through this script.

'use strict';

const { ipcRenderer } = require('electron');

let enabled = false;
let autofillEnabled = true;
let lastFilledOrigin = '';

function isVisible(el) {
  if (!el) return false;
  const type = (el.type || '').toLowerCase();
  if (type === 'hidden') return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function passwordFields(root) {
  return Array.from((root || document).querySelectorAll('input[type="password"]')).filter(isVisible);
}

function usernameFieldFor(form, password) {
  const scope = form || document;
  const inputs = Array.from(scope.querySelectorAll('input')).filter(isVisible);
  const passwordIndex = password ? inputs.indexOf(password) : inputs.length;
  let fallback = null;
  for (let i = 0; i < inputs.length; i += 1) {
    const el = inputs[i];
    const type = (el.type || 'text').toLowerCase();
    if (type !== 'email' && type !== 'text' && type !== 'tel') continue;
    if (i >= passwordIndex) break;
    const autocomplete = (el.autocomplete || '').toLowerCase();
    const name = (el.name || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    if (autocomplete.includes('username') || autocomplete.includes('email') || /user|email|login|account|phone/.test(`${name} ${id}`)) {
      return el;
    }
    if (!fallback) fallback = el;
  }
  return fallback || inputs.find((el) => {
    const type = (el.type || 'text').toLowerCase();
    return (type === 'email' || type === 'text' || type === 'tel') && (el.autocomplete || '').toLowerCase().includes('username');
  }) || null;
}

function setValue(el, value) {
  if (!el || value == null) return;
  const proto = Object.getPrototypeOf(el);
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
  if (descriptor && descriptor.set) {
    descriptor.set.call(el, value);
  } else {
    el.value = value;
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function fillForm(form, password, cred) {
  const user = usernameFieldFor(form, password);
  if (user && cred.username) setValue(user, cred.username);
  if (password && cred.password) setValue(password, cred.password);
}

async function tryAutofill() {
  if (!enabled || !autofillEnabled) return;
  const origin = location.origin;
  if (!origin || origin === 'null' || origin === lastFilledOrigin) return;
  const password = passwordFields()[0];
  if (!password) return;
  lastFilledOrigin = origin;
  try {
    const cred = await ipcRenderer.invoke('browser-guest:autofill', { origin });
    if (cred && (cred.username || cred.password)) {
      fillForm(password.closest('form'), password, cred);
    }
  } catch { /* main unavailable — ignore */ }
}

function capture() {
  if (!enabled) return;
  const password = passwordFields()[0];
  if (!password || !password.value) return;
  const form = password.closest('form');
  const user = usernameFieldFor(form, password);
  const payload = {
    origin: location.origin,
    username: user ? user.value : '',
    password: password.value,
  };
  if (!payload.password) return;
  try {
    ipcRenderer.send('browser-guest:credential-captured', payload);
  } catch { /* ignore */ }
}

function submitForm(form) {
  if (!form) return;
  try {
    if (typeof form.requestSubmit === 'function') {
      form.requestSubmit();
      return;
    }
    const button = form.querySelector('button[type="submit"], input[type="submit"], button');
    if (button) button.click();
    else form.submit();
  } catch { /* ignore */ }
}

function installListeners() {
  // Explicit fill requested by the main process (agent "log in" action). The
  // credentials arrive over IPC into this isolated world, never as JS source.
  ipcRenderer.on('browser-guest:fill', (_event, cred) => {
    if (!cred || !cred.password) return;
    const password = passwordFields()[0];
    if (!password) return;
    const form = password.closest('form');
    fillForm(form, password, cred);
    if (cred.submit !== false) setTimeout(() => submitForm(form), 100);
  });
  document.addEventListener('submit', capture, true);
  document.addEventListener(
    'keydown',
    (event) => {
      if (event.key !== 'Enter') return;
      const password = passwordFields()[0];
      if (password && event.target === password) {
        // Give the page a tick to run its own handler, then capture.
        setTimeout(capture, 50);
      }
    },
    true,
  );
  document.addEventListener(
    'click',
    (event) => {
      const target = event.target;
      if (!target || !(target instanceof Element)) return;
      const button = target.closest('button, input[type="submit"], [role="button"]');
      if (button && passwordFields().length) setTimeout(capture, 50);
    },
    true,
  );
  window.addEventListener('load', () => tryAutofill());
  document.addEventListener('DOMContentLoaded', () => tryAutofill());
}

async function boot() {
  let features = null;
  try {
    features = await ipcRenderer.invoke('browser-guest:features');
  } catch {
    return;
  }
  if (!features || !features.password_manager) return;
  enabled = true;
  autofillEnabled = features.autofill !== false;
  installListeners();
  tryAutofill();
}

boot();
