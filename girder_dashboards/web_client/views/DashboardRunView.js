import EditDashboardWidget from './EditDashboardWidget';
import { getDashboard } from '../registry';
import template from '../templates/dashboardRun.pug';

import '../stylesheets/dashboardRun.styl';

const $ = girder.$;
const View = girder.views.View;
const router = girder.router;
const { AccessType } = girder.constants;

/**
 * Chrome for a running dashboard.
 *
 * Rendered under `Layout.EMPTY`, so none of Girder's header, navigation or
 * footer is on screen: this view owns the whole viewport and provides the only
 * way back out via the "Dashboards" link in its own top bar.
 *
 * The dashboard itself comes from the client-side registry, keyed by the
 * document's `key`. A dashboard whose key has no registered view renders an
 * explanation instead — that happens when the server knows about a dashboard
 * whose JS bundle failed to load.
 */
var DashboardRunView = View.extend({
    events: {
        'click .g-dashboard-configure': function (event) {
            event.preventDefault();
            new EditDashboardWidget({
                el: $('#g-dialog-container'),
                model: this.model,
                parentView: this
            }).on('g:saved', this.render, this).render();
        },
        'click .g-dashboard-home': function (event) {
            event.preventDefault();
            router.navigate('', { trigger: true });
        }
    },

    initialize: function (settings) {
        this.model = settings.model;
    },

    render: function () {
        const registration = getDashboard(this.model.get('key'));

        let error = null;
        if (!registration) {
            error = {
                title: 'This dashboard is not installed',
                message: `Nothing on this page registered a dashboard named ` +
                    `"${this.model.get('key')}". Its plugin may be disabled or its ` +
                    `web client assets may have failed to load.`
            };
        } else if (!this.model.get('enabled')) {
            error = {
                title: 'This dashboard is disabled',
                message: 'An administrator has turned this dashboard off.'
            };
        }

        this.$el.html(template({
            dashboard: {
                name: this.model.get('name'),
                icon: this.model.get('icon') || 'icon-gauge'
            },
            canConfigure: this.model.get('_accessLevel') >= AccessType.ADMIN,
            error: error
        }));

        if (this.dashboardView) {
            this.dashboardView.destroy();
            this.dashboardView = null;
        }

        if (!error) {
            this.dashboardView = new registration.view({ // eslint-disable-line new-cap
                el: this.$('.g-dashboard-mount'),
                parentView: this,
                dashboard: this.model,
                settings: this.model.get('settings') || {}
            });
            this.dashboardView.render();
        }

        return this;
    }
});

export default DashboardRunView;
