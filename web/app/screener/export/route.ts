import { getScreenerData } from "@/lib/data";
import { getLocale, messagesFor } from "@/lib/i18n";
import {
  applyScreenerFilters,
  buildScreenerRows,
  parseScreenerFilters,
  sortScreenerRows,
  toScreenerCsv,
} from "@/lib/screener";

export const dynamic = "force-dynamic";

/**
 * CSV export for the current screener filter set (§7.2). The same pure
 * pipeline the page uses — parse → load → build → filter → sort — then the
 * dictionary-driven CSV, so the file matches the rendered table row for row.
 */
export async function GET(request: Request) {
  const filters = parseScreenerFilters(new URL(request.url).searchParams);
  const { companies, signals, alerts } = await getScreenerData();
  const results = sortScreenerRows(
    applyScreenerFilters(buildScreenerRows(companies, signals, alerts), filters),
  );

  const locale = await getLocale();
  const csv = toScreenerCsv(results, messagesFor(locale), locale);

  return new Response(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="alphastock-screener.csv"',
    },
  });
}
