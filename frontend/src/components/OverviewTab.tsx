import React, { useEffect, useState } from 'react';
import { api, type DashboardMetrics, type ContentItem } from '../api/client';
import { Panel, SectionHead, Stat, Empty, Spinner } from '../lib/ui';
import { CONTENT_STATUS, LENGTHS, TONES, uk } from '../lib/labels';

const PLATFORM_NAMES: Record<string, string> = {
  telegram: 'Telegram',
  mastodon: 'Mastodon',
  bluesky: 'Bluesky',
  youtube: 'YouTube',
  facebook: 'Facebook',
  instagram: 'Instagram',
  tiktok: 'TikTok',
  local_archive: 'Archive',
};

const Reader: React.FC<{
  item: ContentItem;
  onClose: () => void;
  onChanged: () => void;
}> = ({ item, onClose, onChanged }) => {
  const [confirming, setConfirming] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string; url?: string | null } | null>(null);

  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title || '');
  const [body, setBody] = useState(item.body || '');
  const [hashtagInput, setHashtagInput] = useState((item.hashtags || []).join(', '));
  const [imageUrl, setImageUrl] = useState<string | null>(item.media_refs?.image_url || null);
  const [mediaPrompt, setMediaPrompt] = useState<string>(item.media_refs?.media_prompt || '');
  const [imageBusy, setImageBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // Esc закриває тільки перегляд: у режимі правки це стерло б незбережені зміни.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !editing) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, editing]);

  const channel = PLATFORM_NAMES[item.platform_code] ?? item.platform_code;
  const hashtags = hashtagInput
    .split(',')
    .map((t) => t.trim().replace(/^#/, ''))
    .filter(Boolean);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateContentItem(item.id, { title, body, hashtags });
      setSaved(true);
      setEditing(false);
      onChanged();
      setTimeout(() => setSaved(false), 2500);
    } catch (e: any) {
      setResult({ ok: false, text: e?.message || 'Failed to save changes' });
    } finally {
      setSaving(false);
    }
  };

  const regenerateImage = async () => {
    setImageBusy(true);
    try {
      const res = await api.regenerateContentImage(item.id, mediaPrompt || undefined);
      setImageUrl(res.image_url);
      onChanged();
    } catch (e: any) {
      setResult({ ok: false, text: e?.message || 'Failed to regenerate the image' });
    } finally {
      setImageBusy(false);
    }
  };

  const publish = async () => {
    setPublishing(true);
    try {
      // Правки зберігаються перед відправкою, щоб у канал пішов саме той текст,
      // який зараз на екрані.
      await api.updateContentItem(item.id, { title, body, hashtags });
      const res = await api.publishContentItem(item.id);
      setResult({
        ok: !!res.success,
        text: res.success ? `Published to ${channel}.` : res.error || 'Failed to publish',
        url: res.url,
      });
      onChanged();
    } catch (e: any) {
      setResult({ ok: false, text: e?.message || 'Failed to publish' });
    } finally {
      setPublishing(false);
      setConfirming(false);
    }
  };

  const paperInput = '!bg-white/60 !text-[#232a35] !border-black/15';

  return (
    <div
      className="fixed inset-0 z-50 bg-[#1e2530]/40 backdrop-blur-sm overflow-y-auto py-10 px-4"
      onClick={() => !editing && onClose()}
    >
      <article
        className="sheet rise max-w-3xl mx-auto overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {imageUrl && (
          <figure className="m-0 relative">
            <img src={imageUrl} alt="" className="w-full aspect-[16/9] object-cover" />
            {imageBusy && (
              <div className="absolute inset-0 bg-bistre/85 flex items-center justify-center gap-3 text-butter text-sm">
                <Spinner /> generating a new image…
              </div>
            )}
          </figure>
        )}

        <div className="px-7 py-9 md:px-14 md:py-12">
          <div className="flex items-start justify-between gap-6 mb-4">
            <div className="eyebrow !text-garnet">
              {channel} · {new Date(item.created_at).toLocaleDateString('en-GB')}
            </div>
            <div className="flex items-center gap-4 text-sm">
              <button
                className="text-[#5b6b80] hover:text-garnet"
                onClick={() => setEditing((v) => !v)}
              >
                {editing ? 'preview' : 'edit'}
              </button>
              <button className="text-[#5b6b80] hover:text-garnet" onClick={onClose}>
                close
              </button>
            </div>
          </div>

          {editing ? (
            <input
              className={`field ${paperInput} font-[family-name:var(--font-display)] text-2xl md:text-3xl mb-6`}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Title"
            />
          ) : (
            <h1 className="font-[family-name:var(--font-display)] text-[2rem] md:text-[2.5rem] text-[#10213a] mb-7 leading-[1.12]">
              {title || 'Untitled'}
            </h1>
          )}

          {editing ? (
            <>
              <textarea
                className={`field ${paperInput} min-h-[28rem] leading-relaxed`}
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
              <div className="num text-xs text-[#5b6b80] mt-2">
                {body.length} characters · separate paragraphs with a blank line
              </div>
            </>
          ) : (
            <div className="article-body">
              {(body || '').split(/\n{2,}/).map((p, i) => (
                <p key={i}>{p.trim()}</p>
              ))}
            </div>
          )}

          <div className="mt-8 pt-6 border-t border-black/10">
            {editing ? (
              <label className="block">
                <span className="block text-[0.8rem] font-semibold text-[#1f3a5f] mb-1.5">
                  Hashtags, comma-separated
                </span>
                <input
                  className={`field ${paperInput}`}
                  value={hashtagInput}
                  onChange={(e) => setHashtagInput(e.target.value)}
                />
              </label>
            ) : (
              !!hashtags.length && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-garnet">
                  {hashtags.map((h) => (
                    <span key={h}>#{h}</span>
                  ))}
                </div>
              )
            )}
          </div>

          {editing && (
            <div className="mt-6 pt-6 border-t border-black/10">
              <span className="block text-[0.8rem] font-semibold text-[#1f3a5f] mb-1.5">
                Image description (in English)
              </span>
              <textarea
                className={`field ${paperInput} min-h-20 text-sm`}
                value={mediaPrompt}
                onChange={(e) => setMediaPrompt(e.target.value)}
                placeholder="A warm-lit workshop bench with brass instruments…"
              />
              <div className="flex flex-wrap gap-3 mt-4">
                <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
                  {saving ? <><Spinner /> Saving…</> : 'Save changes'}
                </button>
                <button
                  className="btn btn-ghost btn-sm !text-[#5b6b80] !border-black/20"
                  onClick={regenerateImage}
                  disabled={imageBusy}
                >
                  {imageBusy ? <><Spinner /> generating…</> : 'Regenerate image'}
                </button>
                <button
                  className="btn btn-ghost btn-sm !text-[#5b6b80] !border-black/20"
                  onClick={() => {
                    setTitle(item.title || '');
                    setBody(item.body || '');
                    setHashtagInput((item.hashtags || []).join(', '));
                    setEditing(false);
                  }}
                >
                  Discard edits
                </button>
              </div>
            </div>
          )}

          {saved && (
            <div className="mt-6 text-sm text-garnet">Changes saved.</div>
          )}

          <div className="mt-8 pt-6 border-t border-black/10 flex flex-wrap items-center gap-4">
            {item.status === 'published' && !result ? (
              <span className="text-sm text-[#5b6b80]">This post is already published to {channel}.</span>
            ) : result ? (
              <span className={`text-sm ${result.ok ? 'text-garnet' : 'text-brick'}`}>
                {result.text}
                {result.url && (
                  <a className="underline ml-2" href={result.url} target="_blank" rel="noreferrer">
                    open
                  </a>
                )}
              </span>
            ) : confirming ? (
              <>
                <button className="btn btn-primary btn-sm" onClick={publish} disabled={publishing}>
                  {publishing ? <><Spinner /> Publishing…</> : `Confirm publishing to ${channel}`}
                </button>
                <button
                  className="btn btn-ghost btn-sm !text-[#5b6b80] !border-black/20"
                  onClick={() => setConfirming(false)}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                className="btn btn-ghost btn-sm !text-[#5b6b80] !border-black/20"
                onClick={() => setConfirming(true)}
              >
                Publish to {channel}
              </button>
            )}
          </div>
        </div>
      </article>
    </div>
  );
};

export const OverviewTab: React.FC<{
  metrics: DashboardMetrics | null;
  setActiveTab: (t: string) => void;
}> = ({ metrics, setActiveTab }) => {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState<ContentItem | null>(null);

  useEffect(() => {
    api
      .listContent()
      .then((d) => setItems(Array.isArray(d) ? d : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [metrics?.total_posts]);

  const sys = metrics?.system_status;

  return (
    <div className="space-y-12">
      <SectionHead
        eyebrow="overview"
        title="What the system has done so far"
        note="Posts generated by the LinUCB cascade and local models, plus the feedback that flows back into the policy."
        right={
          <button className="btn btn-primary btn-sm" onClick={() => setActiveTab('studio')}>
            Create a post
          </button>
        }
      />

      <div className="grid gap-5 sm:grid-cols-3">
        <Stat
          label="Posts"
          value={loading ? '—' : items.length}
          note="generated and saved"
        />
        <Stat label="Published" value={metrics?.published_posts ?? '—'} note="via platform adapters" />
        <Stat
          label="Telemetry events"
          value={metrics?.total_telemetry_events ?? '—'}
          note="reactions, views, comments"
        />
      </div>

      <div>
        <h3 className="dim-title text-2xl mb-1">Post library</h3>
        <p className="text-sm text-faint mb-6">Click a card to read the full text.</p>

        {loading ? (
          <div className="text-dim flex items-center gap-3 py-10">
            <Spinner /> loading…
          </div>
        ) : items.length === 0 ? (
          <Empty
            title="Nothing here yet"
            note="Generate your first post in the Studio — it will show up here along with its image."
            action={
              <button className="btn btn-primary btn-sm" onClick={() => setActiveTab('studio')}>
                Go to Studio
              </button>
            }
          />
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {items.slice(0, 12).map((item) => (
              <button
                key={item.id}
                onClick={() => setReading(item)}
                className="convex convex-hover text-left overflow-hidden group"
              >
                {item.media_refs?.image_url ? (
                  <img
                    src={item.media_refs.image_url}
                    alt=""
                    className="w-full aspect-[16/9] object-cover opacity-85 group-hover:opacity-100 transition-opacity"
                  />
                ) : (
                  <div className="w-full aspect-[16/9] bg-shade" />
                )}
                <div className="p-5">
                  <div className="flex items-center justify-between text-[0.7rem] font-mono uppercase tracking-widest text-gold/80 mb-2">
                    <span>{PLATFORM_NAMES[item.platform_code] ?? item.platform_code}</span>
                    <span className={item.status === 'published' ? 'text-butter' : 'text-faint'}>
                      {uk(CONTENT_STATUS, item.status)}
                    </span>
                  </div>
                  <div className="font-[family-name:var(--font-display)] text-lg text-vanilla leading-snug line-clamp-2">
                    {item.title || 'Untitled'}
                  </div>
                  <p className="text-sm text-faint mt-2 line-clamp-2 leading-relaxed">
                    {(item.body || '').slice(0, 140)}…
                  </p>
                  <div className="num text-[0.7rem] text-faint mt-4">
                    {uk(TONES, item.tone)} · {uk(LENGTHS, item.length_bucket)} ·{' '}
                    {(item.body || '').length} chars
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {sys && (
        <Panel quiet className="p-7">
          <div className="eyebrow mb-5">generation pipeline</div>
          <dl className="grid gap-x-10 gap-y-4 sm:grid-cols-2">
            {[
              ['Text model', sys.llm_inference?.model, sys.llm_inference?.online ? 'online' : 'offline'],
              ['Diffusion model', sys.image_inference?.model, sys.image_inference?.worker_mode],
              ['Decision engine', sys.decision_engine?.algorithm, sys.decision_engine?.status],
              ['Storage', sys.database?.engine, sys.database?.connected ? 'connected' : 'disconnected'],
            ].map(([label, value, note]) => (
              <div key={String(label)} className="border-b border-[color:var(--hairline)] pb-3">
                <dt className="text-xs text-faint">{label}</dt>
                <dd className="text-sm text-vanilla mt-1 break-words">{value ?? '—'}</dd>
                <dd className="text-xs text-gold/80 mt-0.5">{note ?? ''}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      )}

      {reading && (
        <Reader
          item={reading}
          onClose={() => setReading(null)}
          onChanged={() => api.listContent().then((d) => setItems(Array.isArray(d) ? d : []))}
        />
      )}
    </div>
  );
};
