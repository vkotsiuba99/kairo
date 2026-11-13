import React, { useEffect, useState } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';
import { api, type ExperimentResults } from '../api/client';
import { Panel, Field, SectionHead, Spinner, Modal, Notice } from '../lib/ui';

const POLICIES = [
  { key: 'bandit', label: 'Factorized LinUCB', color: '#2c5282', width: 2.6 },
  { key: 'random', label: 'Random', color: '#a3acb9', width: 1.8 },
  { key: 'greedy_avg', label: 'Greedy average', color: '#5b8bc7', width: 1.8 },
  { key: 'fixed_heuristic', label: 'Fixed heuristic', color: '#4a5565', width: 1.8 },
];

const axis = { stroke: '#5b6b80', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' };

const tooltipStyle = {
  background: '#ffffff',
  border: '1px solid rgba(30,50,80,0.25)',
  borderRadius: 2,
  fontSize: 12,
  color: '#1e2530',
};

export const ExperimentsTab: React.FC = () => {
  const [nRounds, setNRounds] = useState(300);
  const [seed, setSeed] = useState(42);
  const [alpha, setAlpha] = useState(0.8);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<ExperimentResults | null>(null);
  const [chartData, setChartData] = useState<any[]>([]);
  const [latexOpen, setLatexOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!loading) return;
    setElapsed(0);
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [loading]);

  useEffect(() => {
    (async () => {
      try {
        const exps = await api.listExperiments();
        if (Array.isArray(exps) && exps.length) {
          // API віддає список у довільному порядку — беремо найсвіжіший за id
          const latest = exps.reduce((a: any, b: any) => (b.id > a.id ? b : a));
          const res = await api.getExperimentResults(latest.id);
          setResults(res);
          buildChart(res.history);
        }
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  const buildChart = (history: any) => {
    if (!history?.bandit?.round) return;
    const rounds: number[] = history.bandit.round;
    const step = rounds.length > 150 ? Math.ceil(rounds.length / 120) : 1;
    const rows: any[] = [];
    for (let i = 0; i < rounds.length; i += step) {
      const row: any = { round: rounds[i] };
      POLICIES.forEach((p) => {
        row[`${p.key}_reward`] = Number(history[p.key]?.cumulative_reward?.[i]?.toFixed(2) ?? 0);
        row[`${p.key}_regret`] = Number(history[p.key]?.cumulative_regret?.[i]?.toFixed(2) ?? 0);
      });
      rows.push(row);
    }
    setChartData(rows);
  };

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const exp = await api.runExperiment({
        name: `Synthetic validation · N=${nRounds}, α=${alpha}`,
        n_rounds: nRounds,
        seed,
        alpha,
      });
      const res = await api.getExperimentResults(exp.id);
      setResults(res);
      buildChart(res.history);
    } catch (e: any) {
      setError(e?.message || 'Failed to run the simulation');
    } finally {
      setLoading(false);
    }
  };

  const exportJson = () => {
    if (!results) return;
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `kairo_experiment_seed${seed}.json`;
    a.click();
  };

  const latex = () => {
    const rows = POLICIES.map((p) => {
      const s: any = results?.summary?.[p.key as keyof typeof results.summary] ?? {};
      const ctr = s.final_avg_ctr != null ? `${(s.final_avg_ctr * 100).toFixed(1)}\\%` : '--';
      return `${p.label} & ${s.total_cumulative_reward?.toFixed?.(2) ?? '--'} & ${
        s.total_cumulative_regret?.toFixed?.(2) ?? '--'
      } & ${ctr} \\\\`;
    }).join('\n');

    return [
      '\\begin{table}[h]',
      '\\centering',
      `\\caption{Comparison of decision policies (N=${nRounds}, seed=${seed}, $\\alpha$=${alpha})}`,
      '\\begin{tabular}{lccc}',
      '\\hline',
      'Policy & Cum. reward & Cum. regret & Avg. CTR \\\\',
      '\\hline',
      rows,
      '\\hline',
      '\\end{tabular}',
      '\\end{table}',
    ].join('\n');
  };

  const summary = results?.summary ?? {};
  const bestReward = Math.max(
    ...POLICIES.map((p) => (summary as any)?.[p.key]?.total_cumulative_reward ?? -Infinity),
  );

  return (
    <div className="space-y-12">
      <SectionHead
        eyebrow="offline validation"
        title="Is the bandit really better than a heuristic?"
        note="An audience simulator with delayed feedback and repetition fatigue runs four policies on identical data. Regret is measured against an oracle that knows the optimal choice."
      />

      <Panel className="p-7 md:p-9">
        <div className="grid gap-6 sm:grid-cols-3">
          <Field label="Simulation rounds" hint="how many posts each policy goes through">
            <input
              type="number"
              className="field num"
              value={nRounds}
              min={20}
              max={5000}
              onChange={(e) => setNRounds(Number(e.target.value))}
            />
          </Field>
          <Field label="Random seed" hint="makes the experiment reproducible">
            <input
              type="number"
              className="field num"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </Field>
          <Field label="α — exploration vs. exploitation" hint="higher α means bolder tries of new options">
            <input
              type="number"
              step="0.1"
              className="field num"
              value={alpha}
              onChange={(e) => setAlpha(Number(e.target.value))}
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-4 mt-7">
          <button className="btn btn-primary" onClick={run} disabled={loading}>
            {loading ? <><Spinner /> Computing…</> : 'Run simulation'}
          </button>
          {loading && (
            <span className="text-sm text-faint">
              {nRounds} rounds × 4 policies plus the oracle — usually about a minute ({elapsed} s)
            </span>
          )}
          {!loading && results && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={exportJson}>Export JSON</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setLatexOpen(true)}>
                Table for the thesis (LaTeX)
              </button>
            </>
          )}
        </div>

        {error && <div className="mt-5"><Notice kind="bad">{error}</Notice></div>}
      </Panel>

      {results && (
        <>
          <div className="grid gap-8 lg:grid-cols-2">
            <Panel className="p-6">
              <div className="flex items-baseline justify-between mb-5">
                <h3 className="dim-title-sm text-xl text-vanilla">Cumulative regret</h3>
                <span className="text-xs text-faint">lower is better</span>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={chartData}>
                  <CartesianGrid stroke="rgba(30,50,80,0.12)" vertical={false} />
                  <XAxis dataKey="round" {...axis} tickLine={false} />
                  <YAxis {...axis} tickLine={false} width={40} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#4a6fa5' }} />
                  {POLICIES.map((p) => (
                    <Line
                      key={p.key}
                      type="monotone"
                      dataKey={`${p.key}_regret`}
                      name={p.label}
                      stroke={p.color}
                      strokeWidth={p.width}
                      dot={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </Panel>

            <Panel className="p-6">
              <div className="flex items-baseline justify-between mb-5">
                <h3 className="dim-title-sm text-xl text-vanilla">Cumulative reward</h3>
                <span className="text-xs text-faint">higher is better</span>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={chartData}>
                  <CartesianGrid stroke="rgba(30,50,80,0.12)" vertical={false} />
                  <XAxis dataKey="round" {...axis} tickLine={false} />
                  <YAxis {...axis} tickLine={false} width={40} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: '#4a6fa5' }} />
                  <Legend
                    wrapperStyle={{ fontSize: 12, color: '#4a5565', paddingTop: 12 }}
                    iconType="plainline"
                  />
                  {POLICIES.map((p) => (
                    <Line
                      key={p.key}
                      type="monotone"
                      dataKey={`${p.key}_reward`}
                      name={p.label}
                      stroke={p.color}
                      strokeWidth={p.width}
                      dot={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </Panel>
          </div>

          <Panel className="p-7">
            <h3 className="dim-title text-xl mb-6">Run summary</h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-widest text-gold/70 font-mono">
                  <th className="pb-3 font-normal">Policy</th>
                  <th className="pb-3 font-normal text-right">Reward</th>
                  <th className="pb-3 font-normal text-right">Regret</th>
                  <th className="pb-3 font-normal text-right">Engagement, CTR</th>
                </tr>
              </thead>
              <tbody>
                {POLICIES.map((p) => {
                  const s: any = (summary as any)?.[p.key] ?? {};
                  const best = s.total_cumulative_reward === bestReward;
                  return (
                    <tr key={p.key} className="border-t border-[color:var(--hairline)]">
                      <td className="py-3">
                        <span
                          className="inline-block w-6 h-[2px] align-middle mr-3"
                          style={{ background: p.color }}
                        />
                        <span className={best ? 'text-butter' : 'text-dim'}>{p.label}</span>
                      </td>
                      <td className={`py-3 num text-right ${best ? 'text-butter' : 'text-dim'}`}>
                        {s.total_cumulative_reward?.toFixed?.(2) ?? '—'}
                      </td>
                      <td className="py-3 num text-right text-dim">
                        {s.total_cumulative_regret?.toFixed?.(2) ?? '—'}
                      </td>
                      <td className="py-3 num text-right text-dim">
                        {s.final_avg_ctr != null ? `${(s.final_avg_ctr * 100).toFixed(1)}%` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        </>
      )}

      <Modal
        open={latexOpen}
        onClose={() => setLatexOpen(false)}
        title="Results table for the thesis report"
        wide
        footer={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setLatexOpen(false)}>Close</button>
            <button
              className="btn btn-gold btn-sm"
              onClick={async () => {
                await navigator.clipboard.writeText(latex());
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </>
        }
      >
        <pre className="num text-xs text-dim whitespace-pre-wrap leading-relaxed">{latex()}</pre>
      </Modal>
    </div>
  );
};
