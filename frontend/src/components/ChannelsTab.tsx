import React, { useEffect, useState } from 'react';
import { api, type Platform, type ConnectedAccount } from '../api/client';
import { Panel, SectionHead, Field, Spinner, Notice } from '../lib/ui';
import { ACCOUNT_STATUS, uk } from '../lib/labels';

// Поля форми беруться з config_schema платформи (бекенд), тут — лише підказки до них.
const FIELD_HINTS: Record<string, string> = {
  bot_token: 'issued by @BotFather; no OAuth needed',
  chat_id: 'channel username (@my_channel) or numeric id; the bot must be an admin',
  handle: 'e.g. user.bsky.social',
  app_password: 'created in your AT Protocol account settings',
  instance_url: 'e.g. https://mastodon.social',
  access_token: 'token of the app registered on the platform',
  client_id: 'Google Cloud OAuth client',
  client_secret: 'Google Cloud OAuth client secret',
  refresh_token: 'result of the OAuth flow with the youtube.upload scope',
  page_id: 'numeric Facebook Page ID',
  page_access_token: 'Page token with pages_manage_posts',
  ig_user_id: 'Instagram professional account ID',
  privacy_level: 'SELF_ONLY until the TikTok app passes the audit',
  archive_dir: 'path inside the container, e.g. data/archive',
};

type SchemaField = { name: string; required?: boolean; secret?: boolean; default?: string };

const fieldLabel = (name: string) =>
  name
    .split('_')
    .map((w, i) => (['id', 'url'].includes(w) ? w.toUpperCase() : i === 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');

const EVENTS = [
  { type: 'view', label: 'View', value: 1 },
  { type: 'reaction', label: 'Reaction', value: 1 },
  { type: 'comment', label: 'Comment', value: 1 },
  { type: 'share', label: 'Share', value: 1 },
];

export const ChannelsTab: React.FC = () => {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [loading, setLoading] = useState(true);

  const [platformId, setPlatformId] = useState<number | null>(null);
  const [externalId, setExternalId] = useState('');
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [connectMsg, setConnectMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [connecting, setConnecting] = useState(false);

  const [testPost, setTestPost] = useState('post_1');
  const [testPlatform, setTestPlatform] = useState('telegram');
  const [telemetryMsg, setTelemetryMsg] = useState<string | null>(null);

  const load = async () => {
    try {
      const [p, a] = await Promise.all([api.getPlatforms(), api.getConnectedAccounts()]);
      setPlatforms(Array.isArray(p) ? p : []);
      setAccounts(Array.isArray(a) ? a : []);
      if (Array.isArray(p) && p.length && platformId === null) setPlatformId(p[0].id);
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

  const selected = platforms.find((p) => p.id === platformId);
  const schemaFields: SchemaField[] = selected?.config_schema?.fields ?? [];

  const connect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!platformId || !externalId.trim()) return;
    const missing = schemaFields.filter((f) => f.required && !(creds[f.name] ?? '').trim());
    if (missing.length) {
      setConnectMsg({ ok: false, text: `Fill in: ${missing.map((f) => fieldLabel(f.name)).join(', ')}` });
      return;
    }
    const credentials = Object.fromEntries(
      schemaFields
        .map((f) => [f.name, (creds[f.name] ?? f.default ?? '').trim()])
        .filter(([, v]) => v),
    );
    setConnecting(true);
    setConnectMsg(null);
    try {
      await api.connectAccount(platformId, externalId.trim(), credentials);
      setConnectMsg({ ok: true, text: `Account ${externalId} connected.` });
      setExternalId('');
      setCreds({});
      await load();
    } catch (err: any) {
      setConnectMsg({ ok: false, text: err?.message || 'Failed to connect the account' });
    } finally {
      setConnecting(false);
    }
  };

  const sendEvent = async (type: string, value: number) => {
    setTelemetryMsg(null);
    try {
      await api.ingestTelemetry({
        platform_code: testPlatform,
        external_post_id: testPost,
        event_type: type,
        event_value: value,
      });
      setTelemetryMsg(`Event “${type}” received — the reward has been recalculated for the policy.`);
    } catch (e: any) {
      setTelemetryMsg(e?.message || 'Failed to send the event');
    }
  };

  return (
    <div className="space-y-12">
      <SectionHead
        eyebrow="platform-agnostic"
        title="Publishing channels"
        note="Each network is a separate implementation of a single adapter interface. The core never knows where a post is going: adding a new platform means adding a class, not rewriting logic."
      />

      {loading ? (
        <div className="text-dim flex items-center gap-3 py-10"><Spinner /> loading…</div>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {platforms.map((p) => {
            const linked = accounts.filter((a) => a.platform_id === p.id);
            return (
              <div key={p.id} className="convex convex-hover p-6">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="dim-title-sm text-xl text-vanilla">{p.display_name}</h3>
                  <span className={`text-xs ${linked.length ? 'text-butter' : 'text-faint'}`}>
                    {linked.length ? 'connected' : 'not connected'}
                  </span>
                </div>
                <div className="num text-xs text-faint mt-2">{p.code}</div>
                {linked.length > 0 && (
                  <ul className="mt-4 space-y-1.5">
                    {linked.map((a) => (
                      <li key={a.id} className="text-sm text-dim flex justify-between gap-3 border-b border-[color:var(--hairline)] pb-1.5">
                        <span className="truncate">{a.external_account_id}</span>
                        <span className="text-xs text-gold/80">{uk(ACCOUNT_STATUS, a.status)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-2">
        <Panel className="p-7">
          <h3 className="dim-title text-2xl mb-1">Connect an account</h3>
          <p className="text-sm text-faint mb-6">
            Credentials are encrypted with AES-256-GCM; the key lives in environment variables, not in the database.
          </p>
          <form className="space-y-5" onSubmit={connect}>
            <Field label="Network">
              <select
                className="field"
                value={platformId ?? ''}
                onChange={(e) => {
                  setPlatformId(Number(e.target.value));
                  setCreds({});
                }}
              >
                {platforms.map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="Channel ID" hint="e.g. @my_channel or user.bsky.social">
              <input
                className="field"
                value={externalId}
                onChange={(e) => setExternalId(e.target.value)}
                placeholder="@my_channel"
              />
            </Field>
            {schemaFields.map((f) => (
              <Field key={f.name} label={fieldLabel(f.name) + (f.required ? '' : ' (optional)')} hint={FIELD_HINTS[f.name]}>
                <input
                  type={f.secret ? 'password' : 'text'}
                  className="field"
                  value={creds[f.name] ?? ''}
                  onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value })}
                  placeholder={f.secret ? '••••••••••••' : f.default ?? ''}
                />
              </Field>
            ))}
            <button className="btn btn-primary btn-sm" type="submit" disabled={connecting}>
              {connecting ? <><Spinner /> Connecting…</> : 'Connect'}
            </button>
            {connectMsg && <Notice kind={connectMsg.ok ? 'good' : 'bad'}>{connectMsg.text}</Notice>}
          </form>
        </Panel>

        <Panel className="p-7">
          <h3 className="dim-title text-2xl mb-1">Feedback</h3>
          <p className="text-sm text-faint mb-6">
            Send an event manually to see how an audience reaction turns into a reward
            and updates the LinUCB heads.
          </p>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Network">
              <select className="field" value={testPlatform} onChange={(e) => setTestPlatform(e.target.value)}>
                {platforms.map((p) => (
                  <option key={p.id} value={p.code}>{p.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="Post ID">
              <input className="field num" value={testPost} onChange={(e) => setTestPost(e.target.value)} />
            </Field>
          </div>
          <div className="flex flex-wrap gap-2 mt-6">
            {EVENTS.map((e) => (
              <button
                key={e.type}
                className="btn btn-ghost btn-sm"
                onClick={() => sendEvent(e.type, e.value)}
              >
                {e.label}
              </button>
            ))}
          </div>
          {telemetryMsg && <div className="mt-5"><Notice>{telemetryMsg}</Notice></div>}
        </Panel>
      </div>
    </div>
  );
};
