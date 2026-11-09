import Link from "next/link";

const SURFACES = [
  {
    href: "/feed",
    title: "Signal Feed",
    zh: "信号流",
    blurb: "Bilingual stream of verified signals — filter by theme, category, strength.",
  },
  {
    href: "/themes",
    title: "Theme Heat",
    zh: "主题热度",
    blurb: "Seven themes from AI power generation to Physical AI, ranked by signal heat.",
  },
  {
    href: "/track-record",
    title: "Track Record",
    zh: "公开业绩记录",
    blurb: "Hash-attested publication history — every signal, including losers.",
  },
  {
    href: "/alerts",
    title: "Weekly Alerts",
    zh: "每周警报",
    blurb: "Monday briefs for the ten highest-conviction names, bilingual.",
  },
] as const;

export default function Home() {
  return (
    <>
      <section className="as-hero">
        <h1>See the AI energy build-out before it shows up in earnings.</h1>
        <p className="as-hero-zh" lang="zh-Hans">
          在业绩兑现之前，看见 AI 能源建设进度。
        </p>
        <p>
          AlphaStock tracks interconnection queues, data-center buildout permits,
          and SEC filings, then publishes verified, source-linked signals on
          U.S.-listed AI-energy and Physical-AI equities — in English and Chinese.
        </p>
      </section>

      <div className="as-hero-card-grid">
        {SURFACES.map(({ href, title, zh, blurb }) => (
          <Link key={href} href={href} className="as-card">
            <h2 className="as-card-title">
              {title} · <span lang="zh-Hans">{zh}</span>
            </h2>
            <p className="as-meta">{blurb}</p>
          </Link>
        ))}
      </div>

      <p className="as-meta" style={{ marginTop: "1.5rem" }}>
        Company pages live under <code>/companies/&lt;ticker&gt;</code> — e.g.{" "}
        <Link href="/companies/VST">/companies/VST</Link>. Free-tier display
        rules apply everywhere: qualitative scenario language only, source links
        and invalidation conditions always visible.
      </p>
    </>
  );
}
