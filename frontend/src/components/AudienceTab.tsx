import React, { useEffect, useMemo, useState } from 'react';
import {
  api,
  type AudienceSegment,
  type AudienceTaxonomy,
  type SegmentProfile,
} from '../api/client';
import { SectionHead, Spinner, Empty, Notice, Field, Panel } from '../lib/ui';

const EMPTY: SegmentProfile = {
  name: '',
  age: '25_34',
  occupation: 'office_9_18',
  region: 'ua',
  language: 'uk',
  device: 'mixed',
  interests: [],
  size: '1000',
};

/** Смужковий графік добової активності сегмента. */
const ActivityChart: React.FC<{ hours: number[]; peaks: number[] }> = ({ hours, peaks }) => (
  <div>
    <div className="flex items-end gap-[2px] h-16">
      {hours.map((v, h) => (
        <div
          key={h}
          title={`${String(h).padStart(2, '0')}:00 — ${Math.round(v * 100)}%`}
          className="flex-1 rounded-t-[2px]"
          style={{
            height: `${Math.max(4, v * 100)}%`,
            background: peaks.includes(h) ? 'var(--color-gold)' : 'rgba(74, 111, 165, 0.38)',
          }}
        />
      ))}
    </div>
    <div className="flex justify-between num text-[0.65rem] text-faint mt-1.5">
      <span>00</span>
      <span>06</span>
      <span>12</span>
      <span>18</span>
      <span>23</span>
    </div>
  </div>
);

const CheckGroup: React.FC<{
  options: { id: string; label: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}> = ({ options, selected, onToggle }) => (
  <div className="flex flex-wrap gap-2">
    {options.map((o) => {
      const on = selected.includes(o.id);
      return (
        <button
          key={o.id}
          type="button"
          onClick={() => onToggle(o.id)}
          className={`btn btn-sm !font-normal ${on ? 'btn-gold' : 'btn-ghost'}`}
        >
          {o.label}
        </button>
      );
    })}
  </div>
);

export const AudienceTab: React.FC = () => {
  const [tax, setTax] = useState<AudienceTaxonomy | null>(null);
  const [segments, setSegments] = useState<AudienceSegment[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<SegmentProfile>(EMPTY);

  const load = async () => {
    try {
      const [t, s] = await Promise.all([api.getAudienceTaxonomy(), api.getAudienceSegments()]);
      setTax(t);
      setSegments(Array.isArray(s) ? s : []);
      setForm((f) => ({ ...f, name: f.name || t.names[0] }));
    } catch {
      setMessage({ ok: false, text: 'Failed to load reference data' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const toggle = (key: 'interests') => (id: string) =>
    setForm((f) => {
      const list = f[key] ?? [];
      return { ...f, [key]: list.includes(id) ? list.filter((x) => x !== id) : [...list, id] };
    });

  const save = async () => {
    if (!form.name) return;
    setSaving(true);
    setMessage(null);
    try {
      if (editingId) {
        await api.updateSegment(editingId, form);
        setMessage({ ok: true, text: 'Segment profile updated.' });
      } else {
        await api.createSegment(form);
        setMessage({ ok: true, text: 'Segment created — the scheduler already takes its activity curve into account.' });
      }
      setEditingId(null);
      setForm({ ...EMPTY, name: tax?.names[0] ?? '' });
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to save' });
    } finally {
      setSaving(false);
    }
  };

  const edit = (seg: AudienceSegment) => {
    setEditingId(seg.id);
    setForm({ ...EMPTY, ...seg.profile, name: seg.label });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const remove = async (seg: AudienceSegment) => {
    try {
      await api.deleteSegment(seg.id);
      await load();
    } catch (e: any) {
      setMessage({ ok: false, text: e?.message || 'Failed to delete' });
    }
  };

  const totalReach = useMemo(
    () => segments.reduce((sum, s) => sum + (s.size || 0), 0),
    [segments],
  );

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
        eyebrow="target audience"
        title="Who we write for"
        note="An audience profile is built by choosing from preset options — no free-form input. From these choices the system computes a daily activity curve, which the publishing scheduler relies on."
      />

      {message && <Notice kind={message.ok ? 'good' : 'bad'}>{message.text}</Notice>}

      <Panel className="p-7 md:p-9">
        <h3 className="dim-title text-2xl mb-6">
          {editingId ? 'Edit segment' : 'New segment'}
        </h3>

        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Segment name">
            <select
              className="field"
              value={form.name ?? ''}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            >
              {tax?.names.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </Field>

          <Field label="Age">
            <select
              className="field"
              value={form.age ?? ''}
              onChange={(e) => setForm({ ...form, age: e.target.value })}
            >
              {tax?.ages.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </Field>

          <Field label="Occupation" hint="sets the base shape of the daily curve">
            <select
              className="field"
              value={form.occupation ?? ''}
              onChange={(e) => setForm({ ...form, occupation: e.target.value })}
            >
              {tax?.occupations.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </Field>

          <Field label="Region" hint="sets the time zone used to calculate posting times">
            <select
              className="field"
              value={form.region ?? ''}
              onChange={(e) => setForm({ ...form, region: e.target.value })}
            >
              {tax?.regions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label} (UTC{(o.utc_offset ?? 0) >= 0 ? '+' : ''}{o.utc_offset})
                </option>
              ))}
            </select>
          </Field>

          <Field label="Content language">
            <select
              className="field"
              value={form.language ?? ''}
              onChange={(e) => setForm({ ...form, language: e.target.value })}
            >
              {tax?.languages.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </Field>

          <Field label="Device">
            <select
              className="field"
              value={form.device ?? ''}
              onChange={(e) => setForm({ ...form, device: e.target.value })}
            >
              {tax?.devices.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </Field>

          <Field label="Approximate size">
            <select
              className="field"
              value={form.size ?? ''}
              onChange={(e) => setForm({ ...form, size: e.target.value })}
            >
              {tax?.sizes.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-8">
          <div className="text-[0.8rem] font-semibold text-butter mb-1">Interests</div>
          <div className="text-xs text-faint mb-3">
            Pick as many as you like. Interests affect more than topics: fishing boosts the
            pre-dawn hours, gaming the night, cooking the time before dinner. The system works out
            activity times on its own — no need to enter them manually.
          </div>
          <div className="max-h-64 overflow-y-auto pr-2">
            <CheckGroup
              options={tax?.interests ?? []}
              selected={form.interests ?? []}
              onToggle={toggle('interests')}
            />
          </div>
          {(form.interests?.length ?? 0) > 0 && (
            <div className="text-xs text-faint mt-3">
              selected: <span className="num text-butter">{form.interests?.length}</span>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4 mt-8">
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? <><Spinner /> Saving…</> : editingId ? 'Save changes' : 'Create segment'}
          </button>
          {editingId && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setEditingId(null);
                setForm({ ...EMPTY, name: tax?.names[0] ?? '' });
              }}
            >
              Cancel editing
            </button>
          )}
        </div>
      </Panel>

      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-3 mb-6">
          <h3 className="dim-title text-2xl">Segments</h3>
          {segments.length > 0 && (
            <span className="text-sm text-faint">
              total reach:{' '}
              <span className="num text-butter">{totalReach.toLocaleString('en-GB')}</span>
            </span>
          )}
        </div>

        {segments.length === 0 ? (
          <Empty
            title="No segments yet"
            note="Create your first audience profile using the form above — without it, the scheduler has no activity curve to go by."
          />
        ) : (
          <div className="grid gap-5 md:grid-cols-2">
            {segments.map((seg) => (
              <div key={seg.id} className="convex convex-hover p-6">
                <div className="flex items-baseline justify-between gap-4">
                  <h4 className="dim-title-sm text-xl text-vanilla">{seg.label}</h4>
                  <span className="num text-butter text-lg">
                    {(seg.size || 0).toLocaleString('en-GB')}
                  </span>
                </div>

                <div className="text-sm text-dim mt-2 leading-relaxed">
                  {seg.description || 'No profile set — this segment was produced by clustering'}
                </div>

                {seg.activity_hours?.length > 0 && (
                  <div className="mt-5">
                    <div className="eyebrow mb-2">activity by hour</div>
                    <ActivityChart hours={seg.activity_hours} peaks={seg.peak_hours} />
                    <div className="text-xs text-faint mt-2">
                      Best time:{' '}
                      <span className="num text-butter">
                        {seg.peak_hours.map((h) => `${String(h).padStart(2, '0')}:00`).join(', ')}
                      </span>{' '}
                      local time (UTC{seg.utc_offset >= 0 ? '+' : ''}
                      {seg.utc_offset})
                    </div>
                  </div>
                )}

                <div className="flex gap-2 mt-5">
                  <button className="btn btn-ghost btn-sm" onClick={() => edit(seg)}>
                    Edit
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => remove(seg)}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
