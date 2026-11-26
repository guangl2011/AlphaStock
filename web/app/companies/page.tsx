import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { getCompanies } from "@/lib/data";
import { formatMarketCap } from "@/lib/format";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.companies };
}

/**
 * The covered-universe index — every tracked name links to its detail page
 * (thesis, signals, Alpha Score). A table, because the reader scans rows
 * against each other, not prose.
 */
export default async function CompaniesIndexPage() {
  const { t } = await getI18n();
  const { companies, fixture } = await getCompanies();

  return (
    <>
      <h1 className="as-page-title">{t.companies.title}</h1>
      <p className="as-page-sub">{t.companies.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      {companies.length === 0 ? (
        <EmptyState title={t.companies.emptyTitle} hint={t.companies.emptyHint} />
      ) : (
        <div className="as-table-wrap">
          <table className="as-table">
            <thead>
              <tr>
                <th scope="col">{t.companies.colTicker}</th>
                <th scope="col">{t.companies.colName}</th>
                <th scope="col">{t.companies.colCategory}</th>
                <th scope="col">{t.companies.colCap}</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((company) => (
                <tr key={company.ticker}>
                  <td>
                    <Link href={`/companies/${company.ticker}`} className="as-alert-ticker">
                      {company.ticker}
                    </Link>
                  </td>
                  <td>{company.name}</td>
                  <td>{t.universeCategories[company.category] ?? company.category}</td>
                  <td>{formatMarketCap(company.marketCap)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
