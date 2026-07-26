import './routes';

import * as collections from './collections';
import * as models from './models';
import * as views from './views';
import DataOverviewDashboard from './dashboards/DataOverviewDashboard';
import PrecipitateDashboard from './dashboards/PrecipitateDashboard';
import {
    getDashboard,
    listDashboards,
    registerDashboard,
    unregisterDashboard
} from './registry';

const { registerPluginNamespace } = girder.pluginUtils;
const { wrap } = girder.utilities.PluginUtils;
const GlobalNavView = girder.views.layout.GlobalNavView;

/**
 * Add "Dashboards" to the left sidebar.
 *
 * Extending `defaultNavItems` before the view first renders — rather than
 * appending an <li> after the fact — means the entry participates in the normal
 * active-link highlighting and survives the re-renders that login/logout trigger.
 */
wrap(GlobalNavView, 'initialize', function (initialize, settings) {
    initialize.call(this, settings);

    if (this.defaultNavItems) {
        this.defaultNavItems.push({
            name: 'Dashboards',
            icon: 'icon-gauge',
            target: 'dashboards'
        });
    }
});

registerDashboard('data-overview', { view: DataOverviewDashboard });
registerDashboard('precipitate-analysis', { view: PrecipitateDashboard });

registerPluginNamespace('dashboards', {
    collections,
    models,
    views,
    getDashboard,
    listDashboards,
    registerDashboard,
    unregisterDashboard
});
