import template from '../templates/dataOverviewDashboard.pug';

import '../stylesheets/dataOverviewDashboard.styl';

const $ = girder.$;
const View = girder.views.View;
const { getCurrentUser } = girder.auth;
const { restRequest } = girder.rest;
const { formatDate, formatSize, DATE_DAY } = girder.misc;
const LoadingAnimation = girder.views.widgets.LoadingAnimation;

const UNKNOWN = '—';

// Which core list endpoints back each tile, and whether they need a session.
// `GET /user` is `@access.user`, so asking for it anonymously is a guaranteed
// 401 — that tile is dropped rather than fetched and shown as a dash.
const TILES = [
    { key: 'collections', url: 'collection', label: 'Collections', icon: 'icon-sitemap' },
    { key: 'users', url: 'user', label: 'Users', icon: 'icon-user', requiresUser: true },
    { key: 'groups', url: 'group', label: 'Groups', icon: 'icon-users' }
];

/**
 * A worked example of a dashboard: counts of the things in this instance plus a
 * table of the largest collections.
 *
 * It reads `settings.collectionLimit` — an admin can change that from the
 * dashboard's settings dialog without touching any code, which is the point of
 * the settings object.
 *
 * Everything is fetched with plain `restRequest` calls against core endpoints
 * and each one degrades independently: an unexpected failure shows as a dash
 * instead of taking down the whole page.
 */
var DataOverviewDashboard = View.extend({
    initialize: function (settings) {
        this.dashboard = settings.dashboard;
        this.settings = settings.settings || {};
        this.collectionLimit = Math.max(1, parseInt(this.settings.collectionLimit, 10) || 10);

        this.counts = {};
        this.collections = [];
        this.tiles = TILES.filter((tile) => !tile.requiresUser || getCurrentUser());
    },

    render: function () {
        const loading = new LoadingAnimation({ parentView: this }).render();
        this.$el.empty().append(loading.el);

        // `limit: 0` means "no limit" in Girder, so these lists are the whole
        // set the user is allowed to see and their length is the count.
        const requests = this.tiles.map((tile) => this._count(tile.url, tile.key));

        $.when(...requests).always(() => {
            restRequest({
                url: 'collection',
                method: 'GET',
                data: {
                    limit: this.collectionLimit,
                    sort: 'size',
                    sortdir: -1
                },
                error: null
            }).done((resp) => {
                this.collections = resp;
            }).always(() => {
                this._renderContent();
            });
        });

        return this;
    },

    _count: function (resource, key) {
        return restRequest({
            url: resource,
            method: 'GET',
            data: { limit: 0 },
            error: null
        }).done((resp) => {
            this.counts[key] = resp.length;
        }).fail(() => {
            this.counts[key] = null;
        });
    },

    _renderContent: function () {
        const format = (value) => (
            typeof value === 'number' ? value.toLocaleString() : UNKNOWN
        );

        this.$el.html(template({
            stats: this.tiles.map((tile) => ({
                label: tile.label,
                icon: tile.icon,
                value: format(this.counts[tile.key])
            })),
            collectionNote: `Top ${this.collectionLimit} by size`,
            collections: this.collections.map((collection) => ({
                id: collection._id,
                name: collection.name,
                description: collection.description,
                size: formatSize(collection.size || 0),
                created: collection.created ? formatDate(collection.created, DATE_DAY) : UNKNOWN
            }))
        }));

        return this;
    }
});

export default DataOverviewDashboard;
