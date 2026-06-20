import { useAppStore } from '../stores/appStore';
import type { Lang } from './translations';
import { getTranslation } from './translations';

/**
 * Lightweight i18n hook.
 * Uses the `language` field from app config.
 * `t(key)` returns the translated string for the current language.
 * `lang` is the current language code.
 */
export function useTranslation() {
  const language: Lang = (useAppStore((s) => s.config.language) as Lang) || 'zh';

  const t = (key: string, vars?: Record<string, string>) => {
    let result = getTranslation(key, language);
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        result = result.replace(`{${k}}`, v);
      }
    }
    return result;
  };

  return { t, language };
}
