import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, type AudienceSegment } from '../api/client';
import { Panel, Field, Spinner, Modal, Notice, SectionHead } from '../lib/ui';
import { armLabel } from '../lib/labels';


const PLATFORMS: Record<string, { name: string; limit: number; note: string }> = {
  telegram: { name: 'Telegram', limit: 3800, note: 'long read with an image' },
  facebook: { name: 'Facebook', limit: 2500, note: 'page post with an image' },
  instagram: { name: 'Instagram', limit: 2200, note: 'image post with a caption' },
  tiktok: { name: 'TikTok', limit: 2200, note: 'photo or short-video post' },
  youtube: { name: 'YouTube', limit: 4500, note: 'Shorts script and video' },
  mastodon: { name: 'Mastodon', limit: 490, note: 'status on the fediverse' },
  bluesky: { name: 'Bluesky', limit: 290, note: 'short AT Protocol post' },
  local_archive: { name: 'Local archive', limit: 4000, note: 'draft, not published' },
};

const ARM_LABELS: Record<string, string> = {
  topic: 'topic',
  format: 'format',
  tone: 'tone',
  length: 'length',
  time_slot: 'time',
  backend: 'backend',
};

export const StudioTab: React.FC<{ onPublished?: () => void }> = ({ onPublished }) => {
  const [platform, setPlatform] = useState('telegram');
  const [topic, setTopic] = useState('');
  const [lengthBucket, setLengthBucket] = useState('long');
  const [tone, setTone] = useState('analytical');
  const [backend, setBackend] = useState('local');

  const [segments, setSegments] = useState<AudienceSegment[]>([]);
  const [segmentId, setSegmentId] = useState<number | null>(null);
  const [topicIdeas, setTopicIdeas] = useState<string[]>([]);
  const [ideasBusy, setIdeasBusy] = useState(false);

  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any | null>(null);

  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [hashtagInput, setHashtagInput] = useState('');
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [mediaPrompt, setMediaPrompt] = useState<string>('');
  const [imageBusy, setImageBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState<{ ok: boolean; url?: string | null; message?: string } | null>(null);

  const sheetRef = useRef<HTMLDivElement>(null);
  const [handles, setHandles] = useState<Record<string, string>>({});
  const meta = { ...(PLATFORMS[platform] ?? PLATFORMS.telegram), handle: handles[platform] ?? '—' };

  useEffect(() => {
    // Назва каналу — з підключеного акаунта, а не зашита в код
    api
      .getConnectedAccounts()
      .then((list) =>
        setHandles(Object.fromEntries((Array.isArray(list) ? list : []).map((a) => [a.platform_code, a.external_account_id]))),
      )
      .catch(() => setHandles({}));
  }, []);
  const hashtags = useMemo(
    () => hashtagInput.split(',').map((t) => t.trim().replace(/^#/, '')).filter(Boolean),
    [hashtagInput],
  );

  useEffect(() => {
    api
      .getAudienceSegments()
      .then((list) => {
        const arr = Array.isArray(list) ? list : [];
        setSegments(arr);
        if (arr.length && segmentId === null) setSegmentId(arr[0].id);
      })
      .catch(() => setSegments([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadTopicIdeas = async () => {
    setIdeasBusy(true);
    setError(null);
    try {
      const res = await api.suggestTopics(segmentId, 8);
      setTopicIdeas(res.topics || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to suggest topics');
    } finally {
      setIdeasBusy(false);
    }
  };

  useEffect(() => {
    if (!loading) return;
    setElapsed(0);
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [loading]);

  useEffect(() => {
    const ci = result?.content_item;
    if (!ci) return;
    setTitle(ci.title || '');
    setBody(ci.body || '');
    setHashtagInput((ci.hashtags || []).join(', '));
    setImageUrl(ci.media_refs?.image_url || null);
    setMediaPrompt(ci.media_refs?.media_prompt || '');
    setEditing(false);
    setSaved(false);
    setPublished(null);
    setTimeout(() => sheetRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }, [result]);

  const generate = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.generateContent({
        platform_code: platform,
        topic: topic.trim() || undefined,
        tone,
        length_bucket: lengthBucket,
        backend,
        segment_id: segmentId ?? undefined,
      });
      setResult(res);
    } catch (e: any) {
      setError(e?.message || 'Failed to generate the post');
    } finally {
      setLoading(false);
    }
  };

  const saveEdits = async () => {
    if (!result?.content_item?.id) return;
    await api.updateContentItem(result.content_item.id, { title, body, hashtags });
    setSaved(true);
    setEditing(false);
    setTimeout(() => setSaved(false), 2500);
  };

  const regenerateImage = async () => {
    if (!result?.content_item?.id) return;
    setImageBusy(true);
    try {
      const res = await api.regenerateContentImage(result.content_item.id, mediaPrompt || undefined);
      setImageUrl(res.image_url);
    } catch (e: any) {
      setError(e?.message || 'Failed to regenerate the image');
    } finally {
      setImageBusy(false);
    }
  };

  const copyAll = async () => {
    const text = [title, '', body, '', hashtags.map((h) => `#${h}`).join(' ')].join('\n');
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const publish = async () => {
    if (!result?.content_item?.id) return;
    setPublishing(true);
    try {
      await api.updateContentItem(result.content_item.id, { title, body, hashtags });
      const res = await api.publishContentItem(result.content_item.id);
      setPublished({ ok: !!res.success, url: res.url, message: res.message || res.error });
      onPublished?.();
    } catch (e: any) {
      setPublished({ ok: false, message: e?.message || 'Publishing failed' });
    } finally {
      setPublishing(false);
      setConfirmOpen(false);
    }
  };

  const arms = result?.decision?.chosen_arms as Record<string, string> | undefined;

  return (
    <div className="space-y-12">
      <SectionHead
        eyebrow="step one"
        title="What are we writing about today?"
        note="Describe the topic in your own words — or leave the field empty and the decision engine will pick a topic based on accumulated engagement statistics."
      />

      <Panel className="p-7 md:p-9">
        <div className="space-y-7">
          <Field label="Post topic">
            <textarea
              className="field min-h-28 resize-y leading-relaxed"
              placeholder="For example: why the Saga pattern suits microservices better than two-phase commit…"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </Field>

          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={loadTopicIdeas}
                disabled={ideasBusy}
              >
                {ideasBusy ? <><Spinner /> Finding topics…</> : 'Suggest topics'}
              </button>
              <span className="text-xs text-faint">
                {segments.length
                  ? 'topics are tailored to the selected segment profile'
                  : 'no segments yet — topics will be generic'}
              </span>
            </div>

            {topicIdeas.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {topicIdeas.map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTopic(t)}
                    className="btn btn-ghost btn-sm !font-normal"
                  >
                    {t}
                  </button>
                ))}
              </div>
            )}
          </div>

          <hr className="rule" />

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Target segment" hint="affects both topics and parameter choice">
              <select
                className="field"
                value={segmentId ?? ''}
                onChange={(e) => setSegmentId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">No segment</option>
                {segments.map((sg) => (
                  <option key={sg.id} value={sg.id}>{sg.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Network">
              <select className="field" value={platform} onChange={(e) => setPlatform(e.target.value)}>
                {Object.entries(PLATFORMS).map(([code, p]) => (
                  <option key={code} value={code}>{p.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Length">
              <select className="field" value={lengthBucket} onChange={(e) => setLengthBucket(e.target.value)}>
                <option value="long">Article — 5–7 paragraphs</option>
                <option value="medium">Post — 4–5 paragraphs</option>
                <option value="short">Short — 2–3 paragraphs</option>
              </select>
            </Field>
            <Field label="Tone">
              <select className="field" value={tone} onChange={(e) => setTone(e.target.value)}>
                <option value="analytical">Analytical</option>
                <option value="provocative">Provocative</option>
                <option value="humorous">Humorous</option>
                <option value="inspirational">Inspirational</option>
              </select>
            </Field>
            <Field label="Model">
              <select className="field" value={backend} onChange={(e) => setBackend(e.target.value)}>
                <option value="local">Local (Bonsai 27B)</option>
                <option value="cloud">Cloud fallback</option>
              </select>
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-5 pt-1">
            <button className="btn btn-primary" onClick={generate} disabled={loading}>
              {loading ? <><Spinner /> Writing…</> : 'Create post'}
            </button>
            <span className="text-sm text-faint">
              {loading
                ? `Local model running for ${elapsed} s — the article and image are generated one after the other`
                : `Publishing to: ${meta.name} · ${meta.note}`}
            </span>
          </div>

          {error && <Notice kind="bad">{error}</Notice>}
        </div>
      </Panel>

      {result?.content_item && (
        <div ref={sheetRef} className="space-y-6 rise">
          <SectionHead
            eyebrow="step two"
            title="Your post is ready"
            note="Review it, edit the text or replace the image if needed — then publish."
            right={
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-ghost btn-sm" onClick={() => setEditing((v) => !v)}>
                  {editing ? 'Preview' : 'Edit'}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={copyAll}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
                <button className="btn btn-gold btn-sm" onClick={() => setConfirmOpen(true)}>
                  Publish
                </button>
              </div>
            }
          />

          {saved && <Notice kind="good">Changes saved.</Notice>}
          {published && (
            <Notice kind={published.ok ? 'good' : 'bad'}>
              {published.ok ? 'Post published. ' : 'Publishing failed. '}
              {published.message}
              {published.url && (
                <a className="underline ml-1" href={published.url} target="_blank" rel="noreferrer">
                  Open post
                </a>
              )}
            </Notice>
          )}

          <article className="sheet overflow-hidden">
            {imageUrl && (
              <figure className="m-0 relative">
                <img
                  src={imageUrl}
                  alt={mediaPrompt || title}
                  className="w-full h-auto block"
                />
                {imageBusy && (
                  <div className="absolute inset-0 bg-bistre/80 flex items-center justify-center text-butter gap-3 text-sm">
                    <Spinner /> generating a new image…
                  </div>
                )}
              </figure>
            )}

            <div className="px-7 py-9 md:px-14 md:py-12">
              <div className="eyebrow !text-garnet mb-4">
                {meta.name} · {new Date(result.content_item.created_at).toLocaleDateString('en-GB')}
              </div>

              {editing ? (
                <input
                  className="field !bg-white/60 !text-[#10213a] !border-black/15 font-[family-name:var(--font-display)] text-3xl mb-6"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Title"
                />
              ) : (
                <h1 className="font-[family-name:var(--font-display)] text-[2rem] md:text-[2.6rem] text-[#10213a] mb-7 leading-[1.12]">
                  {title || 'Untitled'}
                </h1>
              )}

              {editing ? (
                <textarea
                  className="field !bg-white/60 !text-[#232a35] !border-black/15 min-h-[26rem] leading-relaxed"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              ) : (
                <div className="article-body">
                  {body.split(/\n{2,}/).map((p, i) => (
                    <p key={i}>{p.trim()}</p>
                  ))}
                </div>
              )}

              <div className="mt-8 pt-6 border-t border-black/10">
                {editing ? (
                  <Field label="Hashtags, comma-separated">
                    <input
                      className="field !bg-white/60 !text-[#232a35] !border-black/15"
                      value={hashtagInput}
                      onChange={(e) => setHashtagInput(e.target.value)}
                    />
                  </Field>
                ) : (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-garnet">
                    {hashtags.map((h) => <span key={h}>#{h}</span>)}
                  </div>
                )}

                <div className="num text-xs text-[#5b6b80] mt-5">
                  {body.length} characters · {meta.name} limit: {meta.limit}
                  {body.length > meta.limit && ' · limit exceeded, the text will be truncated'}
                </div>
              </div>

              {editing && (
                <div className="mt-8 flex flex-wrap gap-3">
                  <button className="btn btn-primary btn-sm" onClick={saveEdits}>Save changes</button>
                  <button className="btn btn-ghost btn-sm !text-[#5b6b80] !border-black/20" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </article>

          <Panel quiet className="p-6 grid gap-6 md:grid-cols-2">
            <div>
              <div className="eyebrow mb-3">image</div>
              <textarea
                className="field min-h-20 text-sm"
                value={mediaPrompt}
                onChange={(e) => setMediaPrompt(e.target.value)}
                placeholder="Scene description in English for the diffusion model"
              />
              <button className="btn btn-ghost btn-sm mt-3" onClick={regenerateImage} disabled={imageBusy}>
                {imageBusy ? <><Spinner /> generating…</> : 'Regenerate'}
              </button>
            </div>

            {arms && (
              <div>
                <div className="eyebrow mb-3">bandit decision</div>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                  {Object.entries(arms).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3 border-b border-[color:var(--hairline)] pb-1">
                      <dt className="text-faint">{ARM_LABELS[k] ?? k}</dt>
                      <dd className="num text-butter">{armLabel(k, String(v))}</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-xs text-faint mt-3 leading-relaxed">
                  Parameters were chosen by the cascade of LinUCB heads; after publishing, audience reactions
                  flow back into the policy as reward.
                </p>
              </div>
            )}
          </Panel>
        </div>
      )}

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Publish this post?"
        footer={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setConfirmOpen(false)}>Cancel</button>
            <button className="btn btn-primary btn-sm" onClick={publish} disabled={publishing}>
              {publishing ? <><Spinner /> Publishing…</> : `Yes, publish`}
            </button>
          </>
        }
      >
        <div className="space-y-4 text-sm">
          <p className="text-dim">
            The post will be sent to the live channel via its platform adapter and saved to the
            local archive.
          </p>
          <dl className="space-y-2">
            <div className="flex justify-between gap-4 border-b border-[color:var(--hairline)] pb-2">
              <dt className="text-faint">Channel</dt>
              <dd className="num text-butter">{meta.handle}</dd>
            </div>
            <div className="flex justify-between gap-4 border-b border-[color:var(--hairline)] pb-2">
              <dt className="text-faint">Title</dt>
              <dd className="text-vanilla text-right max-w-xs truncate">{title || '—'}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-faint">Length</dt>
              <dd className="num text-butter">{body.length} characters</dd>
            </div>
          </dl>
        </div>
      </Modal>
    </div>
  );
};
