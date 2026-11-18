import { FixtureNotice } from "@/components/fixture-notice";
import { getSentimentData } from "@/lib/data";
import { getI18n } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import {
  SENTIMENT_PLATFORMS,
  confidenceBand,
  sentimentBand,
  volumeDelta,
  type SentimentPlatform,
  type SentimentPostRow,
  type SentimentRunRow,
} from "@/lib/sentiment";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.sentiment };
}

type SearchParams = Record<string, string | string[] | undefined>;

/** First `?topic=` value; null when absent. */
function parseTopicParam(params: SearchParams): string | null {
  const raw = params.topic;
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value || null;
}

/** The selected topic's run row; an unknown id falls back to the first topic. */
function resolveSelected(params: SearchParams, runs: SentimentRunRow[]): SentimentRunRow {
  const requested = parseTopicParam(params);
  return runs.find((run) => run.topicId === requested) ?? runs[0];
}

/**
 * Center-anchored score gauge: a bar grows right from center for positive
 * scores, left for negative — the -1..+1 range reads as signed, not absolute.
 * The numeric score itself never renders (free-tier display rules); the band
 * label carries the meaning.
 */
function ScoreGauge({ run, t }: { run: SentimentRunRow; t: Messages }) {
  const band = sentimentBand(run.topicScore);
  const width = Math.min(Math.abs(run.topicScore), 1) * 50;
  return (
    <div
      className="as-heat-bar"
      role="img"
      aria-label={`${t.sentiment.score}: ${t.sentiment.sentimentBand[band]}`}
    >
      <span
        style={{
          width: `${width}%`,
          marginLeft: run.topicScore >= 0 ? "50%" : `${50 - width}%`,
        }}
      />
    </div>
  );
}

/** Platform chips, only the platforms the topic actually drew posts from. */
function PlatformChips({ run, t }: { run: SentimentRunRow; t: Messages }) {
  const active = SENTIMENT_PLATFORMS.filter((p) => run.platformCounts[p] > 0);
  if (active.length === 0) return null;
  return (
    <p className="as-meta" style={{ margin: "0.35rem 0 0" }}>
      {active.map((platform) => (
        <span key={platform} className="as-platform-chip">
          {t.sentiment.platformNames[platform as SentimentPlatform]} {run.platformCounts[platform]}
        </span>
      ))}
    </p>
  );
}

/** One sourced feedback row: platform, snippet, date, link, qualitative reading. */
function FeedbackRow({ post, t }: { post: SentimentPostRow; t: Messages }) {
  return (
    <li className="as-feedback-row">
      <p className="as-meta" style={{ margin: 0 }}>
        {t.sentiment.platformNames[post.platform]} · {post.author}
        {post.postDate ? ` · ${post.postDate}` : ""} ·{" "}
        {t.sentiment.postReads[sentimentBand(post.rawScore)]}
      </p>
      <p style={{ margin: "0.2rem 0" }}>{post.snippet}</p>
      <a href={post.url} target="_blank" rel="noopener noreferrer">
        {t.sentiment.viewSource}
      </a>
    </li>
  );
}

export default async function SentimentPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const { locale, t } = await getI18n();
  const params = (await searchParams) ?? {};
  const { data, fixture } = await getSentimentData();
  const selected = resolveSelected(params, data.runs);
  const feedback = data.posts.filter((post) => post.topicId === selected.topicId);

  return (
    <>
      <h1 className="as-page-title">{t.sentiment.title}</h1>
      <p className="as-page-sub">{t.sentiment.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      <section aria-labelledby="sentiment-explainer">
        <h2 id="sentiment-explainer" className="as-section-title">
          {t.sentiment.explainerTitle}
        </h2>
        <p>{t.sentiment.explainer}</p>
        <p className="as-meta" style={{ margin: "0.3rem 0" }}>
          {t.sentiment.coverage}
        </p>
        <p className="as-meta" style={{ margin: 0 }}>
          {t.sentiment.asOf(data.latestAsOf)}
        </p>
      </section>

      <div className="as-heat-grid">
        {data.runs.map((run) => {
          const delta = volumeDelta(run, data.previousCounts);
          const band = confidenceBand(run.confidence);
          const isSelected = run.topicId === selected.topicId;
          return (
            <a
              key={run.topicId}
              href={`/sentiment?topic=${encodeURIComponent(run.topicId)}`}
              className={isSelected ? "as-card as-heat-card is-selected" : "as-card as-heat-card"}
              aria-current={isSelected ? "true" : undefined}
            >
              <h2 className="as-card-title">{locale === "zh" ? run.labelZh : run.labelEn}</h2>
              <ScoreGauge run={run} t={t} />
              <p style={{ margin: "0.5rem 0 0.2rem" }}>
                <strong>{t.sentiment.sentimentBand[sentimentBand(run.topicScore)]}</strong> ·{" "}
                <span className="as-meta">{t.sentiment.posts(run.postCount)}</span>
              </p>
              <p className="as-meta" style={{ margin: 0 }}>
                {delta === null
                  ? t.sentiment.firstScan
                  : t.sentiment.volumeDelta(delta)}
                {" · "}
                {t.sentiment.confidence}: {t.sentiment.confidenceBand[band]}
              </p>
              <PlatformChips run={run} t={t} />
            </a>
          );
        })}
      </div>

      <section aria-labelledby="sentiment-feedback">
        <h2 id="sentiment-feedback" className="as-section-title">
          {t.sentiment.feedbackTitle(locale === "zh" ? selected.labelZh : selected.labelEn)}
        </h2>
        {feedback.length === 0 ? (
          <p className="as-meta">{t.sentiment.emptyTopic}</p>
        ) : (
          <ul className="as-feedback-list" style={{ listStyle: "none", padding: 0 }}>
            {feedback.map((post, index) => (
              <FeedbackRow key={`${post.url}-${index}`} post={post} t={t} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
