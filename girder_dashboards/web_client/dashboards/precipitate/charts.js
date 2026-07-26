/*
 * The dashboard's plots, built on Chart.js.
 *
 * Only the controllers, elements and scales actually used are registered, so the
 * tree-shaken build carries a fraction of the library.
 *
 * These replace three of the five matplotlib panels the research script drew:
 * the size histogram, the spacing histogram and the spacing heat map. The other
 * two — the detection overlay and the nearest-neighbour map — are drawn over the
 * micrograph itself by RoiSelector, where they belong.
 */
import {
    BarController,
    BarElement,
    Chart,
    LinearScale,
    PointElement,
    ScatterController,
    Title,
    Tooltip
} from 'chart.js';

import { INK, SERIES, sequentialColor } from './palette';
import { extent, fmt } from './format';

Chart.register(
    BarController,
    BarElement,
    ScatterController,
    PointElement,
    LinearScale,
    Title,
    Tooltip
);

Chart.defaults.font.family =
    'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif';
Chart.defaults.color = INK.muted;
Chart.defaults.animation = false;

/**
 * Draws the mean and median as vertical rules, the way the original figures did.
 *
 * A plugin rather than an extra dataset: a dataset would appear in tooltips and
 * shift the stacking, whereas this is annotation. The rules and their labels use
 * ink tokens, never the series colour.
 */
const referenceLines = {
    id: 'referenceLines',
    afterDatasetsDraw(chart, args, options) {
        const lines = (options && options.lines) || [];
        if (!lines.length) {
            return;
        }
        const { ctx, chartArea, scales } = chart;

        lines.forEach((line, index) => {
            const x = scales.x.getPixelForValue(line.value);
            if (!isFinite(x) || x < chartArea.left || x > chartArea.right) {
                return;
            }

            ctx.save();
            ctx.beginPath();
            ctx.setLineDash(line.dash || [5, 4]);
            ctx.lineWidth = 2;
            ctx.strokeStyle = line.color;
            ctx.moveTo(x, chartArea.top);
            ctx.lineTo(x, chartArea.bottom);
            ctx.stroke();

            // Labels stack so a mean and median that nearly coincide stay readable.
            ctx.setLineDash([]);
            ctx.font = '600 11px ' + Chart.defaults.font.family;
            ctx.textBaseline = 'top';
            const label = `${line.label} ${fmt(line.value)}`;
            const width = ctx.measureText(label).width;
            const flip = x + width + 10 > chartArea.right;
            const textX = flip ? x - width - 6 : x + 6;
            const textY = chartArea.top + 4 + index * 15;

            ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
            ctx.fillRect(textX - 2, textY - 1, width + 4, 13);
            ctx.fillStyle = line.color;
            ctx.fillText(label, textX, textY);
            ctx.restore();
        });
    }
};

Chart.register(referenceLines);

/**
 * Bin values into a histogram.
 *
 * The bin count follows the research script's rule (`max(10, n // 15)`) so a
 * given dataset is divided the same way it was in the published figures.
 */
export function histogramBins(values) {
    const finite = values.filter((value) => isFinite(value));
    const range = extent(finite);
    if (!range) {
        return { edges: [], counts: [], centres: [], width: 0 };
    }

    const { min, max } = range;
    const count = Math.max(10, Math.floor(finite.length / 15));
    // A single repeated value has no range to divide; give it one nominal bin.
    const width = max > min ? (max - min) / count : 1;

    const counts = new Array(count).fill(0);
    finite.forEach((value) => {
        const index = Math.min(count - 1, Math.floor((value - min) / width));
        counts[index] += 1;
    });

    const centres = counts.map((_, index) => min + (index + 0.5) * width);
    return { edges: [min, max], counts, centres, width };
}

function axis(title, extra) {
    return Object.assign(
        {
            type: 'linear',
            title: { display: true, text: title, color: INK.secondary },
            grid: { color: INK.grid, drawTicks: false },
            border: { color: INK.baseline },
            ticks: { color: INK.muted, padding: 6 }
        },
        extra || {}
    );
}

/**
 * A histogram with mean and median rules.
 *
 * @param canvas the canvas element to draw into
 * @param options.values raw per-particle values
 * @param options.stats the matching stats block, for the rules
 * @param options.xLabel axis title, including units
 */
export function histogram(canvas, options) {
    const bins = histogramBins(options.values);
    const lines = [];
    if (options.stats && options.stats.n) {
        lines.push({
            value: options.stats.mean.nm,
            label: 'Mean',
            color: INK.primary,
            dash: [5, 4]
        });
        lines.push({
            value: options.stats.median.nm,
            label: 'Median',
            color: INK.muted,
            dash: [2, 3]
        });
    }

    return new Chart(canvas, {
        type: 'bar',
        data: {
            datasets: [
                {
                    label: options.xLabel,
                    data: bins.centres.map((x, index) => ({ x, y: bins.counts[index] })),
                    backgroundColor: SERIES,
                    // A 2px surface gap between neighbouring bars, and rounded
                    // data-ends anchored to the baseline.
                    borderColor: INK.surface,
                    borderWidth: { top: 0, left: 1, right: 1, bottom: 0 },
                    borderRadius: 4,
                    borderSkipped: 'bottom',
                    barPercentage: 1,
                    categoryPercentage: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: axis(options.xLabel, { offset: false }),
                y: axis('Particle count', { beginAtZero: true, precision: 0 })
            },
            plugins: {
                // One series: the panel heading names it, so a legend box would
                // only repeat itself.
                legend: { display: false },
                referenceLines: { lines },
                tooltip: {
                    displayColors: false,
                    callbacks: {
                        title: (items) => {
                            const centre = items[0].parsed.x;
                            const half = bins.width / 2;
                            return `${fmt(centre - half)} – ${fmt(centre + half)}`;
                        },
                        label: (item) =>
                            `${item.parsed.y} particle${item.parsed.y === 1 ? '' : 's'}`
                    }
                }
            }
        }
    });
}

/**
 * The spacing map: every particle at its position in the image, coloured by its
 * nearest-neighbour spacing.
 *
 * Y is reversed, and both axes are pinned to the image's own bounds. The caller
 * must give the canvas's container the image's aspect ratio, otherwise the
 * particle arrangement is stretched and reads as a different microstructure than
 * the one on screen above it — Chart.js has no equal-scale option of its own.
 */
export function spacingMap(canvas, options) {
    const particles = options.particles;
    const spacings = particles.spacingNm;

    // A region holding a single particle has no neighbour, so that particle has no
    // spacing to colour it by. Plotting it anyway painted it mid-ramp — the colour
    // of a perfectly ordinary spacing — and its tooltip read "NaN nm".
    const points = particles.x
        .map((x, index) => ({
            x,
            y: particles.y[index],
            spacing: spacings[index],
            diameter: particles.diameterNm[index]
        }))
        .filter((point) => isFinite(point.spacing));

    const range = extent(points.map((point) => point.spacing)) || { min: 0, max: 1 };
    const { min, max } = range;

    const chart = new Chart(canvas, {
        type: 'scatter',
        data: {
            datasets: [
                {
                    label: 'Particles',
                    data: points,
                    parsing: false,
                    // 10px markers: the mark-spec floor is 8px, and these sit on
                    // a sparse field where a smaller dot is easy to miss.
                    pointRadius: 5,
                    pointHoverRadius: 7,
                    backgroundColor: points.map((point) =>
                        sequentialColor(point.spacing, min, max)
                    ),
                    // A 1px surface ring keeps overlapping markers separable.
                    borderColor: INK.surface,
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                // includeBounds: false keeps the ticks on round numbers; with it
                // on, the forced max label lands on top of the last round one.
                x: axis('x (pixels)', {
                    min: 0,
                    max: options.width,
                    ticks: { color: INK.muted, padding: 6, includeBounds: false }
                }),
                y: axis('y (pixels)', {
                    min: 0,
                    max: options.height,
                    reverse: true,
                    ticks: { color: INK.muted, padding: 6, includeBounds: false }
                })
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    displayColors: false,
                    callbacks: {
                        title: (items) =>
                            `x ${Math.round(items[0].raw.x)}, y ${Math.round(items[0].raw.y)}`,
                        label: (item) => [
                            `Spacing ${fmt(item.raw.spacing)} nm`,
                            `Diameter ${fmt(item.raw.diameter)} nm`
                        ]
                    }
                }
            }
        }
    });

    return chart;
}
