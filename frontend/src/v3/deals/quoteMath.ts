import type { Quote, QuoteLine } from "./dealStore";

/** Decimal input to an exact rational, including JS's scientific notation. */
function ratio(value: number): [bigint, bigint] {
  if (!Number.isFinite(value) || value < 0) throw new Error("Quote values must be finite and non-negative.");
  const [mantissa, exponent = "0"] = String(value).toLowerCase().split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  const scale = fraction.length - Number(exponent);
  const digits = BigInt(whole + fraction);
  return scale < 0 ? [digits * 10n ** BigInt(-scale), 1n] : [digits, 10n ** BigInt(scale)];
}

const rounded = (numerator: bigint, denominator: bigint) =>
  (numerator * 2n + denominator) / (denominator * 2n);

function discount(cents: bigint, percent: number): bigint {
  const [n, d] = ratio(Math.min(100, Math.max(0, percent)));
  return rounded(cents * (100n * d - n), 100n * d);
}

function lineCents(line: QuoteLine): bigint {
  const [price, priceScale] = ratio(line.unitPrice);
  const priceCents = rounded(price * 100n, priceScale);
  const [quantity, quantityScale] = ratio(line.quantity);
  const [pct, pctScale] = ratio(line.discountPct);
  return rounded(
    priceCents * quantity * (100n * pctScale - pct),
    quantityScale * 100n * pctScale,
  );
}

function dollars(cents: bigint): number {
  if (cents < 0n || cents > 100_000_000_000_000n) throw new Error("Quote amount exceeds the supported limit.");
  return Number(cents) / 100;
}

export const lineTotal = (line: QuoteLine) => dollars(lineCents(line));

export function quoteTotals(quote: Pick<Quote, "lines" | "discountPct" | "taxPct" | "termMonths">) {
  let once = 0n;
  let monthly = 0n;
  for (const line of quote.lines) {
    if (line.billing === "monthly") monthly += lineCents(line);
    else once += lineCents(line);
  }
  const gross = once + monthly * BigInt(Math.max(1, quote.termMonths || 1));
  const subtotal = discount(gross, quote.discountPct);
  const [taxRate, taxScale] = ratio(quote.taxPct);
  const tax = rounded(subtotal * taxRate, 100n * taxScale);
  return {
    oneTime: dollars(discount(once, quote.discountPct)),
    monthly: dollars(discount(monthly, quote.discountPct)),
    discount: dollars(gross - subtotal),
    subtotal: dollars(subtotal),
    tax: dollars(tax),
    contractValue: dollars(subtotal + tax),
  };
}
