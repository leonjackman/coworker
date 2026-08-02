declare module 'shiki/langs/*' {
  import type { LanguageRegistration } from 'shiki/types';

  const registration: LanguageRegistration[];
  export default registration;
}

declare module 'shiki/themes/*' {
  import type { ThemeRegistration } from 'shiki/types';

  const theme: ThemeRegistration;
  export default theme;
}
