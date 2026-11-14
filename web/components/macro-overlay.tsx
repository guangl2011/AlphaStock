import type { Messages } from "@/lib/messages";
import type { Sensitivity } from "@/lib/types";

/**
 * Macro Overlay line — rate and USD sensitivity of a signal or view
 * (design doc §2 v2.0: cycle transparency). Qualitative always.
 */
export function MacroOverlayLine({
  rateSensitivity,
  usdSensitivity,
  t,
}: {
  rateSensitivity: Sensitivity | null;
  usdSensitivity: Sensitivity | null;
  t: Messages;
}) {
  if (!rateSensitivity && !usdSensitivity) return null;
  return (
    <p className="as-meta" style={{ margin: "0.3rem 0 0" }}>
      {t.macro(rateSensitivity ?? "—", usdSensitivity ?? "—")}
    </p>
  );
}
