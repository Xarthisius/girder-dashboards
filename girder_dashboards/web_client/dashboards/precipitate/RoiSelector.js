/*
 * The micrograph, the regions drawn on it, and the detection overlay.
 *
 * Regions are stored in *full-resolution image pixels*, which is the only frame
 * the server understands. The preview PNG is downscaled and then scaled again by
 * CSS, so rather than track those two factors separately, the overlay is an SVG
 * whose viewBox is the full-resolution image: marks are placed in image
 * coordinates and the browser does the scaling. Only pointer input has to be
 * converted, and that comes straight from the element's bounding rectangle.
 *
 * This view also draws the two panels of the original figure that belong on the
 * image itself: the detected-precipitate circles and the nearest-neighbour links.
 */
import { OVERLAY } from './palette';

const $ = girder.$;
const View = girder.views.View;

const SVG_NS = 'http://www.w3.org/2000/svg';

// Ignore click-sized drags: they are a misclick, not a region.
const MIN_DRAG_PX = 6;

//: The smallest region the server will analyse (see analysis._normalizeRegions).
const MIN_REGION_PX = 8;

function el(name, attrs) {
    const node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach((key) => node.setAttribute(key, attrs[key]));
    return node;
}

var RoiSelector = View.extend({
    events: {
        'mousedown .g-precip-stage': '_onDown',
        'dragstart .g-precip-stage': function (event) {
            // The <img> is draggable by default, which hijacks the rubber band.
            event.preventDefault();
        }
    },

    /**
     * @param settings.previewUrl URL of the preview PNG
     * @param settings.width full-resolution image width
     * @param settings.height full-resolution image height
     * @param settings.regions initial regions, in image pixels
     */
    initialize: function (settings) {
        this.previewUrl = settings.previewUrl;
        this.imageWidth = settings.width;
        this.imageHeight = settings.height;
        this.regions = (settings.regions || []).slice();
        this.overlay = { detections: true, links: false };
        this.results = null;
    },

    render: function () {
        this.$el.html(
            '<div class="g-precip-stage">' +
                `<img class="g-precip-image" alt="Micrograph preview" src="${this.previewUrl}">` +
                `<svg class="g-precip-overlay" viewBox="0 0 ${this.imageWidth} ${this.imageHeight}" ` +
                'preserveAspectRatio="none"></svg>' +
            '</div>'
        );
        this.svg = this.$('.g-precip-overlay')[0];

        // Stroke widths are derived from the image's rendered size, which is zero
        // until it loads — so anything drawn before then (the overlays of a
        // completed run being re-opened) has to be redrawn once it has.
        const image = this.$('.g-precip-image')[0];
        if (!image.complete) {
            image.addEventListener('load', () => this._draw(), { once: true });
        }

        this._draw();
        return this;
    },

    setRegions: function (regions) {
        this.regions = (regions || []).slice();
        this._draw();
        this.trigger('g:regionsChanged', this.regions);
        return this;
    },

    setResults: function (results) {
        this.results = results;
        this._draw();
        return this;
    },

    setOverlay: function (overlay) {
        Object.assign(this.overlay, overlay || {});
        this._draw();
        return this;
    },

    // -- pointer handling ------------------------------------------------

    /** Convert a pointer event to full-resolution image coordinates. */
    _toImage: function (event) {
        const rect = this.$('.g-precip-image')[0].getBoundingClientRect();
        return {
            x: ((event.clientX - rect.left) / rect.width) * this.imageWidth,
            y: ((event.clientY - rect.top) / rect.height) * this.imageHeight,
            rect: rect
        };
    },

    _onDown: function (event) {
        if (event.which !== 1) {
            return;
        }
        event.preventDefault();

        const start = this._toImage(event);
        this.pending = { x: start.x, y: start.y, width: 0, height: 0 };

        // Bound to the document, not the image, so a drag that leaves the image
        // still finishes — which is what happens when selecting a region at the
        // edge. Namespaced so teardown can remove them without holding references:
        // a handler left bound to the document outlives this view.
        const onMove = (moveEvent) => {
            const current = this._toImage(moveEvent);
            this.pending = {
                x: Math.min(start.x, current.x),
                y: Math.min(start.y, current.y),
                width: Math.abs(current.x - start.x),
                height: Math.abs(current.y - start.y)
            };
            this._draw();
        };

        const onUp = (upEvent) => {
            this._releaseDrag();

            const dragged = Math.max(
                Math.abs(upEvent.clientX - event.clientX),
                Math.abs(upEvent.clientY - event.clientY)
            );
            const pending = this.pending;
            this.pending = null;

            if (dragged < MIN_DRAG_PX || !pending) {
                this._draw();
                return;
            }

            const region = this._clamp(pending);
            if (region) {
                region.label = `ROI ${this.regions.length + 1}`;
                this.regions.push(region);
                this.trigger('g:regionsChanged', this.regions);
            }
            this._draw();
        };

        $(document)
            .on('mousemove.girderPrecipRoi', onMove)
            .on('mouseup.girderPrecipRoi', onUp);
    },

    _releaseDrag: function () {
        $(document).off('.girderPrecipRoi');
    },

    /**
     * Clip a dragged rectangle to the image, in image pixels.
     *
     * Both edges have to be clamped before the width is taken: a drag that ends
     * off the left of the image gives a negative `x`, and deriving the width from
     * that unclamped origin produced a region wider than the image — one that
     * disagreed with the box the user saw, since the SVG viewBox clips it.
     */
    _clamp: function (rect) {
        const x0 = Math.max(0, Math.min(this.imageWidth, rect.x));
        const y0 = Math.max(0, Math.min(this.imageHeight, rect.y));
        const x1 = Math.max(0, Math.min(this.imageWidth, rect.x + rect.width));
        const y1 = Math.max(0, Math.min(this.imageHeight, rect.y + rect.height));

        const region = {
            x: Math.round(x0),
            y: Math.round(y0),
            width: Math.round(x1 - x0),
            height: Math.round(y1 - y0)
        };
        // The server refuses anything under 8x8; rejecting it here keeps a region
        // that could never be analysed out of the list in the first place.
        return region.width >= MIN_REGION_PX && region.height >= MIN_REGION_PX
            ? region
            : null;
    },

    // -- drawing ---------------------------------------------------------

    _draw: function () {
        // The image element can be gone by the time this runs: a detached <img>
        // still fires `load` after a re-render replaced it, and a window resize can
        // arrive between renders.
        const image = this.$('.g-precip-image')[0];
        if (!this.svg || !image) {
            return;
        }
        while (this.svg.firstChild) {
            this.svg.removeChild(this.svg.firstChild);
        }

        // Stroke widths are in image units because of the viewBox, so they are
        // divided by the display scale to stay visually constant.
        const rect = image.getBoundingClientRect();
        const scale = rect.width ? this.imageWidth / rect.width : 1;

        this._drawResults(scale);
        this._drawRegions(scale);

        if (this.pending) {
            this.svg.appendChild(
                el('rect', {
                    x: this.pending.x,
                    y: this.pending.y,
                    width: this.pending.width,
                    height: this.pending.height,
                    fill: OVERLAY.region,
                    'fill-opacity': 0.12,
                    stroke: OVERLAY.region,
                    'stroke-width': 1.5 * scale,
                    'stroke-dasharray': `${5 * scale} ${4 * scale}`
                })
            );
        }
    },

    _drawRegions: function (scale) {
        this.regions.forEach((region, index) => {
            this.svg.appendChild(
                el('rect', {
                    x: region.x,
                    y: region.y,
                    width: region.width,
                    height: region.height,
                    fill: 'none',
                    stroke: OVERLAY.region,
                    'stroke-width': 2 * scale
                })
            );

            const label = el('text', {
                x: region.x + 4 * scale,
                y: region.y + 16 * scale,
                fill: '#ffffff',
                stroke: 'rgba(0, 0, 0, 0.65)',
                'stroke-width': 3 * scale,
                'paint-order': 'stroke',
                'font-size': 14 * scale,
                'font-family': 'system-ui, sans-serif'
            });
            label.textContent = region.label || `ROI ${index + 1}`;
            this.svg.appendChild(label);
        });
    },

    _drawResults: function (scale) {
        if (!this.results) {
            return;
        }

        this.results.regions.forEach((region) => {
            const particles = region.particles;

            if (this.overlay.links) {
                const path = [];
                particles.nnIndex.forEach((neighbour, index) => {
                    if (neighbour === undefined || particles.x[neighbour] === undefined) {
                        return;
                    }
                    path.push(
                        `M${particles.x[index]} ${particles.y[index]}` +
                            `L${particles.x[neighbour]} ${particles.y[neighbour]}`
                    );
                });
                if (path.length) {
                    this.svg.appendChild(
                        el('path', {
                            d: path.join(''),
                            fill: 'none',
                            stroke: OVERLAY.link,
                            'stroke-opacity': 0.75,
                            'stroke-width': Math.max(0.6, 1 * scale)
                        })
                    );
                }
            }

            if (this.overlay.detections) {
                particles.x.forEach((x, index) => {
                    this.svg.appendChild(
                        el('circle', {
                            cx: x,
                            cy: particles.y[index],
                            // Tiny precipitates would be invisible at true size,
                            // so the marker has a floor, as the original's did.
                            r: Math.max(2.2, particles.diameterPx[index] / 2),
                            fill: 'none',
                            stroke: OVERLAY.detection,
                            'stroke-opacity': 0.9,
                            'stroke-width': Math.max(0.5, 0.9 * scale)
                        })
                    );
                });
            }
        });
    },

    /** Redraw after a resize, so stroke widths track the new display scale. */
    refresh: function () {
        this._draw();
    },

    destroy: function () {
        // A drag in progress when this view goes away would otherwise leave its
        // handlers bound to the document forever.
        this._releaseDrag();
        View.prototype.destroy.apply(this, arguments);
    }
});

export default RoiSelector;
