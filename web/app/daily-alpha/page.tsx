import { FixtureNotice } from "@/components/fixture-notice";
import { DailyAlphaDigest } from "@/components/daily-alpha-digest";
import { getDailyAlpha } from "@/lib/daily-alpha";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.dailyAlpha };
}

export default async function DailyAlphaPage() {
  const { t, locale } = await getI18n();
  const { brief, fixture } = getDailyAlpha();

  return (
    <>
      <DailyAlphaDigest brief={brief} t={t} locale={locale} now={new Date()} />
      {fixture ? <FixtureNotice t={t} /> : null}
    </>
  );
}
