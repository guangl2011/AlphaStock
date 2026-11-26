import type { PublicAlert } from "./types";

export interface WeekGroup {
  alertDate: string;
  alerts: PublicAlert[];
}

/** Group the alert history into one row per published week, newest first. */
export function groupByWeek(alerts: PublicAlert[]): WeekGroup[] {
  const weeks = new Map<string, PublicAlert[]>();
  for (const alert of alerts) {
    const bucket = weeks.get(alert.alertDate);
    if (bucket) {
      bucket.push(alert);
    } else {
      weeks.set(alert.alertDate, [alert]);
    }
  }
  return [...weeks.entries()]
    .map(([alertDate, grouped]) => ({ alertDate, alerts: grouped }))
    .sort((a, b) => (a.alertDate < b.alertDate ? 1 : -1));
}
