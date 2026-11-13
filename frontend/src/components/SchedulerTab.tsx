import React, { useEffect, useMemo, useState } from 'react';
import {
  api,
  type AudienceSegment,
  type ConnectedAccount,
  type ContentItem,
  type PostingPlan,
  type ScheduledPost,
  type SuggestedSlot,
} from '../api/client';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const OFFSETS = Array.from({ length: 27 }, (_, i) => i - 12);
import { SectionHead, Spinner, Empty, Notice, Field, Panel } from '../lib/ui';

const STATUS_LABELS: Record<string, string> = {
  pending: 'scheduled',
  publishing: 'publishing',
  published: 'published',
  failed: 'failed',
  cancelled: 'cancelled',
};

// Бекенд (SQLite) віддає час публікацій у UTC без позначки поясу —
// без «Z» браузер сприйняв би його як місцевий і зсунув на кілька годин.
const asUtc = (iso: string) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);

const fmt = (iso: string | null) =>
  iso
    ? new Date(asUtc(iso)).toLocaleString('en-GB', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

export const SchedulerTab: React.FC<{ setActiveTab: (t: string) => void }> = ({ setActiveTab }) => {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [segments, setSegments] = useState<AudienceSegment[]>([]);
  const [posts, setPosts] = useState<ScheduledPost[]>([]);
  const [loading, setLoading] = useState(true);

  const [itemId, setItemId] = useState<number | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [segmentId, setSegmentId] = useState<number | null>(null);
  const [slots, setSlots] = useState<SuggestedSlot[]>([]);
  const [slotInfo, setSlotInfo] = useState<{ peaks: number[]; offset: number } | null>(null);
  const [chosenSlot, setChosenSlot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [plan, setPlan] = useState<PostingPlan | null>(null);

  useEffect(() => {
    if (!accountId) return;
    api.getPostingPlan(accountId).then(setPlan).catch(() => setPlan(null));
  }, [accountId]);

  const savePlan = async () => {
    if (!accountId || !plan) return;
    setBusy(true);
    setMessage(null);
    try {
      setPlan(await api.savePostingPlan(accountId, plan));
      setMessage({ ok: true, text: 'Channel schedule saved.' });
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to save the schedule' });
    } finally {
      setBusy(false);
    }
  };

  const queueByPlan = async () => {
    if (!itemId || !accountId) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await api.queueByPlan(itemId, accountId);
      setMessage({ ok: true, text: `Queued by schedule: ${r.slot_label}.` });
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to add to the queue' });
    } finally {
      setBusy(false);
    }
  };

  const setSlot = (i: number, patch: Partial<PostingPlan['slots'][number]>) =>
    plan && setPlan({ ...plan, slots: plan.slots.map((s, k) => (k === i ? { ...s, ...patch } : s)) });

  const load = async () => {
    try {
      const [c, a, s, p] = await Promise.all([
        api.listContent(),
        api.getConnectedAccounts(),
        api.getAudienceSegments(),
        api.listScheduledPosts(),
      ]);
      const drafts = (Array.isArray(c) ? c : []).filter((x) => x.status !== 'published');
      setItems(drafts);
      setAccounts(Array.isArray(a) ? a : []);
      setSegments(Array.isArray(s) ? s : []);
      setPosts(Array.isArray(p) ? p : []);
      if (drafts.length && itemId === null) setItemId(drafts[0].id);
      if (Array.isArray(a) && a.length && accountId === null) setAccountId(a[0].id);
      const withCurve = (Array.isArray(s) ? s : []).filter((x) => x.activity_hours?.length);
      if (withCurve.length && segmentId === null) setSegmentId(withCurve[0].id);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!segmentId) {
      setSlots([]);
      return;
    }
    api
      .getSuggestedSlots(segmentId, 3, 2)
      .then((r) => {
        setSlots(r.slots);
        setSlotInfo({ peaks: r.peak_hours, offset: r.utc_offset });
        setChosenSlot(r.slots[0]?.at_utc ?? null);
      })
      .catch((e) => {
        setSlots([]);
        setMessage({ ok: false, text: e?.message || 'Failed to suggest posting times' });
      });
  }, [segmentId]);

  const schedule = async () => {
    if (!itemId || !accountId || !chosenSlot) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.schedulePost(itemId, accountId, chosenSlot);
      setMessage({ ok: true, text: `Post scheduled for ${fmt(chosenSlot)}.` });
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to schedule' });
    } finally {
      setBusy(false);
    }
  };

  const publishNow = async (post: ScheduledPost) => {
    setBusy(true);
    try {
      await api.publishNow(post.id);
      setMessage({ ok: true, text: 'Post published.' });
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Publishing failed' });
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (post: ScheduledPost) => {
    try {
      await api.cancelScheduledPost(post.id);
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to cancel' });
    }
  };

  const segment = useMemo(
    () => segments.find((s) => s.id === segmentId) ?? null,
    [segments, segmentId],
  );
  const segmentsWithCurve = segments.filter((s) => s.activity_hours?.length);

  if (loading) {
    return (
      <div className="text-dim flex items-center gap-3 py-16">
        <Spinner /> loading…
      </div>
    );
  }

  return (
    <div className="space-y-12">
      <SectionHead
        eyebrow="scheduler"
        title="When people will see it"
        note="Posting times come from the selected segment’s activity curve, not from guesswork: an office audience peaks in the evening, while a night-owl segment in another time zone has its own peak."
      />

      {message && <Notice kind={message.ok ? 'good' : 'bad'}>{message.text}</Notice>}

      {segmentsWithCurve.length === 0 ? (
        <Empty
          title="Describe your audience first"
          note="The scheduler relies on a segment’s daily activity curve. Set up a target audience profile and recommended slots will appear here."
          action={
            <button className="btn btn-primary btn-sm" onClick={() => setActiveTab('audience')}>
              Go to Audience
            </button>
          }
        />
      ) : items.length === 0 ? (
        <Empty
          title="No posts to schedule"
          note="All generated posts are already published. Create a new one in the Studio."
          action={
            <button className="btn btn-primary btn-sm" onClick={() => setActiveTab('studio')}>
              Go to Studio
            </button>
          }
        />
      ) : (
        <Panel className="p-7 md:p-9">
          <div className="grid gap-6 lg:grid-cols-3">
            <Field label="Post">
              <select
                className="field"
                value={itemId ?? ''}
                onChange={(e) => setItemId(Number(e.target.value))}
              >
                {items.map((i) => (
                  <option key={i.id} value={i.id}>
                    #{i.id} · {(i.title || 'untitled').slice(0, 46)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Channel">
              <select
                className="field"
                value={accountId ?? ''}
                onChange={(e) => setAccountId(Number(e.target.value))}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.platform_code} · {a.external_account_id}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Target segment" hint="its activity curve determines the recommended time">
              <select
                className="field"
                value={segmentId ?? ''}
                onChange={(e) => setSegmentId(Number(e.target.value))}
              >
                {segmentsWithCurve.map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </Field>
          </div>

          {segment && slotInfo && (
            <div className="text-sm text-faint mt-6">
              {segment.label}: activity peak at{' '}
              <span className="num text-butter">
                {slotInfo.peaks.map((h) => `${String(h).padStart(2, '0')}:00`).join(', ')}
              </span>{' '}
              local time (UTC{slotInfo.offset >= 0 ? '+' : ''}
              {slotInfo.offset}). The nearest such windows are listed below.
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            {slots.map((s) => (
              <button
                key={s.at_utc}
                onClick={() => setChosenSlot(s.at_utc)}
                className={`btn btn-sm !font-normal ${chosenSlot === s.at_utc ? 'btn-gold' : 'btn-ghost'}`}
                title={`Activity curve match: ${Math.round(s.score * 100)}%`}
              >
                {s.local_label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4 mt-7">
            <button className="btn btn-primary" onClick={schedule} disabled={busy || !chosenSlot}>
              {busy ? <><Spinner /> Scheduling…</> : 'Schedule post'}
            </button>
            <button
              className="btn btn-ghost"
              onClick={queueByPlan}
              disabled={busy || !itemId || !accountId}
              title="Next free slot from the channel schedule (below)"
            >
              Queue by schedule
            </button>
            {chosenSlot && (
              <span className="text-sm text-faint">
                goes out at <span className="num text-butter">{fmt(chosenSlot)}</span> your time
              </span>
            )}
          </div>
        </Panel>
      )}

      {plan && accountId && (
        <Panel className="p-7 md:p-9">
          <h3 className="dim-title text-2xl mb-2">Channel schedule</h3>
          <p className="text-sm text-faint mb-6">
            When this channel’s posts go out. “Auto” uses the peaks of the segment’s activity curve; a custom
            schedule uses the days and times you set. “Queue by schedule” places the post in the
            next free slot.
          </p>

          <div className="flex flex-wrap gap-2 mb-6">
            {(['auto', 'manual'] as const).map((m) => (
              <button
                key={m}
                className={`btn btn-sm ${plan.mode === m ? 'btn-gold' : 'btn-ghost'}`}
                onClick={() =>
                  setPlan({
                    ...plan,
                    mode: m,
                    slots: m === 'manual' && !plan.slots.length ? [{ weekday: 0, time: '09:00' }] : plan.slots,
                  })
                }
              >
                {m === 'auto' ? 'Auto — based on segment activity' : 'Custom schedule'}
              </button>
            ))}
          </div>

          {plan.mode === 'auto' ? (
            <div className="grid gap-6 md:grid-cols-2">
              <Field label="Segment" hint="the scheduler uses its activity curve">
                <select
                  className="field"
                  value={plan.segment_id ?? ''}
                  onChange={(e) => setPlan({ ...plan, segment_id: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">first segment with a curve</option>
                  {segmentsWithCurve.map((s) => (
                    <option key={s.id} value={s.id}>{s.label}</option>
                  ))}
                </select>
              </Field>
              <Field label="Posts per day">
                <select
                  className="field"
                  value={plan.per_day}
                  onChange={(e) => setPlan({ ...plan, per_day: Number(e.target.value) })}
                >
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </Field>
            </div>
          ) : (
            <div className="space-y-3">
              {plan.slots.map((s, i) => (
                <div key={i} className="flex flex-wrap items-center gap-3">
                  <select
                    className="field !w-28"
                    value={s.weekday}
                    onChange={(e) => setSlot(i, { weekday: Number(e.target.value) })}
                  >
                    {WEEKDAYS.map((d, k) => <option key={d} value={k}>{d}</option>)}
                  </select>
                  <input
                    type="time"
                    className="field !w-36"
                    value={s.time}
                    onChange={(e) => setSlot(i, { time: e.target.value })}
                  />
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setPlan({ ...plan, slots: plan.slots.filter((_, k) => k !== i) })}
                    disabled={plan.slots.length <= 1}
                  >
                    Remove
                  </button>
                </div>
              ))}
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setPlan({ ...plan, slots: [...plan.slots, { weekday: 2, time: '18:00' }] })}
              >
                + Add slot
              </button>
              <div className="max-w-xs pt-2">
                <Field label="Schedule time zone">
                  <select
                    className="field"
                    value={plan.utc_offset}
                    onChange={(e) => setPlan({ ...plan, utc_offset: Number(e.target.value) })}
                  >
                    {OFFSETS.map((o) => (
                      <option key={o} value={o}>UTC{o >= 0 ? '+' : ''}{o}</option>
                    ))}
                  </select>
                </Field>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-4 mt-7">
            <button className="btn btn-primary" onClick={savePlan} disabled={busy}>
              Save schedule
            </button>
            <span className="text-sm text-faint">
              minimum gap between posts — {plan.min_gap_minutes} min
            </span>
          </div>
        </Panel>
      )}

      <div>
        <h3 className="dim-title text-2xl mb-6">Publishing queue</h3>

        {posts.length === 0 ? (
          <Empty title="The queue is empty" note="Scheduled posts will appear here." />
        ) : (
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm min-w-[42rem]">
              <thead>
                <tr className="text-left text-[0.7rem] uppercase tracking-widest text-gold/70 font-mono">
                  <th className="p-4 font-normal">Post</th>
                  <th className="p-4 font-normal">Publish time</th>
                  <th className="p-4 font-normal">Status</th>
                  <th className="p-4 font-normal text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => (
                  <tr key={p.id} className="border-t border-[color:var(--hairline)]">
                    <td className="p-4 text-vanilla">#{p.content_item_id}</td>
                    <td className="p-4 num text-dim">{fmt(p.published_at || p.scheduled_at)}</td>
                    <td className="p-4">
                      <span
                        className={
                          p.status === 'published'
                            ? 'text-butter'
                            : p.status === 'failed'
                              ? 'text-brick'
                              : 'text-dim'
                        }
                      >
                        {STATUS_LABELS[p.status] ?? p.status}
                      </span>
                      {p.error_message && (
                        <div className="text-xs text-brick mt-1">{p.error_message}</div>
                      )}
                    </td>
                    <td className="p-4 text-right whitespace-nowrap">
                      {p.status === 'pending' ? (
                        <>
                          <button
                            className="btn btn-ghost btn-sm mr-2"
                            onClick={() => publishNow(p)}
                            disabled={busy}
                          >
                            Publish now
                          </button>
                          <button className="btn btn-ghost btn-sm" onClick={() => cancel(p)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <span className="text-faint text-xs">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
