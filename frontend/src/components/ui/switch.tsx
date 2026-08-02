import { forwardRef } from 'react';
import { cn } from '../../lib/utils';

export interface SwitchProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string;
}

export const Switch = forwardRef<HTMLInputElement, SwitchProps>(
  ({ className, label, id, checked, disabled, onChange, ...props }, ref) => (
    <div className="switch-control">
      {label && (
        <label htmlFor={id ? `${id}-input` : undefined} className="switch-control__label">
          {label}
        </label>
      )}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          const input = document.getElementById(id ? `${id}-input` : '') as HTMLInputElement | null;
          input?.click();
        }}
        className={cn('glass-switch', checked && 'glass-switch--checked', className)}
      >
        <span />
      </button>
      <input
        ref={ref}
        id={id ? `${id}-input` : undefined}
        type="checkbox"
        className="switch-control__input"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        {...props}
      />
    </div>
  ),
);

Switch.displayName = 'Switch';
