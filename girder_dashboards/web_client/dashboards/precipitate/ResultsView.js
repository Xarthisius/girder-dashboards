/*
 * Renders a finished run: headline numbers, the plots, and the statistics tables.
 *
 * Everything here comes from the stored results.json — this view holds no state
 * of its own beyond which region is in scope, so re-opening an old run shows
 * exactly what the run produced.
 */
import template from '../../templates/precipitateResults.pug';

import { histogram, spacingMap } from './charts';
import { PRECISE as NARROW, extent, num } from './format';
import { sequentialBands } from './palette';

const View = girder.views.View;

/** Rows of the statistics table, in the order the original printed them. */
const STAT_ROWS = [
    { key: 'mean', label: 'Mean' },
    { key: 'std', label: 'Std dev' },
    { key: 'median', label: 'Median' },
    { key: 'min', label: 'Min' },
    { key: 'max', label: 'Max' },
    { key: 'sem', label: 'SEM' },
    { key: 'ci95', label: '95% CI (±)' }
];

var ResultsView = View.extend({
    events: {
        'change #g-precip-scope': function (event) {
            this.scope = event.currentTarget.value;
            this.render();
        }
    },

    /**
     * @param settings.results the parsed results.json
     * @param settings.resultUrl download URL for results.json, if stored
     * @param settings.folderUrl link to the run folder in Girder
     */
    initialize: function (settings) {
        this.results = settings.results;
        this.resultUrl = settings.resultUrl;
        this.folderUrl = settings.folderUrl;
        this.scope = 'pooled';
        this.charts = [];
    },

    /** The region in scope, or null when the pooled view is selected. */
    _region: function () {
        if (this.scope === 'pooled') {
            return null;
        }
        return this.results.regions[Number(this.scope)] || null;
    },

    /**
     * Particle arrays for the current scope.
     *
     * `nnIndex` is deliberately dropped when pooling: those indices are
     * region-local, so concatenating regions would make them point at the wrong
     * particles. Nothing in the pooled view needs them — the nearest-neighbour
     * links are drawn per region, over the image.
     */
    _particles: function () {
        const region = this._region();
        const regions = region ? [region] : this.results.regions;
        const merged = { x: [], y: [], diameterNm: [], spacingNm: [] };

        regions.forEach((each) => {
            const particles = each.particles;
            particles.x.forEach((x, index) => {
                merged.x.push(x);
                merged.y.push(particles.y[index]);
                merged.diameterNm.push(particles.diameterNm[index]);
                // A region holding a single particle has no neighbour, so its
                // spacing array is shorter than its particle list.
                const spacing = particles.spacingNm[index];
                merged.spacingNm.push(spacing === undefined ? NaN : spacing);
            });
        });
        return merged;
    },

    _stats: function () {
        const region = this._region();
        return region
            ? { diameter: region.diameter, spacing: region.spacing }
            : { diameter: this.results.pooled.diameter, spacing: this.results.pooled.spacing };
    },

    render: function () {
        this._destroyCharts();

        const results = this.results;
        const region = this._region();
        const stats = this._stats();
        const particles = this._particles();
        const spacings = particles.spacingNm.filter((value) => isFinite(value));
        const edgeToEdge = results.spacingMode === 'edge-to-edge';
        const spacingWord = edgeToEdge ? 'Edge-to-edge' : 'Centre-to-centre';

        const { min, max } = extent(spacings) || { min: 0, max: 1 };

        this.$el.html(
            template({
                tiles: [
                    {
                        value: num(stats.diameter.n),
                        unit: '',
                        label: 'Particles measured'
                    },
                    {
                        value: num(stats.diameter.n ? stats.diameter.mean.nm : null),
                        unit: 'nm',
                        label: 'Mean diameter, d'
                    },
                    {
                        value: num(stats.spacing.n ? stats.spacing.mean.nm : null),
                        unit: 'nm',
                        label: `Mean spacing, s (${spacingWord.toLowerCase()})`
                    },
                    {
                        value: num(results.scale.nmPerPx, NARROW),
                        unit: 'nm/px',
                        label: 'Scale'
                    }
                ],
                scopes: [
                    {
                        value: 'pooled',
                        label:
                            results.regions.length > 1
                                ? `All ${results.regions.length} regions (pooled)`
                                : results.regions[0].label,
                        selected: this.scope === 'pooled'
                    }
                ].concat(
                    results.regions.length > 1
                        ? results.regions.map((each, index) => ({
                            value: String(index),
                            label: `${each.label} (n = ${each.n})`,
                            selected: this.scope === String(index)
                        }))
                        : []
                ),
                spacingTitle: `${spacingWord} spacing distribution`,
                spacingHeader: `Spacing, s (${spacingWord.toLowerCase()})`,
                statsTitle: region ? `Statistics: ${region.label}` : 'Pooled statistics',
                statsNote: `n = ${stats.diameter.n} particles · CV of d ${num(
                    stats.diameter.cv,
                    NARROW
                )} · CV of s ${num(stats.spacing.cv, NARROW)}`,
                stats: {
                    rows: STAT_ROWS.map((row) => ({
                        label: row.label,
                        diameterPx: num((stats.diameter[row.key] || {}).px),
                        diameterNm: num((stats.diameter[row.key] || {}).nm),
                        spacingPx: num((stats.spacing[row.key] || {}).px),
                        spacingNm: num((stats.spacing[row.key] || {}).nm)
                    }))
                },
                regions: results.regions.map((each) => ({
                    label: each.label,
                    n: each.n,
                    diameter: each.n
                        ? `${num(each.diameter.mean.nm)} ± ${num(each.diameter.std.nm)}`
                        : '—',
                    spacing: each.spacing.n
                        ? `${num(each.spacing.mean.nm)} ± ${num(each.spacing.std.nm)}`
                        : '—',
                    area: `${each.bbox.width} × ${each.bbox.height}`
                })),
                colorbar: {
                    min: num(min),
                    max: num(max),
                    // Straight from the ramp the chart quantises with, so the
                    // legend cannot drift from the colours on the map.
                    bands: sequentialBands(min, max).map((band) => ({
                        color: band.color,
                        title: `${num(band.from)} – ${num(band.to)} nm`
                    }))
                },
                provenance: this._provenance(),
                resultUrl: this.resultUrl,
                folderUrl: this.folderUrl
            })
        );

        this._renderCharts(particles, stats);
        return this;
    },

    _provenance: function () {
        const results = this.results;
        const source = results.source || {};
        const parts = [];
        if (source.name) {
            parts.push(source.name);
        }
        parts.push(`${results.image.width} × ${results.image.height} px`);
        parts.push(`scale bar ${num(results.scale.barMicrons, NARROW)} µm = ${num(
            results.scale.barPixels
        )} px`);
        parts.push(`preset "${results.params.preset}"`);
        return parts.join(' · ');
    },

    _renderCharts: function (particles, stats) {
        if (stats.diameter.n) {
            this.charts.push(
                histogram(this.$('#g-precip-diameter-chart')[0], {
                    values: particles.diameterNm,
                    stats: stats.diameter,
                    xLabel: 'Equivalent diameter, d (nm)'
                })
            );
        }

        if (stats.spacing.n) {
            this.charts.push(
                histogram(this.$('#g-precip-spacing-chart')[0], {
                    values: particles.spacingNm.filter((value) => isFinite(value)),
                    stats: stats.spacing,
                    xLabel:
                        this.results.spacingMode === 'edge-to-edge'
                            ? 'Edge-to-edge spacing, s (nm)'
                            : 'Centre-to-centre distance, s (nm)'
                })
            );

            // Give the container the image's own aspect ratio (plus room for the
            // axes) so the map is not a stretched version of the micrograph.
            const width = this.results.image.width;
            const height = this.results.image.height;
            this.$('.g-precip-map-canvas').css({
                'max-width': `${Math.round(680 * Math.max(1, width / height))}px`,
                'aspect-ratio': `${width} / ${height}`,
                height: 'auto'
            });

            this.charts.push(
                spacingMap(this.$('#g-precip-map-chart')[0], {
                    particles: particles,
                    width: width,
                    height: height
                })
            );
        }
    },

    _destroyCharts: function () {
        this.charts.forEach((chart) => chart.destroy());
        this.charts = [];
    },

    destroy: function () {
        this._destroyCharts();
        View.prototype.destroy.apply(this, arguments);
    }
});

export default ResultsView;
