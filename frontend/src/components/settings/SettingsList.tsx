import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';
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

  return (
    <ToggleGroup
      value={item.value}
      onValueChange={item.onChange}
      items={item.options}
      className="settings-toggle"
    />
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
