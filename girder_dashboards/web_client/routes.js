import ConfigView from './views/ConfigView';
import DashboardListView from './views/DashboardListView';
import DashboardModel from './models/DashboardModel';
import DashboardRunView from './views/DashboardRunView';

const router = girder.router;
const events = girder.events;
const { Layout } = girder.constants;
const { exposePluginConfig } = girder.utilities.PluginUtils;

exposePluginConfig('dashboards', 'plugins/dashboards/config');

router.route('plugins/dashboards/config', 'dashboardsConfig', function () {
    events.trigger('g:navigateTo', ConfigView);
});

router.route('dashboards', 'dashboards', function () {
    events.trigger('g:navigateTo', DashboardListView);
    events.trigger('g:highlightItem', 'DashboardsView');
});

// A running dashboard owns the whole window: Layout.EMPTY drops Girder's header,
// global navigation and footer, and DashboardRunView supplies its own top bar
// with the way back.
router.route('dashboard/:id', 'dashboard', function (id) {
    const dashboard = new DashboardModel({ _id: id });
    dashboard.fetch().done(() => {
        events.trigger('g:navigateTo', DashboardRunView, {
            model: dashboard
        }, {
            layout: Layout.EMPTY,
            renderNow: true
        });
    }).fail(() => {
        router.navigate('dashboards', { trigger: true, replace: true });
    });
});
