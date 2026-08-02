import { t } from '../lib/i18n';

interface ChatInputProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSend: () => void;
}

export function ChatInput({ value, disabled, onChange, onSend }: ChatInputProps) {
  return (
    <footer className="composer">
      <textarea
        className="composer__input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            onSend();
          }
        }}
        placeholder={t('chat.placeholder')}
        disabled={disabled}
      />
      <button className="composer__send" onClick={onSend} disabled={disabled || !value.trim()}>
        {t('common.send')}
      </button>
    </footer>
  );
}
