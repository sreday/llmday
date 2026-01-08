// src/lib/utils.ts

export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trim() + '...';
}

export function getFirstParagraph(text: string): string {
  const paragraphs = text.split(/\n\n+/);
  for (const p of paragraphs) {
    const trimmed = p.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      return trimmed;
    }
  }
  return '';
}

