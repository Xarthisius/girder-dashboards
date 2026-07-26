/*
 * Number formatting for the precipitate dashboard.
 *
 * One place, because the same figures appear in the tiles, the tables, the chart
 * tooltips and the run history — and "22.85" in one and "22.8" in another reads as
 * two different measurements.
 */

const TWO_DP = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const PRECISE = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 });

//: What a missing or non-finite value looks like. Never "NaN", never "undefined".
export const UNKNOWN = '—';

/** Format a measurement, or `UNKNOWN` when there isn't one. */
export function num(value, formatter) {
    if (value === null || value === undefined || !isFinite(value)) {
        return UNKNOWN;
    }
    return (formatter || TWO_DP).format(value);
}

/** Format a value that is known to be finite, for axis and tooltip text. */
export function fmt(value) {
    return TWO_DP.format(value);
}

/**
 * Smallest and largest finite value in an array.
 *
 * A loop, not `Math.min(...values)`: spreading an array past roughly 10^5 elements
 * overflows the argument stack with a RangeError, and a large micrograph at the
 * fine preset can produce that many particles.
 */
export function extent(values) {
    let min = Infinity;
    let max = -Infinity;
    for (let index = 0; index < values.length; index += 1) {
        const value = values[index];
        if (!isFinite(value)) {
            continue;
        }
        if (value < min) {
            min = value;
        }
        if (value > max) {
            max = value;
        }
    }
    return isFinite(min) ? { min, max } : null;
}

export { PRECISE, TWO_DP };
