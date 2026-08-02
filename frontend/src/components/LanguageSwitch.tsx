import { getLanguage, setLanguage, t, type Language } from '../lib/i18n';

interface LanguageSwitchProps {
  onLanguageChange: () => void;
}

export function LanguageSwitch({ onLanguageChange }: LanguageSwitchProps) {
  const currentLanguage = getLanguage();

  async function selectLanguage(language: Language) {
    if (language === currentLanguage) return;
    await setLanguage(language);
    onLanguageChange();
  }

  return (
    <div className="language-switch" aria-label={t('language.switch_label')}>
      <button
        className={`language-switch__option ${currentLanguage === 'zh' ? 'language-switch__option--active' : ''}`}
        type="button"
        onClick={() => selectLanguage('zh')}
      >
        {t('language.zh')}
      </button>
      <button
        className={`language-switch__option ${currentLanguage === 'en' ? 'language-switch__option--active' : ''}`}
        type="button"
        onClick={() => selectLanguage('en')}
      >
        {t('language.en')}
      </button>
    </div>
  );
}
