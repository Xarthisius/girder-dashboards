/**
 * Client-side registry mapping a dashboard key to its implementation.
 *
 * The server tells us which dashboards exist, are enabled, and are readable by
 * the current user; this registry supplies the view that actually renders one.
 * A dashboard is therefore only runnable when both halves agree on the key.
 *
 * Plugins register from their bundle's entry point:
 *
 *     girder.plugins.dashboards.registerDashboard('my-dashboard', {
 *         view: MyDashboardView
 *     });
 *
 * The view is instantiated with `{el, parentView, dashboard, settings}` where
 * `dashboard` is the DashboardModel and `settings` is its settings object.
 */

const _dashboards = {};

/**
 * Register a dashboard implementation.
 *
 * @param {string} key The dashboard key, matching the server-side registration.
 * @param {object} spec
 * @param {Backbone.View} spec.view View class rendering the dashboard.
 * @returns {object} The stored registration.
 */
function registerDashboard(key, spec) {
    if (!key) {
        throw new Error('A dashboard key is required.');
    }
    if (!spec || !spec.view) {
        throw new Error(`Dashboard "${key}" must be registered with a view.`);
    }
    _dashboards[key] = { ...spec, key };
    return _dashboards[key];
}

/** Forget a dashboard implementation. */
function unregisterDashboard(key) {
    delete _dashboards[key];
}

/** @returns {object|null} The registration for `key`, if any. */
function getDashboard(key) {
    return _dashboards[key] || null;
}

/** @returns {object[]} All registrations. */
function listDashboards() {
    return Object.keys(_dashboards).map((key) => _dashboards[key]);
}

export {
    getDashboard,
    listDashboards,
    registerDashboard,
    unregisterDashboard
};
