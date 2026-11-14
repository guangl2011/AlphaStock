import Link from "next/link";

import { getI18n } from "@/lib/i18n";

export default async function NotFound() {
  const { t } = await getI18n();
  return (
    <div style={{ padding: "3rem 0", textAlign: "center" }}>
      <h1 className="as-page-title">{t.notFound.title}</h1>
      <p className="as-page-sub">{t.notFound.sub}</p>
      <p>
        <Link href="/feed">{t.notFound.back}</Link>
      </p>
    </div>
  );
}
