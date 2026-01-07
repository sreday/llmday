// src/lib/utils.ts

/**
 * Generate URL-friendly slug from text
 */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80);
}

/**
 * Truncate text to specified length with ellipsis
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trim() + '...';
}

/**
 * Extract first paragraph from markdown text
 */
export function getFirstParagraph(text: string): string {
  const paragraphs = text.split(/\n\n+/);
  // Skip headers that start with #
  for (const p of paragraphs) {
    const trimmed = p.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      return trimmed;
    }
  }
  return '';
}

/**
 * Format duration in minutes to human readable string
 */
export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (mins === 0) return `${hours}h`;
  return `${hours}h ${mins}min`;
}

/**
 * Get speaker photo URL
 */
export function getSpeakerPhotoUrl(photo: string): string {
  return `/speakers/${photo}`;
}

/**
 * Get sponsor logo URL
 */
export function getSponsorLogoUrl(logo: string): string {
  return `/sponsors/${logo}`;
}
