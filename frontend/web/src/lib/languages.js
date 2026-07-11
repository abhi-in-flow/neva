// The fixed language list from the frontend work order (§2.3), each with its
// own-script rendering — dual-script chips are a day-one Indic rendering test.
export const LANGUAGES = [
  { code: 'as', en: 'Assamese', native: 'অসমীয়া' },
  { code: 'bn', en: 'Bengali', native: 'বাংলা' },
  { code: 'hi', en: 'Hindi', native: 'हिन्दी' },
  { code: 'en', en: 'English', native: 'English' },
  { code: 'kn', en: 'Kannada', native: 'ಕನ್ನಡ' },
  { code: 'ta', en: 'Tamil', native: 'தமிழ்' },
  { code: 'te', en: 'Telugu', native: 'తెలుగు' },
  { code: 'ml', en: 'Malayalam', native: 'മലയാളം' },
  { code: 'mr', en: 'Marathi', native: 'मराठी' },
  { code: 'or', en: 'Odia', native: 'ଓଡ଼ିଆ' },
  { code: 'ne', en: 'Nepali', native: 'नेपाली' },
  { code: 'bho', en: 'Bhojpuri', native: 'भोजपुरी' },
];

export const langByCode = (code) => LANGUAGES.find((l) => l.code === code);
