import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, CheckCheck, Radar, TriangleAlert, Users } from 'lucide-react';
import { Notice, Panel, SectionHead, Spinner, Stat } from '../lib/ui';
import {
  api,
  type DemandCatalog,
  type DemandPassport,
  type DemandProductDetail,
  type DemandSeries,
} from '../api/client';

/* Меню товарів формує разовий замір сигналу, а не користувач: сюди потрапляє
   лише те, що пройшло пороги. Тому картка завжди показує підставу — скільки
   дописів на добу і яка частка різних авторів. */

const VERDICT_TONE: Record<string, string> = {
  ok: 'chip-ok',
  thin: 'chip-thin',
};

function num(value: number, digits = 1): string {
  return value.toFixed(digits).replace('.0', '');
}

const ProductCard: React.FC<{
  item: DemandPassport;
  active: boolean;
  onPick: () => void;
}> = ({ item, active, onPick }) => (
  <button
    type="button"
    onClick={onPick}
    className={`convex convex-hover text-left px-5 py-4 w-full ${active ? 'ring-1 ring-[color:var(--color-gold)]' : ''}`}
  >
    <div className="flex items-start justify-between gap-3">
      <span className="font-display text-lg leading-tight text-vanilla">{item.name}</span>
      <span className={`chip ${VERDICT_TONE[item.verdict] ?? 'chip-thin'}`}>
        {item.verdict === 'ok' ? 'signal' : 'thin'}
      </span>
    </div>
    <div className="mt-3 flex items-center gap-4 text-xs font-mono text-dim">
      <span className="inline-flex items-center gap-1.5">
        <Activity size={13} className="text-gold" />
        {num(item.posts_per_day)}/day
      </span>
      <span className="inline-flex items-center gap-1.5">
        <Users size={13} className="text-gold" />
        {num(item.unique_author_ratio * 100, 0)}% authors
      </span>
      {item.mentions_collected > 0 && (
        <span className="text-faint">{item.mentions_collected} collected</span>
      )}
    </div>
  </button>
);

const PassportPanel: React.FC<{ detail: DemandProductDetail; series: DemandSeries | null }> = ({
  detail,
  series,
}) => {
  const p = detail.passport;
  const chart = useMemo(
    () => (series?.points ?? []).map((pt) => ({ ...pt, label: pt.day.slice(5) })),
    [series],
  );
  const totals = detail.corpus.reduce(
    (acc, row) => ({
      mentions: acc.mentions + row.mentions,
      bridged: acc.bridged + row.bridged,
      promo: acc.promo + row.promo,
    }),
    { mentions: 0, bridged: 0, promo: 0 },
  );

  return (
    <Panel className="p-7 mt-8">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <div className="eyebrow mb-1.5">{p.category_title}</div>
          <h3 className="dim-title text-3xl">{p.name}</h3>
        </div>
        <span className={`chip ${VERDICT_TONE[p.verdict] ?? 'chip-thin'}`}>{p.verdict_label}</span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Posts/day" value={num(p.posts_per_day)} note="from the measured sample" />
        <Stat
          label="Unique authors"
          value={`${num(p.unique_author_ratio * 100, 0)}%`}
          note="below 35% means inflated activity"
        />
        <Stat
          label="History depth"
          value={`${num(p.history_days, 0)} d`}
          note="available lookback"
        />
        <Stat label="Mentions collected" value={totals.mentions} note="currently in the corpus" />
      </div>

      {chart.length > 1 ? (
        <div className="mt-8">
          <div className="eyebrow mb-3">Daily mentions</div>
          <div className="panel-quiet px-3 pt-5 pb-2" style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chart} margin={{ top: 4, right: 10, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="demandFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-butter)" stopOpacity={0.55} />
                    <stop offset="100%" stopColor="var(--color-garnet)" stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(30,50,80,0.12)" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: 'var(--color-faint)', fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: 'rgba(30,50,80,0.25)' }}
                  minTickGap={28}
                />
                <YAxis
                  tick={{ fill: 'var(--color-faint)', fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    background: '#ffffff',
                    border: '1px solid rgba(30,50,80,0.25)',
                    borderRadius: 6,
                    color: 'var(--color-vanilla)',
                    fontSize: 12,
                  }}
                  labelStyle={{ color: 'var(--color-butter)' }}
                />
                <Area
                  type="monotone"
                  dataKey="mentions"
                  stroke="var(--color-butter)"
                  strokeWidth={1.6}
                  fill="url(#demandFill)"
                  name="mentions"
                />
                <Area
                  type="monotone"
                  dataKey="authors"
                  stroke="var(--color-brick)"
                  strokeWidth={1.2}
                  fill="none"
                  name="authors"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-faint mt-3 leading-relaxed">
            The series is trimmed to the window where collection is complete. Older days are
            under-sampled due to pagination; showing them would make the dip look like falling demand,
            when it is really a collection artifact.
          </p>
        </div>
      ) : (
        <Notice kind="info">
          No series collected yet. Run <code className="font-mono">backfill</code> for this
          product — without history there is nothing to forecast from.
        </Notice>
      )}

      <div className="mt-8">
        <div className="eyebrow mb-3">Corpus by source</div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[0.7rem] uppercase tracking-[0.14em] text-gold/80 font-mono">
                <th className="py-2 pr-4">platform</th>
                <th className="py-2 pr-4">source</th>
                <th className="py-2 pr-4 text-right">mentions</th>
                <th className="py-2 pr-4 text-right">authors</th>
                <th className="py-2 pr-4 text-right">bridged</th>
                <th className="py-2 text-right">promo</th>
              </tr>
            </thead>
            <tbody className="font-mono text-dim">
              {detail.corpus.map((row) => (
                <tr
                  key={`${row.platform}-${row.source}`}
                  className="border-t border-[color:var(--hairline)]"
                >
                  <td className="py-2 pr-4 text-vanilla">{row.platform}</td>
                  <td className="py-2 pr-4">{row.source}</td>
                  <td className="py-2 pr-4 text-right">{row.mentions}</td>
                  <td className="py-2 pr-4 text-right">{row.authors}</td>
                  <td className="py-2 pr-4 text-right">{row.bridged || '—'}</td>
                  <td className="py-2 text-right">{row.promo || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {totals.bridged > 0 && (
          <p className="text-xs text-faint mt-3 leading-relaxed">
            {totals.bridged} mentions came through the bridge between Bluesky and the fediverse — the same
            author and the same text on both platforms. They are counted once in cross-platform
            totals.
          </p>
        )}
      </div>
    </Panel>
  );
};

export const DemandTab: React.FC = () => {
  const [catalog, setCatalog] = useState<DemandCatalog | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [detail, setDetail] = useState<DemandProductDetail | null>(null);
  const [series, setSeries] = useState<DemandSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getDemandCatalog()
      .then((data) => {
        setCatalog(data);
        setCategory(data.categories[0]?.code ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const pick = useCallback(async (code: string) => {
    setPicked(code);
    setDetail(null);
    setSeries(null);
    try {
      const [d, s] = await Promise.all([
        api.getDemandProduct(code),
        api.getDemandSeries(code, 180),
      ]);
      setDetail(d);
      setSeries(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the product');
    }
  }, []);

  const visible = useMemo(() => {
    const all = catalog?.products ?? [];
    const needle = filter.trim().toLowerCase();
    return all.filter(
      (p) =>
        (needle ? p.name.toLowerCase().includes(needle) : p.category === category),
    );
  }, [catalog, category, filter]);

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-dim py-20 justify-center">
        <Spinner /> Loading catalog…
      </div>
    );
  }

  if (error) return <Notice kind="bad">{error}</Notice>;

  if (!catalog || catalog.total === 0) {
    return (
      <Notice kind="info">
        The catalog is empty. Run{' '}
        <code className="font-mono">scripts/build_demand_catalog.py</code> — it measures
        the candidates and keeps those with a signal in the menu.
      </Notice>
    );
  }

  return (
    <div>
      <SectionHead
        eyebrow="Demand forecasting"
        title="Product catalog"
        note={
          `${catalog.total} products passed the signal check on Bluesky and Mastodon. ` +
          'The menu is not edited by hand: a product without a confirmed signal does not make it here, ' +
          'because there is nothing to forecast it from.'
        }
        right={
          <div className="convex px-4 py-3 text-center">
            <div className="text-[0.66rem] uppercase tracking-[0.16em] text-gold/80 font-mono">
              in menu
            </div>
            <div className="num dim-title-sm text-3xl leading-none mt-1">{catalog.total}</div>
          </div>
        }
      />

      <div className="grid lg:grid-cols-[250px_1fr] gap-8">
        <aside className="lg:sticky lg:top-6 self-start">
          <Panel quiet className="p-3">
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter menu…"
              className="w-full bg-transparent border border-[color:var(--hairline)] rounded px-3 py-2 text-sm text-vanilla placeholder:text-faint focus:outline-none focus:border-[color:var(--hairline-strong)] mb-3"
            />
            <nav className="flex flex-col gap-0.5">
              {catalog.categories.map((c) => (
                <button
                  key={c.code}
                  type="button"
                  onClick={() => {
                    setCategory(c.code);
                    setFilter('');
                  }}
                  className={`flex items-center justify-between gap-2 px-3 py-2 rounded text-sm text-left transition-colors ${
                    !filter && category === c.code
                      ? 'bg-[color:var(--color-garnet)]/45 text-butter'
                      : 'text-dim hover:text-vanilla hover:bg-[color:var(--color-raise)]/50'
                  }`}
                >
                  <span>{c.title}</span>
                  <span className="font-mono text-xs text-faint">{c.count}</span>
                </button>
              ))}
            </nav>
          </Panel>

          <Panel quiet className="p-4 mt-4 text-xs text-faint leading-relaxed">
            <div className="flex items-center gap-2 text-gold mb-2">
              <Radar size={14} /> <span className="font-mono uppercase tracking-wider">thresholds</span>
            </div>
            <div className="flex items-start gap-2 mb-1.5">
              <CheckCheck size={13} className="mt-0.5 shrink-0" />
              15+ posts per day — full signal
            </div>
            <div className="flex items-start gap-2">
              <TriangleAlert size={13} className="mt-0.5 shrink-0" />
              under 35% unique authors — inflated activity, product excluded
            </div>
          </Panel>
        </aside>

        <div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {visible.map((item) => (
              <ProductCard
                key={item.code}
                item={item}
                active={picked === item.code}
                onPick={() => pick(item.code)}
              />
            ))}
          </div>
          {visible.length === 0 && (
            <Notice kind="info">No products with a confirmed signal in this category.</Notice>
          )}

          {picked && !detail && (
            <div className="flex items-center gap-3 text-dim py-10 justify-center">
              <Spinner /> Loading product profile…
            </div>
          )}
          {detail && <PassportPanel detail={detail} series={series} />}
        </div>
      </div>
    </div>
  );
};
