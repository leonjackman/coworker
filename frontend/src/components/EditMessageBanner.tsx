import { Check, Pencil, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { Textarea } from './ui/textarea';
import { t } from '../lib/i18n';

interface EditMessageBannerProps {
  initialContent: string;
  onSave: (content: string) => void;
  onCancel: () => void;
}

export function EditMessageBanner({ initialContent, onSave, onCancel }: EditMessageBannerProps) {
  const [value, setValue] = useState(initialContent);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  return (
    <div className="edit-banner">
      <div className="edit-banner__header">
        <Pencil size={13} />
        <span>{t('message.edit')}</span>
      </div>
      <Textarea
        ref={textareaRef}
        className="edit-banner__textarea"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={t('message.edit_placeholder')}
        rows={3}
      />
      <div className="edit-banner__actions">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          <X size={14} />
          {t('message.edit_cancel')}
        </Button>
        <Button variant="primary" size="sm" onClick={() => onSave(value)}>
          <Check size={14} />
          {t('message.edit_save')}
        </Button>
      </div>
    </div>
  );
}
