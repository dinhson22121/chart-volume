// Prices span many orders of magnitude (VND stock prices in the thousands
// down to sub-cent crypto tokens like 0.00000000000135) -- a fixed decimal
// count either rounds a micro-cap coin down to "0.00" or, once precision is
// stretched to cover it, produces unreadably long strings. Below a threshold
// of leading zeros we switch to the compact "0.0<n>xxx" notation used by
// Binance/CoinGecko, where the subscript digit is the count of zeros right
// after the decimal point (e.g. 0.00000000000135 -> "0.0₂12135").
const SUBSCRIPT_DIGITS = ["₀", "₁", "₂", "₃", "₄", "₅", "₆", "₇", "₈", "₉"];
const COMPACT_ZERO_THRESHOLD = 4; // leading zeros before switching to compact notation
const SIGNIFICANT_DIGITS = 3;

function toSubscript(n: number): string {
  return String(n)
    .split("")
    .map((d) => SUBSCRIPT_DIGITS[Number(d)])
    .join("");
}

// Count of zeros immediately after "0." before the first significant digit
// (e.g. 0.05 -> 1, 0.00000000000135 -> 12). Only meaningful for 0 < abs < 1.
function leadingZeroCount(abs: number): number {
  return -Math.floor(Math.log10(abs)) - 1;
}

export function formatPrice(value: number): string {
  const abs = Math.abs(value);
  if (!Number.isFinite(abs) || abs === 0) return "0.00";
  if (abs >= 1) return value.toFixed(2);

  const zeros = leadingZeroCount(abs);
  if (zeros < COMPACT_ZERO_THRESHOLD) {
    return value.toFixed(Math.min(10, zeros + 1 + SIGNIFICANT_DIGITS));
  }

  const sign = value < 0 ? "-" : "";
  const digits = abs
    .toExponential(SIGNIFICANT_DIGITS - 1)
    .split("e")[0]
    .replace(".", "");
  return `${sign}0.0${toSubscript(zeros)}${digits}`;
}

// lightweight-charts' custom price formatter still requires a numeric
// minMove (used for crosshair snapping) even though the display string is
// fully custom -- derive one small enough not to clip the value's own
// precision instead of the library's 0.01 default.
export function priceMinMove(value: number): number {
  const abs = Math.abs(value);
  if (!Number.isFinite(abs) || abs === 0 || abs >= 1) return 0.01;
  const zeros = leadingZeroCount(abs);
  return 10 ** -Math.min(10, zeros + 1 + SIGNIFICANT_DIGITS);
}
