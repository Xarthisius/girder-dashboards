import DashboardCollection from '../collections/DashboardCollection';
import EditDashboardWidget from './EditDashboardWidget';
import template from '../templates/dashboardList.pug';

import '../stylesheets/dashboardList.styl';

const $ = girder.$;
const View = girder.views.View;
const { AccessType } = girder.constants;
const { getCurrentUser } = girder.auth;
const { cancelRestRequests } = girder.rest;
const LoadingAnimation = girder.views.widgets.LoadingAnimation;

/**
 * The dashboard gallery: one card per dashboard the current user may open.
 *
 * The server only returns enabled dashboards the user can read, so every card
 * here is one they are allowed to run — the per-card checks below decide between
 * "Open" and an explanation, they don't do the access control.
 */
var DashboardListView = View.extend({
    events: {
        'click .g-dashboard-configure': function (event) {
            event.preventDefault();
            const id = $(event.currentTarget).data('id');
            this.editDashboard(this.collection.get(id));
        }
    },

    initialize: function () {
        cancelRestRequests('fetch');

        this.collection = new DashboardCollection();
        this.collection.on('g:changed', this.render, this);
        this.collection.fetch();

        this.loadingAnimation = new LoadingAnimation({ parentView: this }).render();
        this.$el.html(this.loadingAnimation.el);
    },

    render: function () {
        const currentUser = getCurrentUser();

        this.$el.html(template({
            isAdmin: !!(currentUser && currentUser.get('admin')),
            dashboards: this.collection.map((dashboard) => ({
                id: dashboard.id,
                name: dashboard.get('name'),
                description: dashboard.get('description'),
                authors: dashboard.get('authors') || [],
                image: dashboard.get('image'),
                icon: dashboard.get('icon') || 'icon-gauge',
                canRun: dashboard.isRunnable(),
                canConfigure: dashboard.get('_accessLevel') >= AccessType.ADMIN
            }))
        }));

        return this;
    },

    editDashboard: function (dashboard) {
        if (!dashboard) {
            return;
        }
        new EditDashboardWidget({
            el: $('#g-dialog-container'),
            model: dashboard,
            parentView: this
        }).on('g:saved', this.render, this).render();
    }
});

export default DashboardListView;
