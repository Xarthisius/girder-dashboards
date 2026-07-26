import template from '../templates/editDashboardWidget.pug';

import '../stylesheets/editDashboardWidget.styl';

const $ = girder.$;
const View = girder.views.View;
const { restRequest } = girder.rest;

/**
 * Modal dialog for a dashboard's global settings: its card metadata, the
 * free-form settings object handed to the dashboard at runtime, and whether it
 * is enabled. Requires ADMIN access on the dashboard, which the server enforces.
 */
var EditDashboardWidget = View.extend({
    events: {
        'submit #g-dashboard-edit-form': function (event) {
            event.preventDefault();
            this.$('.g-validation-failed-message').empty();

            const settingsText = this.$('#g-dashboard-settings').val().trim();
            let settings;
            try {
                settings = settingsText ? JSON.parse(settingsText) : {};
            } catch (e) {
                this.$('.g-validation-failed-message').text(`Settings must be valid JSON: ${e.message}`);
                return;
            }
            if (settings === null || typeof settings !== 'object' || Array.isArray(settings)) {
                this.$('.g-validation-failed-message').text('Settings must be a JSON object.');
                return;
            }

            // One name per line, since a name can contain a comma and being
            // credited under a mangled name is worse than typing a newline.
            const authors = this.$('#g-dashboard-authors').val()
                .split('\n')
                .map((author) => author.trim())
                .filter((author) => author);

            this.$('button.g-save-dashboard').girderEnable(false);
            this.save({
                name: this.$('#g-dashboard-name').val().trim(),
                description: this.$('#g-dashboard-description').val().trim(),
                authors: JSON.stringify(authors),
                image: this.$('#g-dashboard-image').val().trim(),
                icon: this.$('#g-dashboard-icon').val().trim(),
                enabled: this.$('#g-dashboard-enabled').is(':checked'),
                settings: JSON.stringify(settings)
            });
        },
        'click .g-dashboard-reset': function (event) {
            event.preventDefault();
            this.$('.g-validation-failed-message').empty();
            this.model.resetToDefaults()
                .done(() => {
                    this.trigger('g:saved', this.model);
                    this.$el.modal('hide');
                })
                .fail((err) => this._showError(err));
        }
    },

    initialize: function (settings) {
        this.model = settings.model;
    },

    render: function () {
        const modal = this.$el.html(template({
            dashboard: this.model,
            authors: (this.model.get('authors') || []).join('\n'),
            settings: JSON.stringify(this.model.get('settings') || {}, null, 2)
        })).girderModal(this);

        modal.trigger($.Event('ready.girder.modal', { relatedTarget: modal }));
        return this;
    },

    /**
     * PUT the changed fields. Uses restRequest rather than model.save() because
     * the settings object has to travel as a JSON-encoded form param, which is
     * what the endpoint's jsonParam expects.
     */
    save: function (fields) {
        restRequest({
            url: `dashboard/${this.model.id}`,
            method: 'PUT',
            data: fields,
            error: null
        }).done((resp) => {
            this.model.set(resp);
            this.trigger('g:saved', this.model);
            this.$el.modal('hide');
        }).fail((err) => this._showError(err));
    },

    _showError: function (err) {
        const message = (err.responseJSON && err.responseJSON.message) ||
            'Could not save the dashboard.';
        this.$('.g-validation-failed-message').text(message);
        this.$('button.g-save-dashboard').girderEnable(true);
    }
});

export default EditDashboardWidget;
