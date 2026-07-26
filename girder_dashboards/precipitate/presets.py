"""The published detection tunings, and how a request resolves to a parameter set.

Split out of :py:mod:`.analysis` because the REST layer needs to offer the presets
and validate a request without importing numpy, scipy and scikit-image. Those are
an optional extra of this plugin; a Girder that has not installed them must still
be able to load and to explain itself.
"""

#: Detection tunings published with the research code. The two conditions need
#: different knobs because 5 hr precipitates are larger and brighter than 1 hr
#: ones; picking the wrong preset finds nothing rather than finding it badly,
#: which is why this is offered in the UI and not buried in a config file.
PRESETS = {
    "fine": {
        "label": "Fine (725 °C, 1 hr) — small, dim precipitates",
        "tophatRadius": 2,
        "smoothSigma": 0.35,
        "minSigma": 0.55,
        "maxSigma": 2.50,
        "numSigma": 25,
        "threshold": 0.018,
        "overlap": 0.55,
        "minDiameterPx": 2.0,
        "maxDiameterPx": 9.0,
        "localRadiusFactor": 2.8,
        "thresholdFraction": 0.45,
        "minPeakBrightness": 0.45,
        "minContrastRatio": 2.0,
        "minAreaLocal": 2,
        "maxAreaLocal": 65,
        "minEquivDiameter": 1.5,
        "maxEquivDiameter": 9.5,
        "maxAspectRatio": 1.65,
        "minCircularity": 0.45,
        "minSolidity": 0.65,
        "maxEccentricity": 0.72,
        "minFillFraction": 0.03,
        "maxComponentCount": 3,
        # The three knobs below are where the two published tunings differ in
        # control flow rather than in a threshold. Keeping them as parameters is
        # what lets one function reproduce both scripts exactly.
        "annulusInner": 1.6,
        "minBlobRadius": 1,
        "minLocalRadius": 4,
        # 1 hr: clamp a small local window up to minLocalRadius and carry on.
        "clampLocalRadius": True,
        # 1 hr: a black annulus means infinite contrast, i.e. accept.
        "acceptOnDarkAnnulus": True,
    },
    "coarse": {
        "label": "Coarse (725 °C, 5 hr) — large, bright precipitates",
        "tophatRadius": 4,
        "smoothSigma": 0.6,
        "minSigma": 1.2,
        "maxSigma": 10.0,
        "numSigma": 35,
        "threshold": 0.04,
        "overlap": 0.30,
        "minDiameterPx": 4.0,
        "maxDiameterPx": 50.0,
        "localRadiusFactor": 2.5,
        "thresholdFraction": 0.38,
        "minPeakBrightness": 0.65,
        "minContrastRatio": 8.0,
        "minAreaLocal": 15,
        "maxAreaLocal": 9000,
        # The multi-image script drops the equivalent-diameter gate entirely and
        # relies on the area range instead; None keeps that behaviour explicit.
        "minEquivDiameter": None,
        "maxEquivDiameter": None,
        "maxAspectRatio": 1.8,
        "minCircularity": 0.50,
        "minSolidity": 0.72,
        "maxEccentricity": 0.80,
        "minFillFraction": 0.15,
        "maxComponentCount": 2,
        "annulusInner": 1.5,
        "minBlobRadius": 2,
        "minLocalRadius": 3,
        # 5 hr: reject a candidate whose local window is too small outright.
        "clampLocalRadius": False,
        # 5 hr: a black annulus is treated as a detection failure.
        "acceptOnDarkAnnulus": False,
    },
}

DEFAULT_PRESET = "fine"

#: Knobs a caller may override on top of a preset. Anything outside this set is
#: rejected rather than silently ignored, so a typo in the settings object is a
#: visible error instead of a run that quietly used the default.
_TUNABLE_PARAMS = frozenset(PRESETS["fine"]) - {"label"}

#: The only knobs for which ``None`` means something: "no such gate". Every other
#: parameter is used in arithmetic, where None is a TypeError partway through a
#: run rather than a rejected request.
_NULLABLE_PARAMS = frozenset({"minEquivDiameter", "maxEquivDiameter"})


class AnalysisError(Exception):
    """A run failed for a reason the user can act on."""


def presetParams(preset=None, overrides=None):
    """Resolve a preset name plus overrides into a full parameter dict."""
    name = preset or DEFAULT_PRESET
    if name not in PRESETS:
        raise AnalysisError(
            f"Unknown detection preset '{name}'; expected one of "
            f"{', '.join(sorted(PRESETS))}."
        )

    params = {k: v for k, v in PRESETS[name].items() if k != "label"}
    for key, value in (overrides or {}).items():
        if key not in _TUNABLE_PARAMS:
            raise AnalysisError(f"Unknown detection parameter '{key}'.")
        if value is None:
            if key not in _NULLABLE_PARAMS:
                raise AnalysisError(
                    f"Detection parameter '{key}' cannot be null; it is used in "
                    "arithmetic, so a null would fail the run partway through."
                )
        elif not isinstance(value, (int, float, bool)):
            raise AnalysisError(f"Detection parameter '{key}' must be a number.")
        params[key] = value
    params["preset"] = name
    return params
