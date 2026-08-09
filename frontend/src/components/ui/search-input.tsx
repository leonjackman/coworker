import { Search } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  size?: 'sm' | 'default';
}

/**
 * Generic search input with a leading icon.
 * Reusable anywhere a filtered list needs a quick search.
 */
export function SearchInput({ value, onChange, placeholder, className, size = 'default' }: SearchInputProps) {
  return (
    <div className={cn('cw-search', size === 'sm' && 'cw-search--sm', className)}>
      <Search size={14} className="cw-search__icon" aria-hidden />
      <input
        className="cw-search__input"
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
