import type { Sensitivity } from "@/lib/types";

/**
 * Macro Overlay line — rate and USD sensitivity of a signal or view
 * (design doc §2 v2.0: cycle transparency). Qualitative always.
 */
export function MacroOverlayLine({
  rateSensitivity,
  usdSensitivity,
}: {
  rateSensitivity: Sensitivity | null;
  usdSensitivity: Sensitivity | null;
}) {
  if (!rateSensitivity && !usdSensitivity) return null;
  return (
    <p className="as-meta" style={{ margin: "0.3rem 0 0" }}>
      Macro overlay: rate {rateSensitivity ?? "n/a"} · USD {usdSensitivity ?? "n/a"}{" "}
      <span lang="zh-Hans">
        （宏观叠加：利率敏感度 {rateSensitivity ?? "—"} · 美元敏感度 {usdSensitivity ?? "—"}）
      </span>
    </p>
  );
}
