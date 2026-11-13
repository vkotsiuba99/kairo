import React, { useState } from 'react';
import { SectionHead } from '../lib/ui';

/* Довідка: повний опис системи для захисту роботи та для щоденної роботи. */

const SECTIONS = [
  { id: 'about', label: 'What this system is' },
  { id: 'howto', label: 'How to use it' },
  { id: 'decision', label: 'Decision engine' },
  { id: 'reward', label: 'Reward and feedback' },
  { id: 'audience', label: 'Audience analysis' },
  { id: 'simulator', label: 'Simulator and baselines' },
  { id: 'generation', label: 'Text and image generation' },
  { id: 'platforms', label: 'Platforms and adapters' },
  { id: 'telemetry', label: 'Telemetry: what is actually collected' },
  { id: 'novelty', label: 'Scientific novelty' },
  { id: 'compare', label: 'Comparison with alternatives' },
  { id: 'api', label: 'API and stack' },
  { id: 'limits', label: 'Limitations and risks' },
  { id: 'glossary', label: 'Glossary' },
];

const Section: React.FC<{ id: string; title: string; children: React.ReactNode }> = ({
  id,
  title,
  children,
}) => (
  <section id={id} className="panel p-7 md:p-9 scroll-mt-32">
    <h3 className="dim-title text-2xl md:text-[1.7rem] mb-5">{title}</h3>
    <div className="space-y-4 text-[0.95rem] leading-relaxed text-dim">{children}</div>
  </section>
);

const Formula: React.FC<{ children: React.ReactNode; note?: string }> = ({ children, note }) => (
  <div className="convex p-5 my-2">
    <pre className="num text-[0.82rem] text-butter whitespace-pre-wrap leading-relaxed">{children}</pre>
    {note && <div className="text-xs text-faint mt-3">{note}</div>}
  </div>
);

const Table: React.FC<{ head: string[]; rows: (string | React.ReactNode)[][] }> = ({ head, rows }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm min-w-[34rem]">
      <thead>
        <tr className="text-left text-[0.7rem] uppercase tracking-widest text-gold/70 font-mono">
          {head.map((h) => (
            <th key={h} className="pb-3 pr-4 font-normal">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-[color:var(--hairline)] align-top">
            {r.map((c, j) => (
              <td key={j} className={`py-3 pr-4 ${j === 0 ? 'text-vanilla' : 'text-dim'}`}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const Term: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="num text-butter">{children}</span>
);

export const HelpTab: React.FC = () => {
  const [active, setActive] = useState('about');

  const go = (id: string) => {
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="space-y-10">
      <SectionHead
        eyebrow="help"
        title="How the system works and what every number means"
        note="A complete description of the subsystems, formulas, hyperparameters and workflows — for everyday use and for preparing the thesis defense."
      />

      <div className="grid gap-8 lg:grid-cols-[16rem_1fr]">
        <nav className="panel-quiet p-5 h-max lg:sticky lg:top-32">
          <div className="eyebrow mb-4">contents</div>
          <ul className="space-y-1.5">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => go(s.id)}
                  className={`text-left text-sm leading-snug transition-colors ${
                    active === s.id ? 'text-butter' : 'text-dim hover:text-vanilla'
                  }`}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="space-y-8 min-w-0">
          <Section id="about" title="What this system is">
            <p>
              Kairo is a software tool that decides on its own <em>what</em>, <em>how</em> and{' '}
              <em>when</em> to post on social media, writes and illustrates the post itself, and
              then learns from the audience’s reaction. The system is deliberately split into three
              independent subsystems rather than reduced to a single language model call:
            </p>
            <Table
              head={['Subsystem', 'What it does', 'Key technology']}
              rows={[
                [
                  'Data analysis',
                  'Collects telemetry from different networks into a single event model, builds follower interest vectors and identifies audience segments',
                  'Unified adapters, 768-dim embeddings, HDBSCAN, pgvector',
                ],
                [
                  'Decision making',
                  'Chooses the topic, format, tone, length, posting time and generation backend for a specific context',
                  'Factorized contextual bandit (LinUCB cascade)',
                ],
                [
                  'Generation',
                  'Writes text within the constraints of a specific platform and draws an image; for video it assembles a clip from the same outputs',
                  'Local language model, diffusion model, TTS + ffmpeg',
                ],
              ]}
            />
            <p>
              The entire application stack runs in Docker; language model inference runs on the host
              and is called as a regular OpenAI-compatible endpoint — this keeps the GPU available
              without passing it through to a container.
            </p>
          </Section>

          <Section id="howto" title="How to use it">
            <Table
              head={['Step', 'Where', 'What happens']}
              rows={[
                [
                  '1. Create a post',
                  'Studio',
                  'Describe a topic or leave the field empty. An empty field means the bandit picks the topic based on accumulated statistics. Then choose the network, length, tone and backend — and click “Create post”.',
                ],
                [
                  '2. Read and edit',
                  'Studio',
                  'The finished post is shown as a printed page. The “Edit” button switches the sheet into editing mode for the title, text and hashtags; “Regenerate” replaces the image using a new scene description.',
                ],
                [
                  '3. Publish',
                  'Studio or Overview',
                  'Publishing always requires a separate confirmation. The post goes to the selected channel through the platform adapter and is saved to the local archive at the same time.',
                ],
                [
                  '4. Collect feedback',
                  'Channels',
                  'Real reactions arrive from telemetry adapters; on the “Channels” tab you can send the same event manually to see how it turns into a reward.',
                ],
                [
                  '5. Validate the policy',
                  'Experiments',
                  'The simulator runs the bandit and three control strategies on identical data and plots regret and reward curves. Results can be exported as JSON and as a LaTeX table.',
                ],
              ]}
            />
            <p>
              Demo tip: if you don’t need to publish anywhere, choose the “Local archive”
              network — the post is saved to a file along with its metadata and image, and no
              external request is made.
            </p>
          </Section>

          <Section id="decision" title="Decision engine: factorized contextual bandit">
            <p>
              A publishing decision is not a single number but a combination of six dimensions. The
              action space is combinatorial, and a tabular bandit is unusable on it:
            </p>
            <Formula note="Each new topic or format multiplies the space rather than adding to it.">
{`topic (5) × format (4) × tone (4) × length (3) × time (4) × backend (2) = 1920 combinations`}
            </Formula>
            <p>
              Instead of enumerating every combination, the decision is broken down into a sequence of
              LinUCB heads. Each subsequent step sees both the context and the choices already made —
              this is what preserves the interdependence of the dimensions:
            </p>
            <Formula>
{`x₁ = [1, context]
x₂ = [x₁, one-hot(topic)]
x₃ = [x₂, one-hot(format)]
…
x₆ = [x₅, one-hot(time)]`}
            </Formula>
            <p>Each head keeps its own matrices for every arm and picks an arm by the upper confidence bound:</p>
            <Formula note="A — accumulated feature covariance, b — accumulated reward, α — confidence interval width.">
{`A_a = I + Σ x·xᵀ
b_a = Σ r·x
θ_a = A_a⁻¹ · b_a

choice: argmax_a ( xᵀθ_a + α · √(xᵀ A_a⁻¹ x) )`}
            </Formula>
            <p>
              The first term is the expected reward (exploitation), the second is the uncertainty of
              the estimate (exploration). An arm that has rarely been tried gets a larger bonus and
              will sooner or later be tested.
            </p>
            <Table
              head={['Parameter', 'Default value', 'What it changes']}
              rows={[
                ['α (alpha)', '0.8', 'Higher α means bolder tries of new options and slower convergence; lower means fast exploitation of the maximum found, at the risk of getting stuck.'],
                ['Seed', '42', 'Fixes all random variables of the simulation. The same seed always produces the same curves — a prerequisite for reproducibility.'],
                ['Rounds (N)', '300', 'How many posts each policy goes through. The bandit’s advantage over random choice becomes noticeable after a few dozen rounds.'],
              ]}
            />
            <p>
              An important implementation detail: a constant one (<Term>bias</Term>) is added to the
              feature vector. Without it, on a centered context the product <Term>θ·x</Term> has zero
              mean, the head cannot express “this arm is better on average”, and the policy
              degenerates into a random one.
            </p>
          </Section>

          <Section id="reward" title="Reward and feedback">
            <p>
              Audience reactions over the post-publication window are collapsed into a single number,
              which is used to update the bandit heads:
            </p>
            <Formula note="norm — normalization within a segment, so that large and small segments are comparable.">
{`reward = 0.15·norm(views)
       + 0.35·norm(reactions)
       + 0.25·norm(comments)
       + 0.15·norm(saves)
       + 0.10·norm(shares)
       − λ · generation_cost`}
            </Formula>
            <p>
              The weights are set so that actions costlier to the user weigh more: a comment takes
              effort, a view does not. The cost term makes the policy <em>cost-aware</em>: if the
              cloud backend yields an engagement gain smaller than its price, the bandit will start
              choosing the local model on its own.
            </p>
            <p>
              A telemetry event from any platform is normalized into a single{' '}
              <Term>telemetry_events</Term> schema (view, reaction, comment, save, share), linked to
              the publication by its external ID, and recalculates the reward for the corresponding
              policy decision.
            </p>
          </Section>

          <Section id="audience" title="Audience analysis">
            <p>
              Each follower is described by a 768-dimensional interest vector built by a local
              embedding model from the texts they interacted with. Vectors are stored in PostgreSQL
              via the pgvector extension, so similarity search is a regular database query, with no
              separate vector store.
            </p>
            <p>
              Segments are identified by <Term>HDBSCAN</Term> — an algorithm that determines the
              number of clusters by density on its own. This matters: the real number of audience
              groups is not known in advance, and setting it as a hyperparameter <Term>k</Term> would
              mean fitting the conclusion to an assumption. Points that do not belong to any dense
              cluster honestly remain noise rather than being assigned to the nearest cluster.
            </p>
            <p>
              On top of the clusters, RFM-like features are computed: how recently a person reacted,
              how often, and with which types of reactions. Together with the segment centroid they
              form the context vector that the bandit receives.
            </p>
          </Section>

          <Section id="simulator" title="Audience simulator and control strategies">
            <p>
              Before a policy reaches real followers, it goes through offline validation on a
              synthetic audience. Each virtual user has their own interest vector around the segment
              centroid and their own activity level; the probability of a reaction depends on how
              well the interests match the content:
            </p>
            <Formula>
{`p(reaction) = base_rate · activity
            + 0.65 · max(0, cos(content, interests))
            − fatigue_penalty

reaction delay   ~ Lognormal(μ = 2.1, σ = 0.9)   // median ≈ 8 min
fatigue_penalty  = 0.18 · (similarity − 0.5), if similarity > 0.5
fatigue window   = last 8 posts`}
            </Formula>
            <p>
              The fatigue model (<Term>novelty decay</Term>) is what sets the simulator apart from a
              naive random number generator: if a policy finds a winning topic and keeps repeating
              it, the audience gradually stops reacting. A policy that can only exploit loses out
              here.
            </p>
            <p>
              <Term>Regret</Term> is measured against an oracle — a hypothetical policy that tries
              every candidate at each step and sees the best possible outcome. It is an upper bound
              that cannot be reached in practice; what matters is not the absolute value but how much
              more slowly the bandit’s curve grows compared to the control strategies.
            </p>
            <Table
              head={['Strategy', 'Logic', 'What to expect from it']}
              rows={[
                ['Factorized LinUCB', 'The proposed method: a cascade of contextual heads with UCB exploration', 'The highest cumulative reward, sublinear regret growth'],
                ['Random', 'Uniform random choice in each dimension', 'Does not learn, but thanks to its variety it avoids audience fatigue — which is exactly why it is a fair baseline, not a straw man'],
                ['Greedy average', 'Picks the arm with the best historical average, with no exploration', 'Locks onto the first option that yields a non-zero reward and never tries anything else'],
                ['Fixed heuristic', 'A fixed set: popular topic, standard format, evening posting', 'Behaves like Buffer or Later: there is a schedule but no adaptation'],
              ]}
            />
          </Section>

          <Section id="generation" title="Text and image generation">
            <p>
              The parameters chosen by the bandit are inserted into a platform-specific prompt
              template. The model does not reply with free text but with a structure that follows the
              JSON schema{' '}
              <Term>{'{title, body, hashtags[], media_suggestion}'}</Term> — this eliminates a whole
              class of “the model returned the wrong format” errors.
            </p>
            <Table
              head={['Platform', 'Text limit', 'What the audience expects']}
              rows={[
                ['Telegram', '≈ 3800 characters', 'An in-depth long read with an image and takeaways'],
                ['Mastodon', '≈ 490 characters', 'A substantive status with a clear argument'],
                ['Bluesky', '≈ 290 graphemes', 'A short post with one insight'],
                ['YouTube', 'script up to 4500 characters', 'A hook in the first seconds, voice-over text, visual cues'],
                ['Local archive', 'up to 4000 characters', 'A full master copy with metadata'],
              ]}
            />
            <p>
              Generation runs strictly sequentially, with a single worker at <Term>concurrency=1</Term>.
              This is not a simplification but an architectural requirement: parallel requests to a
              single checkpoint in video memory either pile up in the scheduler queue with growing
              latency or run out of memory. Explicit serialization at the application level makes the
              behavior predictable.
            </p>
            <p>
              The image is drawn from a scene description to which the system automatically adds a
              signature style — a warm ruby-and-gold palette. If an image cannot be obtained, the last
              fallback draws a typographic poster in the same palette: a post will never have an
              empty frame or a technical placeholder.
            </p>
            <p>
              Emoji are not used in the interface or in the generated text. The ban is enforced
              twice: it is in the system prompt and is also applied as a deterministic cleanup of the
              output — relying on the model’s obedience alone is not enough here.
            </p>
          </Section>

          <Section id="platforms" title="Platforms and adapters">
            <p>
              All networks are connected through a single interface: a telemetry adapter and a
              publishing adapter. The core never knows where a post is going, so adding a platform
              means adding a class, not rewriting logic.
            </p>
            <Table
              head={['Platform', 'Authorization', 'Setup complexity']}
              rows={[
                ['Telegram', 'Bot token from @BotFather', 'Minimal, no OAuth'],
                ['Mastodon', 'App access token on the instance', 'Minimal'],
                ['Bluesky', 'App password (AT Protocol)', 'Minimal'],
                ['YouTube', 'OAuth 2.0 with the youtube.upload scope', 'Moderate, standard Google flow'],
                ['Local archive', 'Not required — just a path in the config', 'None'],
              ]}
            />
            <p>
              The Meta ecosystem is deliberately excluded: business account verification, an unstable
              Graph API and opaque moderation add risk that deterministic tests cannot be written
              against. Credentials are stored encrypted with AES-256-GCM; the key lives in environment
              variables, not in the database.
            </p>
          </Section>

          <Section id="telemetry" title="Telemetry: what is actually collected">
            <p>
              Feedback is not a promise but whatever a given API actually returns. Below is an honest
              picture for each platform; it determines which signals go into the reward.
            </p>
            <Table
              head={['Platform', 'Views', 'Reactions', 'Comments', 'Reposts', 'Who interacted']}
              rows={[
                ['Mastodon', 'not in API', 'yes', 'yes', 'yes', 'yes'],
                ['Bluesky', 'not in API', 'yes', 'yes', 'yes, with quotes', 'yes'],
                ['Telegram (Bot API)', 'not in API', 'totals only', 'only via a discussion group', 'no', 'no, channel reactions are anonymous'],
                ['YouTube', 'yes', 'yes', 'yes', '—', 'no'],
              ]}
            />
            <p>
              Because of this, the system collects full telemetry from <Term>Mastodon</Term> and{' '}
              <Term>Bluesky</Term>: each post’s counters are polled throughout the post-publication
              window, and the notification feed provides the authors of interactions — without them
              audience profiles cannot be built. From <Term>Telegram</Term> only the aggregate
              reaction count on our own posts is taken: views live in MTProto, not in the Bot API,
              and the bot cannot see them.
            </p>
            <p>
              Counters are stored as absolute values, and the worker computes the increment by
              comparing with what is already recorded. So a repeated poll does not double the
              telemetry, and a service restart needs no separate state.
            </p>
            <p>
              Since three of the four platforms provide no views, reward weights are normalized over
              the signals available on each specific platform: missing views do not penalize Mastodon
              and Bluesky relative to YouTube.
            </p>

            <h4 className="dim-title-sm text-lg text-vanilla pt-2">Why YouTube is disabled</h4>
            <p>
              Technically, YouTube is the richest telemetry source of all: Data API v3 returns views,
              likes and comments, and the Analytics API adds impressions, CTR and watch time. The
              limitation is not in the API but in generation: making video worth publishing requires
              a separate heavy video model and far more powerful hardware than this project assumes.
              The adapter stays in the system as a demonstration of extensibility: once suitable
              hardware is available, the channel can be enabled without changes to the core.
            </p>

            <h4 className="dim-title-sm text-lg text-vanilla pt-2">Why there is no Meta</h4>
            <p>
              Instagram, Facebook and Threads are excluded for organizational rather than technical
              reasons. Getting access to the Graph API requires business account verification and an
              app review, which depends on a third-party company’s decision and an undefined
              timeline. The second reason is unpredictable moderation: what passes today may be
              restricted tomorrow, even on neutral topics. Deterministic tests cannot be written for
              such a system and experiment reproducibility cannot be guaranteed, so Meta platforms
              are deliberately left out of scope.
            </p>
          </Section>

          <Section id="novelty" title="Scientific novelty">
            <p className="text-vanilla">
              For the first time, a method is proposed for adaptive selection of generated content
              parameters based on a factorized contextual multi-armed bandit — a sequence of LinUCB
              heads with a shared context that reduces a combinatorial action space to a sequence of
              low-dimensional decisions without losing the interdependence between dimensions.
            </p>
            <p className="text-vanilla">
              The method of offline policy validation has been improved through a synthetic audience
              model with controllable reaction-delay curves and a fatigue factor, making it possible
              to compare strategies quantitatively before applying them to real users.
            </p>
            <p className="text-vanilla">
              The application of the factorized bandit has been further developed by adding a
              generation-cost dimension: a cost-aware reward combines engagement optimization and
              efficient resource use in a single model.
            </p>
          </Section>

          <Section id="compare" title="Comparison with existing solutions">
            <Table
              head={['Criterion', 'Buffer / Later', 'Hootsuite', 'Ocoya / Predis', 'Kairo']}
              rows={[
                ['Scheduling', 'Manual schedule', 'Schedule + heuristics', 'Templates', 'Adaptive policy'],
                ['Parameter selection', 'Set by the user', 'Limited rules', 'Rigid templates', 'Contextual bandit with online learning'],
                ['Audience model', 'None', 'Basic demographics', 'None', 'HDBSCAN over the embedding space'],
                ['Local inference', 'None', 'None', 'None', 'Yes, two local models'],
                ['Offline validation', 'None', 'None', 'None', 'Simulator and three baselines'],
              ]}
            />
          </Section>

          <Section id="api" title="API and tech stack">
            <Table
              head={['Endpoint', 'Purpose']}
              rows={[
                ['POST /api/v1/content/generate', 'Bandit parameter selection and post generation'],
                ['PUT /api/v1/content/{id}', 'Edit the title, text and hashtags'],
                ['POST /api/v1/content/{id}/regenerate-image', 'Regenerate the image from a new description'],
                ['POST /api/v1/content/{id}/publish', 'Publish via the platform adapter'],
                ['POST /api/v1/telemetry/ingest', 'Ingest an event and recalculate the reward'],
                ['GET /api/v1/audience/segments', 'Audience segments and their centroids'],
                ['POST /api/v1/audience/cluster', 'Re-run HDBSCAN clustering'],
                ['POST /api/v1/experiments', 'Run a comparative policy simulation'],
                ['GET /api/v1/experiments/{id}/results', 'Reward and regret curves for the charts'],
                ['GET /api/v1/dashboard/metrics', 'System summary metrics'],
              ]}
            />
            <Table
              head={['Layer', 'Technology']}
              rows={[
                ['Backend', 'Python 3.12, FastAPI, SQLAlchemy (async)'],
                ['Storage', 'PostgreSQL 16 + pgvector'],
                ['Queues', 'Redpanda (Kafka API) for events, Redis + RQ for generation'],
                ['Frontend', 'React, TypeScript, Vite, Tailwind, Recharts'],
                ['ML', 'NumPy, SciPy, scikit-learn, HDBSCAN'],
                ['Deployment', 'Docker Compose; language model inference runs on the host'],
              ]}
            />
          </Section>

          <Section id="limits" title="Limitations and risks">
            <Table
              head={['Limitation', 'Why it exists', 'How it is addressed']}
              rows={[
                ['Policy cold start', 'At the start the bandit knows nothing about the audience and behaves almost randomly', 'The simulator is not an optional module but a mandatory warm-up stage before a real account'],
                ['Generation latency', 'A single GPU handles one request at a time', 'Deliberate queue serialization; generation time is shown to the user rather than hidden'],
                ['Video without real motion', 'A separate heavy video diffusion model is outside the system’s budget', 'The clip is assembled from voice-over and panning over key frames — a documented trade-off'],
                ['Dependence on an external image service', 'The diffusion pipeline needs heavy dependencies in the container', 'A multi-level fallback down to a local typographic poster; the system keeps working without a network'],
                ['Cloud backend', 'An external provider may change the model or the price', 'It is a fallback, not a dependency: without a key the system works fully, and the cost is part of the reward'],
              ]}
            />
          </Section>

          <Section id="glossary" title="Glossary">
            <Table
              head={['Term', 'Explanation']}
              rows={[
                ['Arm', 'One possible choice within a dimension: a specific topic, tone, time slot, etc.'],
                ['Context', 'The feature vector of the moment: segment centroid, platform, hour, day of week, rolling engagement statistics'],
                ['LinUCB', 'A linear reward model for each arm plus a confidence interval that drives exploration'],
                ['Regret', 'The difference between what an ideal policy would have achieved and what the actual policy achieved'],
                ['CTR', 'The share of views that turned into a reaction'],
                ['Embedding', 'A numeric vector that captures the meaning of a text or interests so that semantically similar items lie close together'],
                ['HDBSCAN', 'A density-based clustering algorithm: it determines the number of groups on its own and separates out noise'],
                ['Cost-aware reward', 'A reward discounted by the generation cost — so a more expensive backend is chosen only when it pays off'],
                ['Novelty decay', 'A drop in engagement from repeating similar content; in the simulator, a penalty for similarity to recent posts'],
              ]}
            />
          </Section>
        </div>
      </div>
    </div>
  );
};
