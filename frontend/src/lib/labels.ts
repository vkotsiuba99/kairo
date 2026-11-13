/**
 * Українські підписи для машинних ключів, які приходять з бекенду.
 * Англійською лишається тільки те, що не має усталеного перекладу:
 * назви платформ, ідентифікатори моделей та формати файлів.
 */

export const CONTENT_STATUS: Record<string, string> = {
  ready: 'ready',
  draft: 'draft',
  published: 'published',
  scheduled: 'scheduled',
  failed: 'failed',
};

export const POST_STATUS: Record<string, string> = {
  pending: 'scheduled',
  publishing: 'publishing',
  published: 'published',
  failed: 'failed',
  cancelled: 'cancelled',
};

export const ACCOUNT_STATUS: Record<string, string> = {
  active: 'active',
  inactive: 'inactive',
  error: 'error',
};

export const TONES: Record<string, string> = {
  analytical: 'analytical',
  provocative: 'provocative',
  humorous: 'humorous',
  inspirational: 'inspirational',
};

export const LENGTHS: Record<string, string> = {
  short: 'short',
  medium: 'medium',
  long: 'long read',
};

export const FORMATS: Record<string, string> = {
  text: 'text',
  image_post: 'text + image',
  video_short: 'short video',
  educational_thread: 'educational thread',
};

export const TOPICS: Record<string, string> = {
  ai_breakthroughs: 'AI breakthroughs',
  developer_humor: 'developer humor',
  open_source_tools: 'open-source tools',
  tech_career_tips: 'tech career',
  system_architecture: 'system architecture',
};

export const BACKENDS: Record<string, string> = {
  local: 'local model',
  cloud: 'cloud fallback',
};

export const TIME_SLOTS: Record<string, string> = {
  morning_08_10: 'morning, 08–10',
  lunch_12_14: 'lunch, 12–14',
  evening_18_20: 'evening, 18–20',
  night_21_23: 'night, 21–23',
};

/** Підпис для значення руки бандита за назвою виміру. */
export function armLabel(dimension: string, value: string): string {
  const dict: Record<string, Record<string, string>> = {
    topic: TOPICS,
    format: FORMATS,
    tone: TONES,
    length: LENGTHS,
    time_slot: TIME_SLOTS,
    backend: BACKENDS,
  };
  return dict[dimension]?.[value] ?? value;
}

export const uk = (dict: Record<string, string>, key: string | null | undefined) =>
  (key && dict[key]) || key || '—';
