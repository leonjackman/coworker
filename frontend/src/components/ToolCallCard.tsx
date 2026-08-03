import { Check, ChevronDown, ChevronRight, Loader2, Wrench, X } from 'lucide-react';
import { useState } from 'react';
import type { PartTool } from '../types';
import { t } from '../lib/i18n';

function toolLabel(name: string) {
  const labels: Record<string, string> = {
    read_file: 'Read',
    write_file: 'Write',
    replace_in_file: 'Edit',
    apply_text_edits: 'Edits',
    search_files: 'Search',
    run_command: 'Command',
    ask_user: 'Ask',
  };
  const key = `tool.${name}`;
  const i18n = t(key);
  return i18n !== key ? i18n : (labels[name] || name);
}

export function ToolCallCard({ tool }: { tool: PartTool }) {
  const [open, setOpen] = useState(false);
  const running = tool.status === 'running';
  const error = tool.status === 'error';
  const done = tool.status === 'success';

  let inputPreview = '';
  try {
    const args = JSON.parse(tool.input || '{}');
    if (typeof args === 'object' && args !== null) {
      const entries = Object.entries(args).slice(0, 2);
      inputPreview = entries.map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 40) : JSON.stringify(v).slice(0, 40)}`).join(', ');
    }
  } catch {
    inputPreview = (tool.input || '').slice(0, 60);
  }

  return (
    <div className={`tool-card tool-card--${tool.status}`}>
      <button
        type="button"
        className="tool-card__header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="tool-card__status-icon">
          {running ? (
            <Loader2 className="tool-card__spinner" size={14} />
          ) : error ? (
            <X className="tool-card__icon-error" size={14} />
          ) : (
            <Check className="tool-card__icon-success" size={14} />
          )}
        </span>
        <Wrench size={13} className="tool-card__wrench" />
        <span className="tool-card__name">{toolLabel(tool.name)}</span>
        {inputPreview && <span className="tool-card__preview">{inputPreview}</span>}
        {(tool.output || tool.input) && (
          <span className="tool-card__expand">
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
        )}
      </button>
      {open && (
        <div className="tool-card__details">
          {tool.input && (
            <div className="tool-card__section">
              <span className="tool-card__label">Input</span>
              <pre className="tool-card__code">{formatTryJson(tool.input)}</pre>
            </div>
          )}
          {done && tool.output && (
            <div className="tool-card__section">
              <span className="tool-card__label">Output</span>
              <pre className="tool-card__code">{tool.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatTryJson(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
  }
}
