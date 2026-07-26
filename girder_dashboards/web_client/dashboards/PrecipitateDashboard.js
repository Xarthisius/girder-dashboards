/*
 * Precipitate Analysis dashboard.
 *
 * Drives one analysis run from end to end: upload a micrograph, give it a scale,
 * pick regions of interest, watch the job, read the numbers. Nothing is computed
 * here — every number on screen comes from the results.json the backend stored in
 * the user's own folder, and the image is the preview the backend rendered.
 *
 * The view re-renders on discrete events only (a run loaded, a job finished, the
 * run list changed). Job progress patches the DOM in place instead, because a
 * full re-render would tear down the region selector and the form while the user
 * is looking at them.
 */
import ResultsView from './precipitate/ResultsView';
import RoiSelector from './precipitate/RoiSelector';
import template from '../templates/precipitateDashboard.pug';

import '../stylesheets/precipitateDashboard.styl';

const $ = girder.$;
const View = girder.views.View;
const { getCurrentToken, getCurrentUser } = girder.auth;
const { getApiRoot, restRequest } = girder.rest;
const FolderModel = girder.models.FolderModel;
const UploadWidget = girder.views.widgets.UploadWidget;
const eventStream = girder.utilities.eventStream;

/*
 * girder_jobs/constants.py::JobStatus. Only these three are terminal — the worker
 * plugin adds statuses in the 820s (FETCHING_INPUT, CONVERTING_INPUT, ...) which
 * are numerically larger but still mean "running", so this has to be a set rather
 * than a "finished when >= 3" comparison.
 */
const JOB_SUCCESS = 3;
const JOB_ERROR = 4;
const JOB_CANCELED = 5;
const JOB_FINISHED = [JOB_SUCCESS, JOB_ERROR, JOB_CANCELED];

const JOB_LABELS = {
    0: 'Queued',
    1: 'Queued',
    2: 'Running',
    3: 'Complete',
    4: 'Failed',
    5: 'Canceled'
};

// Job progress is pushed over the notification socket; this poll is the fallback
// for when that socket is not connected (or a notification is missed).
const POLL_INTERVAL_MS = 1500;

const STATUS_LABELS = {
    new: 'No image',
    preparing: 'Preparing',
    ready: 'Ready to run',
    analyzing: 'Running',
    complete: 'Complete',
    failed: 'Failed'
};

var PrecipitateDashboard = View.extend({
    events: {
        'click #g-precip-run': function () {
            // The button is disabled while work is in flight, but a click can
            // still land in the instant before that reaches the DOM.
            if (this._busy()) {
                return;
            }
            this._readForm();
            this._analyze();
        },
        'click .g-precip-reset': function () {
            this._openRun(null);
        },
        'click .g-precip-login': function () {
            girder.events.trigger('g:loginUi');
        },
        'click .g-precip-region-clear': function () {
            this.regions = [];
            this._syncRegions();
        },
        'click .g-precip-region-remove': function (event) {
            const index = Number($(event.currentTarget).data('index'));
            this.regions.splice(index, 1);
            this._syncRegions();
        },
        'click .g-precip-open-run': function (event) {
            event.preventDefault();
            this._openRun($(event.currentTarget).data('id'));
        },
        'click .g-precip-delete-run': function (event) {
            this._deleteRun($(event.currentTarget).data('id'));
        },
        'change #g-precip-show-detections': function (event) {
            this.overlay.detections = event.currentTarget.checked;
            if (this.roiSelector) {
                this.roiSelector.setOverlay(this.overlay);
            }
        },
        'change #g-precip-show-links': function (event) {
            this.overlay.links = event.currentTarget.checked;
            if (this.roiSelector) {
                this.roiSelector.setOverlay(this.overlay);
            }
        },
        /*
         * Keep `this.form` in step with the DOM on every keystroke, not just on
         * blur. A re-render rebuilds the inputs from `this.form`, and one is
         * triggered by the first region drag — whose `preventDefault()` stops the
         * number input from ever blurring. Reading only on `change` therefore
         * silently reverted a half-typed scale and analysed at the old value.
         */
        'input #g-precip-scale-microns': '_readForm',
        'input #g-precip-scale-pixels': '_readForm',
        'change #g-precip-scale-microns': '_readForm',
        'change #g-precip-scale-pixels': '_readForm',
        'change #g-precip-preset': '_readForm',
        'change input[name="g-precip-spacing"]': '_readForm',
        'click .g-precip-scale-restore': '_useDetectedScale',
        /*
         * Changing the exclusion moves the boundary of what gets analysed, so it
         * has to reach the image and the region list — but through a patch, not a
         * re-render, for the same reason the scale inputs do.
         */
        'change #g-precip-exclude-panel': '_onExclusionChanged',
        'input #g-precip-exclude-px': '_onExclusionChanged',
        'change #g-precip-exclude-px': '_onExclusionChanged'
    },

    initialize: function (settings) {
        this.dashboard = settings.dashboard;
        this.settings = settings.settings || {};

        this.capability = null;
        this.runs = [];
        this.run = null;
        this.results = null;
        this.job = null;
        // A step that has been asked for but has no job document yet. Scheduling
        // is itself a round trip, and until it comes back `job` is null — which
        // used to leave the Run button live and the upload widget offering to
        // replace the micrograph that is being prepared.
        this.pending = null;
        this.jobError = null;
        this.regions = [];
        this.overlay = { detections: true, links: false };
        // What the backend read out of the image file itself: its pixel scale and
        // its instrument info panel. Findings, not choices — they prefill the
        // form and are then the user's to overrule.
        this.detected = {};

        this.form = {
            scaleBarMicrons: this.settings.defaultScaleBarMicrons || 1.0,
            scaleBarPixels: this.settings.defaultScaleBarPixels || 129,
            edgeToEdge: !!this.settings.defaultEdgeToEdge,
            preset: this.settings.defaultPreset || 'fine',
            excludePanel: false,
            excludeBottomPx: 0
        };

        this.listenTo(eventStream, 'g:event.job_status', this._onJobEvent);
        // The card is public, so this view can be reached before signing in; a
        // login from the runner's own navbar has to bring the dashboard to life.
        this.listenTo(girder.events, 'g:login', function () {
            this.capability = null;
            this.render();
        });
    },

    render: function () {
        // Every endpoint here is @access.user, and a run is stored in the user's
        // own folder, so there is nothing this dashboard can do anonymously.
        // Saying so beats letting each request 401.
        if (!getCurrentUser()) {
            this._destroySubViews();
            // The runner has no Girder header to sign in from, so the banner has
            // to offer the login dialog itself. #g-dialog-container is outside the
            // body container, so the dialog still works in the empty layout.
            this.$el.html(
                '<div class="g-precip"><div class="g-precip-banner g-precip-banner-warning">' +
                    '<i class="icon-lock"></i><div><strong>Sign in to use this dashboard</strong>' +
                    '<div>It stores the micrographs you upload, and the results it computes ' +
                    'from them, in a folder of your own.</div>' +
                    '<button class="btn btn-sm btn-primary g-precip-login" type="button">' +
                    '<i class="icon-login"></i> Sign in</button>' +
                    '</div></div></div>'
            );
            return this;
        }

        if (!this.capability) {
            // Sub-views first: a re-login clears `capability`, and Chart.js
            // instances left behind here would keep their canvases and resize
            // listeners alive after the HTML under them is replaced.
            this._destroySubViews();
            this.$el.html(
                '<div class="g-precip-loading"><i class="icon-spin4 animate-spin"></i> Loading…</div>'
            );
            this._loadCapability();
            return this;
        }

        this._destroySubViews();

        const state = this.run ? this.run.state || {} : {};
        const image = state.image || {};
        const hasPreview = !!state.previewFileId;
        const micrograph = this._micrographContext();

        this.$el.html(
            template({
                banner: this._banner(),
                run: this.run,
                micrograph: micrograph,
                hasPreview: hasPreview,
                hasResults: !!this.results,
                overlay: this.overlay,
                form: Object.assign({}, this.form, { derived: this._derivedScale() }),
                scaleSource: this._scaleSource(),
                panel: hasPreview ? this._panelContext() : null,
                presets: (this.capability.presets || []).map((preset) => ({
                    key: preset.key,
                    label: preset.label,
                    selected: preset.key === this.form.preset
                })),
                regions: this.regions.map((region, index) => ({
                    index: index,
                    label: region.label || `ROI ${index + 1}`,
                    box: `${region.width} × ${region.height} px at ${region.x}, ${region.y}`
                })),
                canRun: this._canRun(),
                computeNote: this._computeNote(),
                uploadHint: `A TIFF micrograph. It is stored in "${this.capability.workspace}" in your own Girder space, along with everything the analysis produces.`,
                placeholder:
                    'Upload a micrograph to see it here and select regions of interest on it.',
                stageTitle: image.width
                    ? `${state.inputName || 'Micrograph'} — ${image.width} × ${image.height} px`
                    : state.inputName || 'Micrograph',
                job: this._jobContext(),
                runs: this._runContext(),
                historyNote: `Stored in ${this.capability.workspace}`
            })
        );

        if (!micrograph) {
            this._renderUpload();
        }
        if (hasPreview) {
            this._renderRoiSelector(image);
        }
        if (this.results) {
            this._renderResults();
        }
        this._lockControls();

        return this;
    },

    // -- busy state ------------------------------------------------------

    /**
     * Is a step of this run in flight?
     *
     * `job` alone is not enough: it only exists once the request that scheduled
     * the step has come back, and that request is a round trip the user can
     * click straight through.
     */
    _busy: function () {
        return !!(this.job || this.pending);
    },

    _canRun: function () {
        const state = (this.run && this.run.state) || {};
        return !!state.previewFileId && !this._busy();
    },

    /**
     * Apply the busy state to the controls, in place.
     *
     * Called from `render()` so a fresh DOM starts out right, and directly when
     * a step is scheduled so the lock lands on the click rather than on the
     * response. Everything it touches describes *what will be run*, so leaving
     * any of it live during a run means the form on screen stops matching the
     * numbers that come back.
     */
    _lockControls: function () {
        const busy = this._busy();
        // The upload widget is left out: it owns its own start button, which is
        // disabled until a file has been chosen, and re-enabling it here would
        // offer an upload with nothing to upload.
        this.$('.g-precip-controls')
            .find('input, select, button')
            .filter((index, el) => !$(el).closest('.g-precip-upload-mount').length)
            .girderEnable(!busy);
        this.$('#g-precip-run').girderEnable(this._canRun());
        // Step 1 says so itself, with the file name and a spinner.
        this.$('.g-precip-step').slice(1).toggleClass('g-precip-step-busy', busy);
        if (this.roiSelector) {
            this.roiSelector.setLocked(busy);
        }
    },

    /**
     * Step 1's state: the file this run is working on, or nothing — in which
     * case the caller renders the upload widget.
     *
     * `run.state.inputName` cannot decide this on its own. The client's copy of
     * the run predates the prepare request that sets it, so for the whole of the
     * prepare job it still reads "no file" — and the upload widget came back,
     * looking ready and *being* ready: choosing a file there creates a second
     * run and orphans the one being prepared.
     */
    _micrographContext: function () {
        const state = (this.run && this.run.state) || {};
        const step = this.job || this.pending;
        if (step) {
            return {
                name: state.inputName || step.name || 'Micrograph',
                icon: 'icon-spin4 animate-spin',
                note: step.title,
                busy: true
            };
        }
        // A prepare that failed left no preview, so there is nothing to analyse
        // and nothing to keep the panel for: the way forward is another file.
        // An analysis that failed is the other case — the image is fine, only
        // the run was not, so the file stays and only the Run button matters.
        if (state.inputName && (state.previewFileId || state.status !== 'failed')) {
            return { name: state.inputName, icon: 'icon-doc-inv', note: null, busy: false };
        }
        return null;
    },

    // -- data loading ----------------------------------------------------

    _loadCapability: function () {
        // Single-flight: render() runs whenever state changes, and the first few
        // renders can easily overlap one in-flight request.
        if (this.capabilityRequest) {
            return;
        }
        this.capabilityRequest = restRequest({
            url: 'precipitate/capability',
            method: 'GET',
            error: null
        })
            .always(() => {
                this.capabilityRequest = null;
            })
            .done((capability) => {
                this.capability = capability;
                Object.assign(this.form, {
                    scaleBarMicrons:
                        capability.settings.defaultScaleBarMicrons || this.form.scaleBarMicrons,
                    scaleBarPixels:
                        capability.settings.defaultScaleBarPixels || this.form.scaleBarPixels,
                    edgeToEdge: !!capability.settings.defaultEdgeToEdge,
                    preset: capability.settings.defaultPreset || this.form.preset
                });
                this._loadRuns();
            })
            .fail((error) => {
                this.capability = {
                    presets: [],
                    settings: {},
                    dependencies: { ok: false, missing: [] },
                    workspace: 'Precipitate Analysis',
                    error: this._message(error, 'This dashboard is not available.')
                };
                this.render();
            });
    },

    _loadRuns: function () {
        restRequest({ url: 'precipitate/run', method: 'GET', error: null })
            .done((runs) => {
                this.runs = runs;
            })
            .always(() => this.render());
    },

    /** Show a run: its preview, the regions it was run with, and its results. */
    _openRun: function (runId) {
        // Opening a run takes two requests, and results.json can be large. Without
        // a generation stamp, clicking run A then run B can leave B's preview on
        // screen with A's numbers under it, because A's slower response lands last.
        const generation = (this.openGeneration || 0) + 1;
        this.openGeneration = generation;

        if (!runId) {
            this.run = null;
            this.results = null;
            this.jobError = null;
            this.regions = [];
            this.detected = {};
            this.form.excludePanel = false;
            this.form.excludeBottomPx = 0;
            this.render();
            return;
        }

        restRequest({ url: `precipitate/run/${runId}`, method: 'GET', error: null })
            .done((run) => {
                if (this.openGeneration !== generation) {
                    return;
                }
                this.run = run;
                this.results = null;

                const state = run.state || {};
                const request = state.request || {};
                this.detected = state.detected || {};
                this._applyDetected(request);
                if (request.regions) {
                    // A stored whole-image run has one region covering everything;
                    // that is not a selection the user made, so it is not restored
                    // as one.
                    const wholeImage =
                        request.regions.length === 1 &&
                        request.regions[0].x === 0 &&
                        request.regions[0].y === 0 &&
                        state.image &&
                        request.regions[0].width === state.image.width;
                    this.regions = wholeImage ? [] : request.regions.slice();
                }

                if (state.resultFileId) {
                    this._loadResults(state.resultFileId, generation);
                } else {
                    this.render();
                }
            })
            .fail((error) => this._fail(error, 'Could not open that run.'));
    },

    _loadResults: function (fileId, generation) {
        // Fetched straight from the stored file rather than through an endpoint of
        // our own: it is the artifact of record, and core already enforces its ACL.
        // restRequest rather than $.ajax so the Girder-Token header is attached —
        // a session whose token lives only in localStorage has no cookie to fall
        // back on, and a private run folder would 401.
        restRequest({
            url: `file/${fileId}/download`,
            method: 'GET',
            dataType: 'json',
            error: null
        })
            .done((results) => {
                if (this.openGeneration === generation) {
                    this.results = results;
                }
            })
            .fail(() => {
                if (this.openGeneration === generation) {
                    this.results = null;
                }
            })
            .always(() => {
                if (this.openGeneration === generation) {
                    this.render();
                }
            });
    },

    _deleteRun: function (runId) {
        restRequest({
            url: `precipitate/run/${runId}`,
            method: 'DELETE',
            error: null
        })
            .done(() => {
                if (this.run && this.run._id === runId) {
                    this.run = null;
                    this.results = null;
                    this.regions = [];
                }
                this._loadRuns();
            })
            .fail((error) => this._fail(error, 'Could not delete that run.'));
    },

    // -- upload ----------------------------------------------------------

    _renderUpload: function () {
        // noParent + overrideStart: the run folder is created between the user
        // pressing upload and the bytes being sent, so choosing a file and then
        // changing their mind leaves no empty folders behind.
        this.uploadWidget = new UploadWidget({
            el: this.$('.g-precip-upload-mount'),
            parentView: this,
            modal: false,
            noParent: true,
            multiFile: false,
            title: false,
            onlyFiles: true,
            onlyFolders: false,
            overrideStart: true
        });

        this.uploadWidget.on('g:uploadStarted', () => {
            const file = this.uploadWidget.files[0];
            restRequest({
                url: 'precipitate/run',
                method: 'POST',
                data: { name: file ? file.name.replace(/\.[^.]+$/, '') : '' },
                error: null
            })
                .done((run) => {
                    this.run = run;
                    this.uploadWidget.parentType = 'folder';
                    this.uploadWidget.parent = new FolderModel({ _id: run._id });
                    this.uploadWidget.uploadNextFile();
                })
                .fail((error) => {
                    this.uploadWidget.setUploadEnabled(true);
                    this._fail(error, 'Could not create a folder for this run.');
                });
        });

        this.uploadWidget.on('g:uploadFinished', (info) => {
            const file = info.files[0];
            this._prepare(file.id, file.name);
        });

        this.uploadWidget.render();
    },

    // -- the two job steps -----------------------------------------------

    _prepare: function (fileId, name) {
        // Claim the step before asking for it. The bytes are already in Girder,
        // so from here to the end of the job the micrograph is settled — the
        // upload widget must not be on screen offering to replace it.
        this._pend('Preparing the micrograph', name);
        this.render();

        restRequest({
            url: `precipitate/run/${this.run._id}/prepare`,
            method: 'POST',
            data: { fileId: fileId },
            error: null
        })
            .done((job) => this._watch(job, 'Preparing the micrograph'))
            .fail((error) => this._fail(error, 'Could not prepare the micrograph.'));
    },

    _analyze: function () {
        if (!this.run) {
            return;
        }
        this.results = null;
        // Locked in place rather than by re-rendering: the render that follows
        // the scheduling response is about to rebuild the region selector
        // anyway, and doing it twice would flash the image for no reason.
        this._pend('Detecting precipitates', null);
        this._lockControls();
        this._updateJobUi();

        restRequest({
            url: `precipitate/run/${this.run._id}/analyze`,
            method: 'POST',
            data: {
                scaleBarMicrons: this.form.scaleBarMicrons,
                scaleBarPixels: this.form.scaleBarPixels,
                edgeToEdge: this.form.edgeToEdge,
                preset: this.form.preset,
                excludeBottomPx: this._excludeBottomPx(),
                regions: JSON.stringify(this.regions)
            },
            error: null
        })
            .done((job) => this._watch(job, 'Detecting precipitates'))
            .fail((error) => this._fail(error, 'Could not start the analysis.'));
    },

    /** Mark a step as asked for, before there is a job document to watch. */
    _pend: function (title, name) {
        this.jobError = null;
        this.pending = {
            title: title,
            name: name,
            status: 0,
            percent: 0,
            message: 'Starting…'
        };
    },

    _watch: function (job, title) {
        this.pending = null;
        this.jobError = null;
        this.job = {
            id: job._id,
            title: title,
            status: job.status,
            percent: 0,
            message: 'Starting…'
        };
        this.render();
        this._poll();
    },

    _poll: function () {
        if (this.pollTimer) {
            window.clearTimeout(this.pollTimer);
        }
        if (!this.job) {
            return;
        }
        this.pollTimer = window.setTimeout(() => {
            if (!this.job) {
                return;
            }
            restRequest({
                url: `job/${this.job.id}`,
                method: 'GET',
                error: null
            })
                .done((job) => this._applyJob(job))
                .always(() => this._poll());
        }, POLL_INTERVAL_MS);
    },

    _onJobEvent: function (event) {
        const info = event.data;
        if (this.job && info && info._id === this.job.id) {
            this._applyJob(info);
        }
    },

    _applyJob: function (job) {
        if (!this.job || job._id !== this.job.id) {
            return;
        }

        this.job.status = job.status;
        if (job.progress) {
            const total = job.progress.total || 100;
            this.job.percent = Math.max(
                0,
                Math.min(100, Math.round((100 * (job.progress.current || 0)) / total))
            );
            this.job.message = job.progress.message || this.job.message;
        }

        if (JOB_FINISHED.indexOf(job.status) === -1) {
            this._updateJobUi();
            return;
        }

        // Terminal. Stop polling, then reload the run so the new state (and any
        // results) come from the server rather than being inferred here.
        if (this.pollTimer) {
            window.clearTimeout(this.pollTimer);
            this.pollTimer = null;
        }
        const runId = this.run && this.run._id;
        const jobId = this.job.id;
        // A failure is remembered separately from `this.job`, which has to be
        // cleared either way: it is what disables the Run button while work is in
        // flight, so keeping it would leave a failed run with no way to retry.
        this.job = null;
        this.jobError = null;

        if (job.status !== JOB_SUCCESS) {
            this._explainFailure(jobId, job);
        }
        if (runId) {
            this._openRun(runId);
        } else {
            this.render();
        }
        this._loadRuns();
    },

    /**
     * Put the reason a job failed on screen.
     *
     * The `job_status` notification the event stream delivers deliberately omits
     * the log, so the document has to be re-fetched to find out what went wrong;
     * the payload already in hand is only a fallback for when that request fails.
     */
    _explainFailure: function (jobId, payload) {
        this.jobError =
            this._jobError(payload) ||
            'The job failed. Check the job log in Girder for details.';
        restRequest({ url: `job/${jobId}`, method: 'GET', error: null }).done((full) => {
            const message = this._jobError(full);
            if (message) {
                this.jobError = message;
                this.render();
            }
        });
    },

    /**
     * Pull the failure out of a job log, whichever path wrote it.
     *
     * The two paths log differently: the in-process one appends the exception on
     * its own line after a preamble, while girder_worker writes
     * `ExcClass: message` followed by an indented traceback. Taking the last
     * *unindented* line lands on the message in both cases — the last line of a
     * traceback is always indented, and the preamble is followed by the error.
     */
    _jobError: function (job) {
        const log = job.log;
        const text = Array.isArray(log) ? log.join('') : log || '';
        const lines = text
            .split('\n')
            .filter((line) => line.trim() && !/^\s/.test(line));
        return lines.length ? lines[lines.length - 1].trim() : null;
    },

    /** In-place progress update: a full re-render here would fight the user. */
    _updateJobUi: function () {
        const job = this.job || this.pending;
        if (!job) {
            return;
        }
        this.$('.g-precip-job').removeClass('hide');
        this.$('.g-precip-job-title').text(job.title);
        this.$('.g-precip-job-status').text(
            JOB_LABELS[job.status] || `Status ${job.status}`
        );
        this.$('.g-precip-job-message').text(job.message);
        // A previous failure's reason is not this job's; leaving it under a live
        // progress bar reads as the run having already failed.
        this.$('.g-precip-job-error').remove();
        this.$('.g-precip-progress .progress-bar').css('width', `${job.percent}%`);
    },

    // -- sub-views -------------------------------------------------------

    _renderRoiSelector: function (image) {
        const state = this.run.state || {};
        this.roiSelector = new RoiSelector({
            el: this.$('.g-precip-roi-mount'),
            parentView: this,
            previewUrl: this._fileUrl(state.previewFileId),
            width: image.width,
            height: image.height,
            regions: this.regions,
            excludeBottomPx: this._excludeBottomPx(),
            // The measured bar is drawn back onto the image it was measured on,
            // so "129 px" is something the user can see rather than take on trust.
            scaleBar: (this.detected.scale || {}).bar
        });
        this.roiSelector.on('g:regionsChanged', (regions) => {
            this.regions = regions;
            this._renderRegionList();
        });
        this.roiSelector.render();
        this.roiSelector.setOverlay(this.overlay);
        if (this.results) {
            this.roiSelector.setResults(this.results);
        }

        // Stroke widths are in image units, so they need recomputing when the
        // display scale changes.
        if (!this._onResize) {
            this._onResize = () => {
                if (this.roiSelector) {
                    this.roiSelector.refresh();
                }
            };
            $(window).on('resize.girderPrecipitate', this._onResize);
        }
    },

    _renderResults: function () {
        const state = this.run.state || {};
        this.resultsView = new ResultsView({
            el: this.$('.g-precip-results-mount'),
            parentView: this,
            results: this.results,
            resultUrl: state.resultFileId ? this._fileUrl(state.resultFileId) : null,
            folderUrl: `#folder/${this.run._id}`
        });
        this.resultsView.render();
    },

    _destroySubViews: function () {
        ['uploadWidget', 'roiSelector', 'resultsView'].forEach((key) => {
            if (this[key]) {
                this[key].destroy();
                this[key] = null;
            }
        });
    },

    // -- small helpers ---------------------------------------------------

    /**
     * Download URL for a stored file, carrying the session token.
     *
     * An `<img>` cannot send a Girder-Token header, and the run folder is private,
     * so the token goes in the query string — which core's file download endpoint
     * accepts, and which core's own EventStream does for the same reason. Relying
     * on the session cookie instead would break any session whose token lives only
     * in localStorage.
     */
    _fileUrl: function (fileId) {
        const token = getCurrentToken();
        const url = `${getApiRoot()}/file/${fileId}/download`;
        return token ? `${url}?token=${encodeURIComponent(token)}` : url;
    },

    _syncRegions: function () {
        if (this.roiSelector) {
            this.roiSelector.setRegions(this.regions);
        }
        this._renderRegionList();
    },

    /**
     * Re-render only the region list.
     *
     * Dragging a region must not re-render the whole view: that would replace the
     * <img> and make the image flash on every selection.
     */
    _renderRegionList: function () {
        const step = this.$('.g-precip-region-list').closest('.g-precip-step-body');
        if (!step.length) {
            this.render();
            return;
        }
        const items = this.regions.map(
            (region, index) =>
                '<li><span class="g-precip-region-swatch"></span>' +
                `<span class="g-precip-region-label">${region.label || `ROI ${index + 1}`}</span>` +
                `<span class="g-precip-region-box">${region.width} × ${region.height} px ` +
                `at ${region.x}, ${region.y}</span>` +
                `<button class="btn btn-link btn-xs g-precip-region-remove" type="button" ` +
                `data-index="${index}" title="Remove this region"><i class="icon-cancel"></i></button></li>`
        );

        if (!items.length) {
            this.render();
            return;
        }
        step.html(
            `<ul class="g-precip-region-list">${items.join('')}</ul>` +
                '<button class="btn btn-default btn-xs g-precip-region-clear" type="button">Clear all</button>'
        );
    },

    /**
     * Fill the form in from the run: what it was last analysed with, or failing
     * that what the backend detected in the image.
     *
     * A run that has already been analysed is reproduced exactly — whatever the
     * user settled on then is what they see now, detection or no detection. Only
     * a run that has never been analysed gets the detected values, which is the
     * one moment they are an improvement on a guess rather than an overwrite of
     * a decision.
     */
    _applyDetected: function (request) {
        const scale = this.detected.scale;
        const panel = this.detected.panel;

        if (request.scaleBarMicrons) {
            this.form.scaleBarMicrons = request.scaleBarMicrons;
            this.form.scaleBarPixels = request.scaleBarPixels;
            this.form.edgeToEdge = !!request.edgeToEdge;
            this.form.preset = request.preset || this.form.preset;
        } else if (scale && scale.barPixels) {
            // An incomplete scale is a measured bar whose printed length only the
            // user can read. Filling in the pixel count alone is the whole of what
            // is known — inventing a length to go with it would be a wrong answer
            // dressed as a measurement.
            this.form.scaleBarPixels = scale.barPixels;
            if (scale.complete) {
                this.form.scaleBarMicrons = scale.barMicrons;
            }
        }

        if (request.excludeBottomPx !== undefined && request.excludeBottomPx !== null) {
            this.form.excludeBottomPx = request.excludeBottomPx;
            this.form.excludePanel = request.excludeBottomPx > 0;
        } else if (panel && panel.height) {
            this.form.excludeBottomPx = panel.height;
            this.form.excludePanel = true;
        } else {
            this.form.excludeBottomPx = 0;
            this.form.excludePanel = false;
        }
    },

    /** Rows excluded from the bottom, as the analysis will see it. */
    _excludeBottomPx: function () {
        const pixels = Math.floor(this.form.excludeBottomPx);
        return this.form.excludePanel && pixels > 0 ? pixels : 0;
    },

    /** The height of the image that is actually analysed. */
    _contentHeight: function () {
        const image = (this.run && (this.run.state || {}).image) || {};
        return Math.max(0, (image.height || 0) - this._excludeBottomPx());
    },

    _useDetectedScale: function () {
        const scale = this.detected.scale;
        if (!scale) {
            return;
        }
        this.form.scaleBarPixels = scale.barPixels;
        if (scale.complete) {
            this.form.scaleBarMicrons = scale.barMicrons;
        }
        this.$('#g-precip-scale-microns').val(this.form.scaleBarMicrons);
        this.$('#g-precip-scale-pixels').val(this.form.scaleBarPixels);
        this._readForm();
        this._renderScaleSource();
    },

    _onExclusionChanged: function () {
        this._readForm();
        this.$('.g-precip-exclude-height').toggleClass('hide', !this.form.excludePanel);

        // Regions the exclusion has just eaten into have to be brought back into
        // line here and now. Leaving them to be clipped server-side would put a
        // rectangle on screen that is not the rectangle being analysed.
        const height = this._contentHeight();
        const before = this.regions.length;
        this.regions = this.regions
            .map((region) => {
                const bottom = Math.min(region.y + region.height, height);
                return Object.assign({}, region, { height: bottom - region.y });
            })
            .filter((region) => region.height >= 8);
        if (this.regions.length !== before) {
            girder.events.trigger('g:alert', {
                icon: 'info-circled',
                text: `${before - this.regions.length} region(s) fell inside the excluded band and were removed.`,
                type: 'info',
                timeout: 5000
            });
        }

        if (this.roiSelector) {
            this.roiSelector.setExclusion(this._excludeBottomPx());
            this.roiSelector.setRegions(this.regions);
        }
        this._renderRegionList();
        this._renderPanelNote();
    },

    _readForm: function () {
        const microns = parseFloat(this.$('#g-precip-scale-microns').val());
        const pixels = parseFloat(this.$('#g-precip-scale-pixels').val());
        if (isFinite(microns) && microns > 0) {
            this.form.scaleBarMicrons = microns;
        }
        if (isFinite(pixels) && pixels > 0) {
            this.form.scaleBarPixels = pixels;
        }
        const preset = this.$('#g-precip-preset').val();
        if (preset) {
            this.form.preset = preset;
        }
        const spacing = this.$('input[name="g-precip-spacing"]:checked').val();
        if (spacing) {
            this.form.edgeToEdge = spacing === 'edge';
        }

        const toggle = this.$('#g-precip-exclude-panel');
        if (toggle.length) {
            this.form.excludePanel = !!toggle.prop('checked');
        }
        const excluded = parseInt(this.$('#g-precip-exclude-px').val(), 10);
        if (isFinite(excluded) && excluded >= 0) {
            this.form.excludeBottomPx = excluded;
        }

        this._updateDerivedScale();
        // Whether the form still matches what was detected is a function of the
        // form, so it has to be recomputed on every keystroke that changes it —
        // not only when the detected value is applied.
        this._renderScaleSource();
    },

    _derivedScale: function () {
        const microns = this.form.scaleBarMicrons;
        const pixels = this.form.scaleBarPixels;
        if (!(microns > 0 && pixels > 0)) {
            return 'Enter the scale bar length and how many pixels it spans.';
        }
        const umPerPx = microns / pixels;
        return `${umPerPx.toFixed(6)} µm/px = ${(umPerPx * 1000).toFixed(2)} nm/px`;
    },

    _updateDerivedScale: function () {
        const microns = parseFloat(this.$('#g-precip-scale-microns').val());
        const pixels = parseFloat(this.$('#g-precip-scale-pixels').val());
        const umPerPx = microns / pixels;
        this.$('#g-precip-scale-derived').text(
            isFinite(umPerPx) && umPerPx > 0
                ? `${umPerPx.toFixed(6)} µm/px = ${(umPerPx * 1000).toFixed(2)} nm/px`
                : 'Enter the scale bar length and how many pixels it spans.'
        );
    },

    /**
     * Where the scale in the form came from, if it came from the image.
     *
     * Two quite different findings share this line. A vendor header is a pixel
     * size, so the form can be filled in and the job is done. A bar measured off
     * the image is only half of one — the length printed beside it is text, and
     * reading text is not something this does — so the user is told what is
     * missing and asked for exactly that.
     */
    _scaleSource: function () {
        const scale = this.detected.scale;
        if (!scale) {
            return null;
        }

        const differs = scale.complete
            ? Math.abs(this.form.scaleBarMicrons / this.form.scaleBarPixels -
                  scale.barMicrons / scale.barPixels) > 1e-9
            : this.form.scaleBarPixels !== scale.barPixels;

        return {
            level: scale.complete ? 'ok' : 'partial',
            icon: scale.complete ? 'icon-ok-circled' : 'icon-info-circled',
            message: `${scale.complete ? 'Read from' : 'Measured on'} ` +
                `${scale.label}. ${scale.detail}`,
            restorable: differs,
            restoreLabel: scale.complete
                ? `Use ${this._number(scale.barMicrons)} µm = ${this._number(scale.barPixels)} px`
                : `Use ${this._number(scale.barPixels)} px`
        };
    },

    /** The info-panel exclusion control: what was found, and what is set. */
    _panelContext: function () {
        const panel = this.detected.panel;
        const image = (this.run && (this.run.state || {}).image) || {};

        let found;
        if (panel && panel.height) {
            found =
                `A ${panel.height} px instrument info panel ` +
                `(${panel.source === 'pixels' ? 'found in the pixels' : 'stated by the image header'})` +
                ' sits below the specimen. Its text and drawn scale bar are bright ' +
                'and round enough to be detected as precipitates.';
        } else if ('panel' in this.detected) {
            found = 'No info panel was found at the bottom of this image. Tick this ' +
                'to exclude a band anyway.';
        } else {
            // A run prepared before this existed, or one whose inspection failed.
            // Nothing was looked for, so "none was found" would be a claim about
            // something that never happened.
            found = 'This image was not examined for an info panel. Tick this to ' +
                'exclude a band at the bottom.';
        }

        return {
            detected: !!(panel && panel.height),
            exclude: this.form.excludePanel,
            height: this.form.excludeBottomPx,
            note:
                `${found} ` +
                (this._excludeBottomPx()
                    ? `Analysing the top ${image.width} × ${this._contentHeight()} px.`
                    : 'The whole image is being analysed.')
        };
    },

    _number: function (value) {
        return Number(value.toFixed(3)).toLocaleString('en-US', {
            maximumFractionDigits: 3,
            useGrouping: false
        });
    },

    /** Repaint just the scale-source line, so the Use button can come and go. */
    _renderScaleSource: function () {
        const source = this._scaleSource();
        const button = this.$('.g-precip-scale-restore');
        if (!source) {
            return;
        }
        button.toggleClass('hide', !source.restorable).text(source.restoreLabel);
    },

    /** Repaint just the info-panel note, which quotes the analysed height. */
    _renderPanelNote: function () {
        this.$('.g-precip-exclude-note').text(this._panelContext().note);
    },

    _banner: function () {
        if (this.capability.error) {
            return {
                level: 'error',
                icon: 'icon-attention',
                title: 'Unavailable',
                message: this.capability.error
            };
        }
        const dependencies = this.capability.dependencies || {};
        if (!dependencies.ok) {
            return {
                level: 'error',
                icon: 'icon-attention',
                title: 'Analysis dependencies are missing',
                message:
                    `This Girder cannot run the analysis: ${(dependencies.missing || []).join(', ')} ` +
                    "is not installed. Install girder-dashboards' \"precipitate\" extra in both " +
                    'the Girder and the Celery worker environment.'
            };
        }
        const state = this.run ? this.run.state || {} : {};
        if (state.status === 'failed' && state.error) {
            return {
                level: 'error',
                icon: 'icon-attention',
                title: 'The last run of this analysis failed',
                message: state.error
            };
        }
        return null;
    },

    _computeNote: function () {
        if (!this.capability.dependencies || !this.capability.dependencies.ok) {
            return '';
        }
        return this.capability.worker
            ? 'Runs as a Celery task on the local queue.'
            : 'No Celery worker is available, so this runs in the Girder process.';
    },

    _jobContext: function () {
        const job = this.job || this.pending;
        // The panel outlives the job when the job failed, so the reason stays on
        // screen while the Run button becomes usable again.
        if (!job) {
            return this.jobError
                ? {
                    title: 'The last job failed',
                    status: JOB_LABELS[JOB_ERROR],
                    percent: 100,
                    message: '',
                    error: this.jobError
                }
                : null;
        }
        return {
            title: job.title,
            status: JOB_LABELS[job.status] || `Status ${job.status}`,
            percent: job.percent,
            message: job.message,
            error: null
        };
    },

    _runContext: function () {
        const format = (value) =>
            value === null || value === undefined || !isFinite(value)
                ? '—'
                : value.toLocaleString(undefined, { maximumFractionDigits: 2 });

        return this.runs.map((run) => {
            const state = run.state || {};
            const summary = state.summary || {};
            const status = state.status || 'new';
            return {
                id: run._id,
                name: run.name,
                current: this.run && this.run._id === run._id,
                detail: [
                    state.inputName,
                    summary.nRegions ? `${summary.nRegions} region(s)` : null,
                    summary.spacingMode
                ]
                    .filter(Boolean)
                    .join(' · '),
                status: STATUS_LABELS[status] || status,
                statusClass: status,
                particles: format(summary.nParticles),
                diameter: format(summary.diameterMeanNm),
                spacing: format(summary.spacingMeanNm)
            };
        });
    },

    _message: function (error, fallback) {
        return (
            (error && error.responseJSON && error.responseJSON.message) || fallback
        );
    },

    _fail: function (error, fallback) {
        this.job = null;
        this.pending = null;
        this.jobError = this._message(error, fallback);
        girder.events.trigger('g:alert', {
            icon: 'cancel',
            text: this._message(error, fallback),
            type: 'danger',
            timeout: 6000
        });
        this.render();
    },

    destroy: function () {
        if (this.pollTimer) {
            window.clearTimeout(this.pollTimer);
        }
        if (this._onResize) {
            $(window).off('resize.girderPrecipitate', this._onResize);
        }
        this._destroySubViews();
        View.prototype.destroy.apply(this, arguments);
    }
});

export default PrecipitateDashboard;
