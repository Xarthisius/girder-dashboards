/*
 * Colour and ink tokens for the precipitate charts.
 *
 * Chart.js takes colours as strings, not CSS variables, so the values live here
 * rather than in the stylesheet. Girder's chrome is light-only, so there is one
 * mode; the surface these were validated against is Girder's white panel
 * (#ffffff), not a generic light grey.
 *
 * Every chart in this dashboard draws ONE series, so no categorical palette is in
 * play: magnitude is the only thing colour encodes, and it always uses the same
 * single blue hue, light -> dark. Vertical rules and labels stay in ink tokens so
 * text never wears a series colour.
 */

// The one data hue. Histogram bars and the darkest end of the sequential ramp.
export const SERIES = '#2a78d6';

/*
 * Sequential blue, light -> dark, for spacing magnitude. Starts at step 250
 * rather than 100: these are 8px markers on white, and the two lightest steps of
 * the full ramp would recede into the surface.
 */
export const SEQUENTIAL = [
    '#86b6ef',
    '#5598e7',
    '#3987e5',
    '#2a78d6',
    '#256abf',
    '#184f95',
    '#0d366b'
];

export const INK = {
    surface: '#ffffff',
    primary: '#0b0b0b',
    secondary: '#52514e',
    muted: '#898781',
    grid: '#e1e0d9',
    baseline: '#c3c2b7'
};

// Overlay marks sit on a grey micrograph, not on the chart surface, so they are
// picked for legibility against mid-grey rather than against white.
export const OVERLAY = {
    detection: '#3987e5',
    link: '#eda100',
    region: '#e34948',
    /*
     * The info panel being left out of the analysis. A neutral scrim rather than
     * a fourth hue, because "excluded" is the absence of a category, not another
     * one — and because whatever is under it still has to be readable, which is
     * the entire reason it is dimmed instead of cropped.
     */
    excluded: '#14120d',
    excludedEdge: '#f2f0e9',
    /*
     * The scale bar that was measured. A hue of its own, since with the exclusion
     * switched off it can share the panel with detection circles and links —
     * which is exactly the comparison a user makes when deciding whether to
     * exclude it. Form separates it further: it is the only rectangle outline
     * that is not a region, and regions cannot be drawn here.
     */
    scaleBar: '#a374e8'
};

/**
 * Map a value to a step of the sequential ramp.
 *
 * Quantising rather than interpolating is deliberate: it makes the colour bar a
 * legend with readable boundaries instead of a gradient the eye has to estimate
 * against.
 */
export function sequentialColor(value, min, max) {
    if (!isFinite(value) || max <= min) {
        return SEQUENTIAL[Math.floor(SEQUENTIAL.length / 2)];
    }
    const fraction = (value - min) / (max - min);
    const index = Math.min(
        SEQUENTIAL.length - 1,
        Math.max(0, Math.floor(fraction * SEQUENTIAL.length))
    );
    return SEQUENTIAL[index];
}

/** Bin boundaries for the colour bar, in the same order as `SEQUENTIAL`. */
export function sequentialBands(min, max) {
    const width = (max - min) / SEQUENTIAL.length;
    return SEQUENTIAL.map((color, index) => ({
        color,
        from: min + index * width,
        to: min + (index + 1) * width
    }));
}
