import { t } from '../lib/i18n';

const TOOL_LABELS: Record<string, string> = {
  read_file: 'Read',
  write_file: 'Write',
  replace_in_file: 'Edit',
  apply_text_edits: 'Edits',
  search_files: 'Search',
  run_command: 'Command',
  ask_user: 'Ask',
  web_search: 'Web',
  web_fetch: 'Fetch',
};

export function toolLabel(name: string): string {
  const key = `tool.${name}`;
  const i18n = t(key);
  if (i18n !== key) return i18n;
  return TOOL_LABELS[name] || name;
}

function tryParse(input: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(input || '{}');
    return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

const PATH_KEYS = ['file_path', 'path', 'target', 'file_paths'];

export function toolPreview(name: string, input: string): string {
  const args = tryParse(input);
  if (!args) return (input || '').slice(0, 80);
  const stringify = (value: unknown): string =>
    typeof value === 'string' ? value : JSON.stringify(value);

  if (name === 'search_files') {
    return stringify(args.pattern ?? args.query ?? '');
  }
  if (name === 'run_command') {
    const command = args.command;
    return Array.isArray(command) ? command.join(' ') : stringify(command ?? '');
  }
  if (name === 'ask_user') {
    return stringify(args.question ?? '');
  }
  if (name === 'web_search') {
    return stringify(args.query ?? '');
  }
  if (name === 'web_fetch') {
    return stringify(args.url ?? '');
  }
  if (name === 'read_file' || name === 'write_file' || name === 'replace_in_file' || name === 'apply_text_edits') {
    for (const key of PATH_KEYS) {
      if (args[key]) return stringify(args[key]);
    }
  }
  const entries = Object.entries(args).slice(0, 2);
  return entries.map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 40) : stringify(v).slice(0, 40)}`).join(', ');
}
