const API_BASE = import.meta.env.VITE_API_URL || '/api/v1';

/**
 * Розбирає невдалу відповідь у зрозуміле повідомлення.
 * Коли бекенд перезапускається, шлюз віддає HTML замість JSON — у такому разі
 * важливо сказати «сервіс недоступний», а не показувати технічну заглушку.
 */
/**
 * Запит із повтором на тимчасові збої шлюзу.
 *
 * Бекенд перезапускається за кілька секунд (перезавантаження коду, оновлення
 * контейнера). Без повтору кожен такий момент виглядав би для користувача як
 * провал генерації, хоча насправді сервіс просто піднімався.
 */
async function fetchRetry(url: string, init?: RequestInit, attempts = 3): Promise<Response> {
  let lastError: unknown = null;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url, init);
      if (![502, 503, 504].includes(res.status) || i === attempts - 1) return res;
    } catch (e) {
      lastError = e;
      if (i === attempts - 1) break;
    }
    await new Promise((r) => setTimeout(r, 1200 * (i + 1)));
  }
  throw new Error(
    lastError instanceof Error && lastError.message.includes('fetch')
      ? 'Cannot reach the backend — check that the api container is running'
      : 'Service unavailable: the backend did not respond after several attempts',
  );
}

async function failure(res: Response, fallback: string): Promise<Error> {
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return new Error('Generation service unavailable: the backend is restarting or not responding');
  }
  try {
    const data = await res.json();
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return new Error(detail);
    if (Array.isArray(detail) && detail.length) {
      return new Error(detail.map((d: any) => d?.msg || '').filter(Boolean).join('; ') || fallback);
    }
  } catch {
    /* тіло не JSON — лишаємо загальне повідомлення */
  }
  return new Error(`${fallback} (code ${res.status})`);
}

export interface Platform {
  id: number;
  code: string;
  display_name: string;
  adapter_class: string;
  config_schema: any;
}

export interface ConnectedAccount {
  id: number;
  platform_id: number;
  platform_code: string;
  external_account_id: string;
  status: string;
  connected_at: string;
}

export interface ContentItem {
  id: number;
  platform_id: number;
  platform_code: string;
  format: string;
  tone: string;
  length_bucket: string;
  title: string | null;
  body: string;
  hashtags: string[];
  media_refs: Record<string, any>;
  status: string;
  created_at: string;
}

export interface SegmentProfile {
  name?: string;
  age?: string | null;
  occupation?: string | null;
  region?: string | null;
  language?: string | null;
  device?: string | null;
  interests?: string[];
  size?: string | null;
}

export interface AudienceSegment {
  id: number;
  label: string;
  size: number;
  updated_at: string;
  source: string;
  profile: SegmentProfile;
  activity_hours: number[];
  peak_hours: number[];
  utc_offset: number;
  description: string;
  centroid_sample: number[];
}

export interface TaxonomyOption {
  id: string;
  label: string;
  utc_offset?: number;
}

export interface AudienceTaxonomy {
  names: string[];
  occupations: TaxonomyOption[];
  ages: TaxonomyOption[];
  regions: TaxonomyOption[];
  languages: TaxonomyOption[];
  devices: TaxonomyOption[];
  interests: TaxonomyOption[];
  sizes: TaxonomyOption[];
}

export interface SuggestedSlot {
  at_utc: string;
  local_hour: number;
  local_label: string;
  score: number;
}

export interface LogEntry {
  id: string;
  at: string | null;
  kind: string;
  level: 'info' | 'success' | 'error';
  title: string;
  detail: string;
  meta: Record<string, string>;
}

export interface ScheduledPost {
  id: number;
  content_item_id: number;
  connected_account_id: number;
  scheduled_at: string;
  published_at: string | null;
  external_post_id: string | null;
  status: string;
  error_message: string | null;
}

export interface PostingPlan {
  mode: 'auto' | 'manual';
  slots: { weekday: number; time: string }[];
  utc_offset: number;
  segment_id: number | null;
  per_day: number;
  min_gap_minutes: number;
}

export interface DashboardMetrics {
  system_status: {
    status: string;
    llm_inference: {
      model: string;
      endpoint: string;
      online: boolean;
      mode: string;
    };
    image_inference: {
      model: string;
      worker_mode: string;
    };
    decision_engine: {
      algorithm: string;
      status: string;
      novelty: string;
    };
    database: {
      engine: string;
      connected: boolean;
    };
  };
  total_posts: number;
  published_posts: number;
  total_telemetry_events: number;
  active_experiments: number;
  latest_experiment_summary: Record<string, any> | null;
  recent_content: ContentItem[];
}

export interface ExperimentResults {
  experiment_id: number;
  name: string;
  config: any;
  summary: Record<string, any>;
  history: {
    bandit: any;
    random: any;
    fixed_heuristic: any;
    greedy_avg: any;
  };
}

/* ---------- Прогнозування попиту ---------- */

export interface DemandPassport {
  code: string;
  name: string;
  category: string;
  category_title: string;
  posts_per_day: number;
  unique_author_ratio: number;
  duplicate_ratio: number;
  promo_ratio: number;
  history_days: number;
  sample_size: number;
  verdict: string;
  verdict_label: string;
  in_menu: boolean;
  measured_at: string | null;
  mentions_collected: number;
}

export interface DemandCategory {
  code: string;
  title: string;
  count: number;
}

export interface DemandCatalog {
  categories: DemandCategory[];
  products: DemandPassport[];
  total: number;
}

export interface DemandCorpusRow {
  platform: string;
  source: string;
  mentions: number;
  authors: number;
  bridged: number;
  promo: number;
  from: string | null;
  to: string | null;
}

export interface DemandProductDetail {
  passport: DemandPassport;
  corpus: DemandCorpusRow[];
}

export interface DemandSeriesPoint {
  day: string;
  mentions: number;
  authors: number;
  engagement: number;
}

export interface DemandSeries {
  code: string;
  name: string;
  days: number;
  points: DemandSeriesPoint[];
}

export const api = {
  async getDemandCatalog(includeRejected = false): Promise<DemandCatalog> {
    const q = includeRejected ? '?include_rejected=true' : '';
    const res = await fetchRetry(`${API_BASE}/demand/catalog${q}`);
    if (!res.ok) throw await failure(res, 'Failed to load the product catalog');
    return res.json();
  },

  async getDemandProduct(code: string): Promise<DemandProductDetail> {
    const res = await fetchRetry(`${API_BASE}/demand/products/${code}`);
    if (!res.ok) throw await failure(res, 'Failed to load the product profile');
    return res.json();
  },

  async getDemandSeries(code: string, days = 90): Promise<DemandSeries> {
    const res = await fetchRetry(`${API_BASE}/demand/products/${code}/series?days=${days}`);
    if (!res.ok) throw await failure(res, 'Failed to load the mention series');
    return res.json();
  },

  async getDashboardMetrics(): Promise<DashboardMetrics> {
    const res = await fetchRetry(`${API_BASE}/dashboard/metrics`);
    if (!res.ok) throw await failure(res, 'Failed to load metrics');
    return res.json();
  },

  async getPlatforms(): Promise<Platform[]> {
    const res = await fetchRetry(`${API_BASE}/platforms`);
    return res.json();
  },

  async getConnectedAccounts(): Promise<ConnectedAccount[]> {
    const res = await fetchRetry(`${API_BASE}/accounts`);
    return res.json();
  },

  async connectAccount(platformId: number, externalId: string, credentials: Record<string, any>) {
    const res = await fetchRetry(`${API_BASE}/platforms/${platformId}/accounts/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ external_account_id: externalId, credentials }),
    });
    return res.json();
  },

  async getAudienceSegments(): Promise<AudienceSegment[]> {
    const res = await fetchRetry(`${API_BASE}/audience/segments`);
    return res.json();
  },

  async getAudienceTaxonomy(): Promise<AudienceTaxonomy> {
    const res = await fetchRetry(`${API_BASE}/audience/taxonomy`);
    return res.json();
  },

  async createSegment(profile: SegmentProfile): Promise<AudienceSegment> {
    const res = await fetchRetry(`${API_BASE}/audience/segments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(profile),
    });
    if (!res.ok) throw await failure(res, 'Failed to save the segment');
    return res.json();
  },

  async updateSegment(id: number, profile: SegmentProfile): Promise<AudienceSegment> {
    const res = await fetchRetry(`${API_BASE}/audience/segments/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(profile),
    });
    if (!res.ok) throw await failure(res, 'Failed to update the segment');
    return res.json();
  },

  async deleteSegment(id: number) {
    const res = await fetchRetry(`${API_BASE}/audience/segments/${id}`, { method: 'DELETE' });
    if (!res.ok) throw await failure(res, 'Failed to delete the segment');
    return res.json();
  },

  async getSuggestedSlots(segmentId: number, days = 3, perDay = 2): Promise<{
    segment_label: string;
    utc_offset: number;
    peak_hours: number[];
    activity_hours: number[];
    slots: SuggestedSlot[];
  }> {
    const res = await fetchRetry(
      `${API_BASE}/schedule/suggest?segment_id=${segmentId}&days=${days}&per_day=${perDay}`,
    );
    if (!res.ok) throw await failure(res, 'Failed to suggest posting times');
    return res.json();
  },

  async cancelScheduledPost(postId: number) {
    const res = await fetchRetry(`${API_BASE}/schedule/${postId}/cancel`, { method: 'POST' });
    if (!res.ok) throw await failure(res, 'Failed to cancel the post');
    return res.json();
  },

  async getLogs(params: { kind?: string; level?: string; limit?: number } = {}): Promise<{
    total: number;
    counts: Record<string, number>;
    kind_labels: Record<string, string>;
    entries: LogEntry[];
  }> {
    const q = new URLSearchParams();
    if (params.kind) q.set('kind', params.kind);
    if (params.level) q.set('level', params.level);
    q.set('limit', String(params.limit ?? 200));
    const res = await fetchRetry(`${API_BASE}/logs?${q.toString()}`);
    return res.json();
  },

  async triggerClustering() {
    const res = await fetchRetry(`${API_BASE}/audience/cluster`, { method: 'POST' });
    return res.json();
  },

  async generateContent(params: {
    platform_code: string;
    topic?: string;
    format_type?: string;
    tone?: string;
    length_bucket?: string;
    backend?: string;
    segment_id?: number;
    auto_schedule_minutes?: number;
  }) {
    const res = await fetchRetry(`${API_BASE}/content/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw await failure(res, 'Failed to generate the post');
    return res.json();
  },

  async suggestTopics(segmentId: number | null, count = 8): Promise<{
    segment_label: string;
    topics: string[];
  }> {
    const q = new URLSearchParams({ count: String(count) });
    if (segmentId) q.set('segment_id', String(segmentId));
    const res = await fetchRetry(`${API_BASE}/content/topics/suggest?${q.toString()}`, { method: 'POST' });
    if (!res.ok) throw await failure(res, 'Failed to suggest topics');
    return res.json();
  },

  async listContent(): Promise<ContentItem[]> {
    const res = await fetchRetry(`${API_BASE}/content`);
    return res.json();
  },

  async schedulePost(contentItemId: number, accountId: number, scheduledAt: string) {
    const res = await fetchRetry(`${API_BASE}/schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content_item_id: contentItemId,
        connected_account_id: accountId,
        scheduled_at: scheduledAt,
      }),
    });
    if (!res.ok) throw await failure(res, 'Failed to schedule');
    return res.json();
  },

  async listScheduledPosts(): Promise<ScheduledPost[]> {
    const res = await fetchRetry(`${API_BASE}/schedule?limit=200`);
    return res.json();
  },

  async publishNow(postId: number) {
    const res = await fetchRetry(`${API_BASE}/schedule/${postId}/publish_now`, { method: 'POST' });
    if (!res.ok) throw await failure(res, 'Publishing failed');
    return res.json();
  },

  async getPostingPlan(accountId: number): Promise<PostingPlan> {
    const res = await fetchRetry(`${API_BASE}/schedule/plans/${accountId}`);
    if (!res.ok) throw await failure(res, 'Failed to load the channel schedule');
    return res.json();
  },

  async savePostingPlan(accountId: number, plan: PostingPlan): Promise<PostingPlan> {
    const res = await fetchRetry(`${API_BASE}/schedule/plans/${accountId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
    });
    if (!res.ok) throw await failure(res, 'Failed to save the schedule');
    return res.json();
  },

  async queueByPlan(contentItemId: number, accountId: number): Promise<{
    post: ScheduledPost;
    mode: string;
    slot_label: string;
  }> {
    const res = await fetchRetry(`${API_BASE}/schedule/queue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_item_id: contentItemId, connected_account_id: accountId }),
    });
    if (!res.ok) throw await failure(res, 'Failed to add to the queue');
    return res.json();
  },

  async publishContentItem(itemId: number) {
    const res = await fetchRetry(`${API_BASE}/content/${itemId}/publish`, { method: 'POST' });
    if (!res.ok) throw await failure(res, 'Failed to publish the post');
    return res.json();
  },

  async updateContentItem(itemId: number, data: { title?: string; body?: string; hashtags?: string[] }) {
    const res = await fetchRetry(`${API_BASE}/content/${itemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw await failure(res, 'Failed to save changes');
    return res.json();
  },

  async regenerateContentImage(itemId: number, customPrompt?: string) {
    const url = new URL(`${window.location.origin}${API_BASE}/content/${itemId}/regenerate-image`);
    if (customPrompt) url.searchParams.set('custom_prompt', customPrompt);
    const res = await fetchRetry(url.toString(), { method: 'POST' });
    if (!res.ok) throw await failure(res, 'Failed to regenerate the image');
    return res.json();
  },


  async runExperiment(params: { name: string; n_rounds: number; seed: number; alpha: number }) {
    const res = await fetchRetry(`${API_BASE}/experiments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) throw await failure(res, 'Failed to start the experiment');
    return res.json();
  },

  async getExperimentResults(expId: number): Promise<ExperimentResults> {
    const res = await fetchRetry(`${API_BASE}/experiments/${expId}/results`);
    return res.json();
  },

  async listExperiments() {
    const res = await fetchRetry(`${API_BASE}/experiments`);
    return res.json();
  },

  async ingestTelemetry(payload: {
    platform_code: string;
    external_post_id: string;
    event_type: string;
    event_value: number;
  }) {
    const res = await fetchRetry(`${API_BASE}/telemetry/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  },
};
