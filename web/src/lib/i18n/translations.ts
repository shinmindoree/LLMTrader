import en from "./en";
import ko from "./ko";

export type Locale = "en" | "ko";

export const LOCALES: { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ko", label: "한국어" },
];

export type TranslationKeys = typeof en;

export const translations: Record<Locale, TranslationKeys> = { en, ko };

export const DEFAULT_LOCALE: Locale = "en";
