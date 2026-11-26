import type { Metadata } from "next";

import { LoginForm } from "@/components/login-form";
import { nextFromQuery } from "@/lib/auth";
import { isFixtureMode } from "@/lib/data";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getI18n();
  return { title: t.login.title };
}

/**
 * Standalone login page — rendered outside the sidebar shell (the middleware
 * marks it shell=0), one language per request like every other page.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { t } = await getI18n();
  const params = await searchParams;
  const next = nextFromQuery(params.next);
  return (
    <div className="as-login">
      <p className="as-login-brand">
        Alpha<span>Stock</span>
      </p>
      <LoginForm fixture={isFixtureMode()} next={next} labels={t.login} />
      <p className="as-login-footer">{t.footer.disclaimer}</p>
    </div>
  );
}
