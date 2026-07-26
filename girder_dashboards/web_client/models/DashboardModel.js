const AccessControlledModel = girder.models.AccessControlledModel;
const { restRequest } = girder.rest;

var DashboardModel = AccessControlledModel.extend({
    resourceName: 'dashboard',

    /**
     * Whether this dashboard can actually be run: its implementation must still
     * be installed and an admin must have enabled it.
     */
    isRunnable: function () {
        return !!this.get('available') && !!this.get('enabled');
    },

    /**
     * Restore the name, description, authors, image, icon and settings declared
     * by the plugin that registered this dashboard. Triggers `g:reset` when done.
     */
    resetToDefaults: function () {
        return restRequest({
            url: `${this.resourceName}/${this.id}/reset`,
            method: 'PUT',
            error: null
        }).done((resp) => {
            this.set(resp);
            this.trigger('g:reset', this);
        }).fail((err) => {
            this.trigger('g:error', err);
        });
    }
});

export default DashboardModel;
