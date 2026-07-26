import DashboardCollection from '../collections/DashboardCollection';
import EditDashboardWidget from './EditDashboardWidget';
import template from '../templates/configView.pug';

import '../stylesheets/configView.styl';

const $ = girder.$;
const View = girder.views.View;
const events = girder.events;
const { restRequest } = girder.rest;
const AccessWidget = girder.views.widgets.AccessWidget;
const PluginConfigBreadcrumbWidget = girder.views.widgets.PluginConfigBreadcrumbWidget;

/**
 * Admin console page for the plugin: the one place where every dashboard known
 * to the server is listed, including the ones that are disabled or whose
 * implementation has gone away.
 */
var ConfigView = View.extend({
    events: {
        'change .g-dashboard-enabled-toggle': function (event) {
            const input = $(event.currentTarget);
            const enabled = input.is(':checked');
            const dashboard = this._dashboardFor(input);

            input.prop('disabled', true);
            restRequest({
                url: `dashboard/${dashboard.id}`,
                method: 'PUT',
                data: { enabled: enabled },
                error: null
            }).done((resp) => {
                dashboard.set(resp);
                input.prop('disabled', false);
                events.trigger('g:alert', {
                    icon: 'ok',
                    text: `${dashboard.get('name')} ${enabled ? 'enabled' : 'disabled'}.`,
                    type: 'success',
                    timeout: 3000
                });
            }).fail((err) => {
                input.prop('checked', !enabled).prop('disabled', false);
                this._showError(err);
            });
        },
        'click .g-dashboard-edit': function (event) {
            event.preventDefault();
            new EditDashboardWidget({
                el: $('#g-dialog-container'),
                model: this._dashboardFor($(event.currentTarget)),
                parentView: this
            }).on('g:saved', this.render, this).render();
        },
        'click .g-dashboard-access': function (event) {
            event.preventDefault();
            const dashboard = this._dashboardFor($(event.currentTarget));
            dashboard.fetchAccess(true).done(() => {
                new AccessWidget({
                    el: $('#g-dialog-container'),
                    model: dashboard,
                    modelType: 'dashboard',
                    hideRecurseOption: true,
                    parentView: this
                }).render();
            });
        },
        'click .g-dashboard-delete': function (event) {
            event.preventDefault();
            const dashboard = this._dashboardFor($(event.currentTarget));
            if (!window.confirm(`Remove the leftover "${dashboard.get('name')}" dashboard?`)) {
                return;
            }
            restRequest({
                url: `dashboard/${dashboard.id}`,
                method: 'DELETE',
                error: null
            }).done(() => {
                this.collection.remove(dashboard);
                this.render();
            }).fail((err) => this._showError(err));
        }
    },

    initialize: function () {
        this.collection = new DashboardCollection();
        this.collection.on('g:changed', this.render, this);
        // Unlike the gallery, the config page needs everything the server knows
        // about, including dashboards that are off or no longer implemented.
        this.collection.fetch({
            includeDisabled: true,
            includeUnavailable: true
        });
    },

    render: function () {
        this.$el.html(template({
            dashboards: this.collection.map((dashboard) => ({
                id: dashboard.id,
                key: dashboard.get('key'),
                name: dashboard.get('name'),
                description: dashboard.get('description'),
                authors: dashboard.get('authors') || [],
                image: dashboard.get('image'),
                icon: dashboard.get('icon') || 'icon-gauge',
                enabled: !!dashboard.get('enabled'),
                available: !!dashboard.get('available')
            }))
        }));

        this.breadcrumb = new PluginConfigBreadcrumbWidget({
            pluginName: 'Dashboards',
            el: this.$('.g-config-breadcrumb-container'),
            parentView: this
        }).render();

        return this;
    },

    _dashboardFor: function ($el) {
        return this.collection.get($el.closest('tr').data('id'));
    },

    _showError: function (err) {
        this.$('#g-dashboards-error-message').text(
            (err.responseJSON && err.responseJSON.message) ||
            'The dashboard could not be updated.'
        );
    }
});

export default ConfigView;
