const STORAGE_KEY = 'coworker-language';

export type Language = 'zh' | 'en';

let currentLanguage: Language = 'zh';
let dictionary: Record<string, string> = {};

export function getLanguage(): Language {
  return currentLanguage;
}

export async function setLanguage(language: Language): Promise<void> {
  currentLanguage = language;
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // localStorage may be unavailable in restricted renderer contexts.
  }
  await loadDictionary();
}

export async function initLanguage(): Promise<Language> {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'zh' || stored === 'en') {
      currentLanguage = stored;
    }
  } catch {
    // Keep the default language when storage is unavailable.
  }
  await loadDictionary();
  return currentLanguage;
}

async function loadDictionary(): Promise<void> {
  try {
    const module = await import(`../locales/${currentLanguage}.json`);
    dictionary = module.default || module;
  } catch {
    dictionary = {};
  }
}

export function t(key: string, params?: Record<string, string | number>): string {
  let message = dictionary[key] ?? key;
  if (!params) return message;

  for (const [name, value] of Object.entries(params)) {
    message = message.replace(`{${name}}`, String(value));
  }
  return message;
}

export function translateError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || '');
  if (message.includes('Failed to fetch')) return t('error.fetch_failed');
  if (message.includes('Failed to connect to backend')) return t('error.connection_refused');
  if (message.includes('Electron API is unavailable')) return t('error.electron_api_unavailable');
  return message || t('common.unknown_error');
}
