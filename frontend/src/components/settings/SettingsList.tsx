import { ChevronRight } from 'lucide-react';
import { type ReactNode } from 'react';
import { cn } from '../../lib/utils';
import { t } from '../../lib/i18n';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';
import { ToggleGroup } from '../ui/toggle-group';

export interface SettingsToggleOption<T extends string> {
  value: T;
  label: ReactNode;
  title?: string;
}

export type SettingsItem =
  | {
      id: string;
      type: 'toggle';
      label: ReactNode;
      description?: ReactNode;
      value: string;
      options: SettingsToggleOption<string>[];
      onChange: (value: string) => void;
      disabled?: boolean;
    }
  | {
      id: string;
      type: 'action';
      label: ReactNode;
      description?: ReactNode;
      actionLabel: ReactNode;
      onAction: () => void;
      disabled?: boolean;
      meta?: ReactNode;
    }
  | {
      id: string;
      type: 'number_input';
      label: ReactNode;
      description?: ReactNode;
      value: number;
      min?: number;
      max?: number;
      unit?: ReactNode;
      onChange: (value: number) => void;
    }
  | {
      id: string;
      type: 'text_input';
      label: ReactNode;
      description?: ReactNode;
      value: string;
      placeholder?: string;
      onChange: (value: string) => void;
    }
  | {
      id: string;
      type: 'select';
      label: ReactNode;
      description?: ReactNode;
      value: string;
      options: { value: string; label: ReactNode }[];
      onChange: (value: string) => void;
    }
  | {
      id: string;
      type: 'switch';
      label: ReactNode;
      description?: ReactNode;
      checked: boolean;
      disabled?: boolean;
      onChange: (checked: boolean) => void;
    }
  | {
      id: string;
      type: 'info';
      label: ReactNode;
      description?: ReactNode;
      meta?: ReactNode;
    };

export interface SettingsGroup {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  items: SettingsItem[];
  footer?: ReactNode;
}

interface SettingsListProps {
  groups: SettingsGroup[];
  className?: string;
}

function SettingControl({ item }: { item: SettingsItem }) {
  if (item.type === 'action') {
    return (
      <Button variant="secondary" onClick={item.onAction} disabled={item.disabled}>
        {item.actionLabel}
        <ChevronRight size={14} />
      </Button>
    );
  }

  if (item.type === 'switch') {
    return (
      <Switch id={`setting-${item.id}`} checked={item.checked} disabled={item.disabled} onChange={(e) => item.onChange(e.target.checked)} />
    );
  }

  if (item.type === 'info') {
    return null;
  }

  if (item.type === 'number_input') {
    return (
      <NumberInputControl
        value={item.value}
        min={item.min ?? 1}
        max={item.max ?? 1024}
        unit={item.unit}
        onChange={item.onChange}
      />
    );
  }

  if (item.type === 'text_input') {
    return <TextInputControl value={item.value} placeholder={item.placeholder} onChange={item.onChange} />;
  }

  if (item.type === 'select') {
    return <SelectControl value={item.value} options={item.options} onChange={item.onChange} />;
  }

  return (
    <ToggleGroup
      value={item.value}
      onValueChange={item.onChange}
      items={item.options}
      className="settings-toggle"
    />
  );
}

function NumberInputControl({
  value,
  min = 1,
  max = 1024,
  unit,
  onChange,
}: {
  value: number;
  min?: number;
  max?: number;
  unit?: ReactNode;
  onChange: (value: number) => void;
}) {
  const clamp = (raw: number) => {
    if (!Number.isFinite(raw)) return min;
    return Math.max(min, Math.min(max, Math.round(raw)));
  };

  return (
    <div className="settings-number-input">
      <input
        type="number"
        className="settings-number-input__field"
        value={Number.isFinite(value) ? value : min}
        min={min}
        max={max}
        onChange={(e) => onChange(clamp(parseInt(e.target.value, 10)))}
        onBlur={(e) => onChange(clamp(parseInt(e.target.value, 10)))}
        aria-label={typeof unit === 'string' ? unit : 'value'}
      />
      {unit && <span className="settings-number-input__unit">{unit}</span>}
    </div>
  );
}

function TextInputControl({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <input
      type="text"
      className="settings-number-input__field"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      aria-label={placeholder ?? 'value'}
    />
  );
}

function SelectControl({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: ReactNode }[];
  onChange: (value: string) => void;
}) {
  const hasValue = options.some((option) => option.value === value);
  return (
    <select
      className="settings-select"
      value={hasValue ? value : ''}
      onChange={(e) => onChange(e.target.value)}
      aria-label="select"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function SettingsList({ groups, className }: SettingsListProps) {
  return (
    <div className={cn('settings-list', className)}>
      {groups.map((group) => (
        <section className="settings-group" key={group.id} aria-labelledby={`settings-group-${group.id}`}>
          <div className="settings-group__heading">
            <h2 id={`settings-group-${group.id}`}>{group.title}</h2>
            {group.description && <p>{group.description}</p>}
          </div>
          <div className="settings-card">
            {group.items.map((item) => (
              <div className="settings-row" key={item.id} id={`setting-row-${item.id}`}>
                <div className="settings-row__copy">
                  <label htmlFor={`setting-${item.id}`}>{item.label}</label>
                  {item.description && <p>{item.description}</p>}
                </div>
                {'meta' in item && item.meta && <div className="settings-row__meta">{item.meta}</div>}
                <div className="settings-row__control">
                  <SettingControl item={item} />
                </div>
              </div>
            ))}
            {group.footer}
          </div>
        </section>
      ))}
    </div>
  );
}
