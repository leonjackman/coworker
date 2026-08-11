import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { cn } from '../../lib/utils';
import { t } from '../../lib/i18n';
import { Button } from '../ui/button';
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
      type: 'goal_rounds';
      label: ReactNode;
      description?: ReactNode;
      value: number;
      onChange: (value: number) => void;
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
    };

export interface SettingsGroup {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  items: SettingsItem[];
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

  if (item.type === 'goal_rounds') {
    return <GoalRoundsControl value={item.value} onChange={item.onChange} />;
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

function GoalRoundsControl({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  // Internal mode/input state: mode is only initialized from `value` on mount,
  // so switching INTO custom keeps its own number instead of snapping to the
  // default (50) that the derived value would otherwise imply.
  const [mode, setMode] = useState<'unlimited' | 'default' | 'custom'>(
    value === 0 ? 'unlimited' : value === 50 ? 'default' : 'custom',
  );
  const [customInput, setCustomInput] = useState<number>(value === 0 || value === 50 ? 50 : value);

  const selectMode = (next: 'unlimited' | 'default' | 'custom') => {
    setMode(next);
    if (next === 'unlimited') onChange(0);
    else if (next === 'default') onChange(50);
    else onChange(customInput);
  };

  const handleCustom = (raw: string) => {
    const parsed = parseInt(raw, 10);
    const clamped = Number.isFinite(parsed) ? Math.max(1, Math.min(1000, parsed)) : 1;
    setCustomInput(clamped);
    setMode('custom');
    onChange(clamped);
  };

  return (
    <div className="settings-goal-rounds">
      <ToggleGroup
        value={mode}
        onValueChange={(next) => selectMode(next as 'unlimited' | 'default' | 'custom')}
        items={[
          { value: 'unlimited', label: t('settings.goal_rounds_unlimited') },
          { value: 'default', label: t('settings.goal_rounds_default') },
          { value: 'custom', label: t('settings.goal_rounds_custom') },
        ]}
      />
      {mode === 'custom' && (
        <div className="settings-goal-rounds__field">
          <input
            type="number"
            min={1}
            max={1000}
            className="settings-goal-rounds__input"
            value={customInput}
            onChange={(e) => handleCustom(e.target.value)}
            aria-label={t('settings.goal_rounds_custom')}
          />
          <span className="settings-goal-rounds__unit">{t('settings.goal_rounds_unit')}</span>
        </div>
      )}
    </div>
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
          </div>
        </section>
      ))}
    </div>
  );
}
