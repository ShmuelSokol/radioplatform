/**
 * A library title as listeners should read it: file-name underscores become spaces
 * ("Moed_Katan 4" -> "Moed Katan 4"). Display only: never use it on data that is
 * stored or sent back to the API.
 */
export function displayTitle(text: string): string;
export function displayTitle(text: string | null | undefined): string | null;
export function displayTitle(text: string | null | undefined): string | null {
  if (text == null) return null;
  return text.replace(/_+/g, ' ').replace(/ {2,}/g, ' ').trim();
}
