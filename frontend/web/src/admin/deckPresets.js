/**
 * Operator presets for the primary admin prompt-to-deck flow.
 *
 * Lists all 28 Indian states (display label + lowercase hyphenated region_tag)
 * plus curated one-line example themes. The backend owns generation rules;
 * this module is presentation data only for /admin Decks.
 */

/** @typedef {{ label: string, region_tag: string }} IndianStatePreset */

/** All 28 Indian states for the region dropdown. */
export const INDIAN_STATES = Object.freeze([
  { label: 'Andhra Pradesh', region_tag: 'andhra-pradesh' },
  { label: 'Arunachal Pradesh', region_tag: 'arunachal-pradesh' },
  { label: 'Assam', region_tag: 'assam' },
  { label: 'Bihar', region_tag: 'bihar' },
  { label: 'Chhattisgarh', region_tag: 'chhattisgarh' },
  { label: 'Goa', region_tag: 'goa' },
  { label: 'Gujarat', region_tag: 'gujarat' },
  { label: 'Haryana', region_tag: 'haryana' },
  { label: 'Himachal Pradesh', region_tag: 'himachal-pradesh' },
  { label: 'Jharkhand', region_tag: 'jharkhand' },
  { label: 'Karnataka', region_tag: 'karnataka' },
  { label: 'Kerala', region_tag: 'kerala' },
  { label: 'Madhya Pradesh', region_tag: 'madhya-pradesh' },
  { label: 'Maharashtra', region_tag: 'maharashtra' },
  { label: 'Manipur', region_tag: 'manipur' },
  { label: 'Meghalaya', region_tag: 'meghalaya' },
  { label: 'Mizoram', region_tag: 'mizoram' },
  { label: 'Nagaland', region_tag: 'nagaland' },
  { label: 'Odisha', region_tag: 'odisha' },
  { label: 'Punjab', region_tag: 'punjab' },
  { label: 'Rajasthan', region_tag: 'rajasthan' },
  { label: 'Sikkim', region_tag: 'sikkim' },
  { label: 'Tamil Nadu', region_tag: 'tamil-nadu' },
  { label: 'Telangana', region_tag: 'telangana' },
  { label: 'Tripura', region_tag: 'tripura' },
  { label: 'Uttar Pradesh', region_tag: 'uttar-pradesh' },
  { label: 'Uttarakhand', region_tag: 'uttarakhand' },
  { label: 'West Bengal', region_tag: 'west-bengal' },
]);

/** Default state selected when the Decks panel mounts. */
export const DEFAULT_REGION_TAG = 'assam';

/** Default card count for prompt generation (matches contract default). */
export const DEFAULT_CARD_COUNT = 8;

/**
 * Varied one-line example themes operators can apply with one click.
 * @type {ReadonlyArray<string>}
 */
export const EXAMPLE_PROMPTS = Object.freeze([
  'Monsoon market chaos with animals sharing umbrellas',
  'Festival night prep gone playfully wrong in a courtyard',
  'Rural transport mishaps on dusty village lanes',
  'Coastal life with boats, nets, and silly sea creatures',
  'Mountain village morning routines with unexpected guests',
  'Busy urban street food stalls and curious wildlife',
]);
