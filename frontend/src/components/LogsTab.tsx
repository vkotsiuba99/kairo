import React, { useCallback, useEffect, useState } from 'react';
import { api, type LogEntry } from '../api/client';
import { SectionHead, Spinner, Empty, Panel } from '../lib/ui';

const LEVELS: { id: string; label: string }[] = [
  { id: '', label: 'All levels' },
  { id: 'info', label: 'Info' },
  { id: 'success', label: 'Success' },
  { id: 'error', label: 'Errors' },
];

const LEVEL_COLOR: Record<string, string> = {
  info: 'var(--color-dim)',
  success: 'var(--color-gold)',
  error: 'var(--color-brick)',
};

const fmt = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('en-GB', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '—';

export const LogsTab: React.FC = () => {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [kind, setKind] = useState('');
  const [level, setLevel] = useState('');
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.getLogs({ kind: kind || undefined, level: level || undefined, limit: 200 });
      setEntries(data.entries || []);
      setLabels(data.kind_labels || {});
      setCounts(data.counts || {});
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [kind, level]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [auto, load]);

  const kinds = ['', ...Object.keys(labels)];

  return (
    <div className="space-y-10">
      <SectionHead
        eyebrow="activity log"
        title="What happened in the system"
        note="The feed is assembled from live records — generation jobs, publications, telemetry events, policy decisions and experiment runs. There is no separate log table, so the log can never drift from the actual state."
        right={
          <div className="flex items-center gap-3">
            <button className="btn btn-ghost btn-sm" onClick={load}>
              Refresh
            </button>
            <button
              className={`btn btn-sm !font-normal ${auto ? 'btn-gold' : 'btn-ghost'}`}
              onClick={() => setAuto((v) => !v)}
            >
              {auto ? 'Auto-refresh on' : 'Auto-refresh off'}
            </button>
          </div>
        }
      />

      <Panel className="p-6">
        <div className="flex flex-wrap items-center gap-2">
          {kinds.map((k) => (
            <button
              key={k || 'all'}
              onClick={() => setKind(k)}
              className={`btn btn-sm !font-normal ${kind === k ? 'btn-gold' : 'btn-ghost'}`}
            >
              {k ? labels[k] : 'All events'}
              {k && counts[k] ? <span className="num opacity-70"> {counts[k]}</span> : null}
            </button>
          ))}
          <span className="w-px h-6 bg-[color:var(--hairline-strong)] mx-2" />
          {LEVELS.map((l) => (
            <button
              key={l.id || 'any'}
              onClick={() => setLevel(l.id)}
              className={`btn btn-sm !font-normal ${level === l.id ? 'btn-gold' : 'btn-ghost'}`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </Panel>

      {loading ? (
        <div className="text-dim flex items-center gap-3 py-10">
          <Spinner /> loading…
        </div>
      ) : entries.length === 0 ? (
        <Empty title="No entries" note="No events match the selected filters." />
      ) : (
        <div className="space-y-2">
          {entries.map((e) => (
            <div
              key={e.id}
              className="panel-quiet px-5 py-4 border-l-2"
              style={{ borderLeftColor: LEVEL_COLOR[e.level] ?? 'var(--color-dim)' }}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div className="text-vanilla text-sm">{e.title}</div>
                <div className="num text-xs text-faint">{fmt(e.at)}</div>
              </div>
              <div className="text-sm text-dim mt-1.5 break-words">{e.detail}</div>
              {Object.keys(e.meta || {}).length > 0 && (
                <div className="flex flex-wrap gap-x-6 gap-y-1 mt-3">
                  {Object.entries(e.meta).map(([k, v]) => (
                    <span key={k} className="text-xs text-faint">
                      {k}: <span className="num text-gold/80">{v}</span>
                    </span>
                  ))}
                </div>
              )}
              <div className="eyebrow mt-3 opacity-70">{labels[e.kind] ?? e.kind}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
