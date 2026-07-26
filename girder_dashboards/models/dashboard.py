import datetime
import logging

from girder.constants import AccessType
from girder.exceptions import ValidationException
from girder.models.model_base import AccessControlledModel

from ..registry import (
    KEY_REGEX,
    DashboardDefinition,
    getDashboard,
    listDashboards,
    normalizeAuthors,
    registeredKeys,
)

logger = logging.getLogger(__name__)


def _now():
    return datetime.datetime.now(datetime.UTC)


class Dashboard(AccessControlledModel):
    """Persistent state for a dashboard declared in the Python registry.

    The registry says which dashboards *exist*; a document of this model holds
    the parts an admin gets to change: whether the dashboard is enabled, who
    may see and run it (the ACL), the card metadata, and free-form per-dashboard
    settings handed to the dashboard's view at runtime.

    Exactly one document exists per registered key; documents are created by
    :py:meth:`provision` at plugin load time and are never silently recreated,
    so admin edits survive restarts.
    """

    def initialize(self):
        self.name = "dashboard"
        self.ensureIndices([("key", {"unique": True})])
        self.exposeFields(
            level=AccessType.READ,
            fields=(
                "_id",
                "key",
                "name",
                "description",
                "authors",
                "image",
                "icon",
                "enabled",
                "settings",
                "public",
                "publicFlags",
                "created",
                "updated",
            ),
        )

    def validate(self, doc):
        key = (doc.get("key") or "").strip()
        if not KEY_REGEX.match(key):
            raise ValidationException(
                f"Invalid dashboard key '{key}': must match {KEY_REGEX.pattern}", "key"
            )
        doc["key"] = key

        name = (doc.get("name") or "").strip()
        if not name:
            raise ValidationException("Dashboard name must not be empty.", "name")
        doc["name"] = name

        doc["description"] = (doc.get("description") or "").strip()

        try:
            doc["authors"] = normalizeAuthors(doc.get("authors"))
        except ValueError as e:
            raise ValidationException(str(e), "authors") from e

        doc["icon"] = (doc.get("icon") or "").strip() or "icon-gauge"
        doc["enabled"] = bool(doc.get("enabled", False))

        image = doc.get("image")
        if image is not None and not isinstance(image, str):
            raise ValidationException("Dashboard image must be a string.", "image")
        doc["image"] = (image or "").strip() or None

        settings = doc.get("settings")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            raise ValidationException(
                "Dashboard settings must be a JSON object.", "settings"
            )
        doc["settings"] = settings

        # The unique index enforces this too, but raising here gives a usable
        # error message instead of a 500 from pymongo.
        duplicate = self.findOne({"key": key, "_id": {"$ne": doc.get("_id")}})
        if duplicate is not None:
            raise ValidationException(
                f"A dashboard with key '{key}' already exists.", "key"
            )

        doc["updated"] = _now()
        return doc

    def provision(self, definition: DashboardDefinition) -> dict:
        """Ensure a document exists for ``definition``, seeded from it.

        Uses a single ``$setOnInsert`` upsert so that the many workers that all
        run ``load()`` at boot cannot race each other into duplicate documents,
        and so that a redeploy never clobbers an admin's edits.

        New dashboards start out **disabled** — an admin has to turn them on
        from the plugin config page — but publicly readable, so enabling one is
        the only step needed to make it visible to everyone.
        """
        now = _now()
        result = self.collection.update_one(
            {"key": definition.key},
            {
                "$setOnInsert": {
                    "key": definition.key,
                    "name": definition.name,
                    "description": definition.description,
                    "authors": list(definition.authors),
                    "image": definition.image,
                    "icon": definition.icon,
                    "settings": dict(definition.settings),
                    "enabled": False,
                    "public": True,
                    "publicFlags": [],
                    "access": {"users": [], "groups": []},
                    "created": now,
                    "updated": now,
                }
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            logger.info("Provisioned dashboard '%s'", definition.key)
        else:
            # Fields added to the model after a document was written are absent
            # rather than edited, so seeding them is not clobbering anything. An
            # admin who cleared the authors has a present-but-empty list and is
            # left alone.
            self.collection.update_one(
                {"key": definition.key, "authors": {"$exists": False}},
                {"$set": {"authors": list(definition.authors)}},
            )
        return self.findOne({"key": definition.key})

    def provisionAll(self) -> list[dict]:
        """Provision a document for every registered dashboard."""
        return [self.provision(definition) for definition in listDashboards()]

    def resetToDefaults(self, doc: dict) -> dict:
        """Restore the card metadata and settings from the registry.

        Leaves ``enabled`` and the ACL alone; those are the admin's decisions,
        not the dashboard author's.
        """
        definition = getDashboard(doc["key"])
        if definition is None:
            raise ValidationException(
                f"Dashboard '{doc['key']}' is no longer registered, so it has no "
                "defaults to reset to.",
                "key",
            )
        doc["name"] = definition.name
        doc["description"] = definition.description
        doc["authors"] = list(definition.authors)
        doc["image"] = definition.image
        doc["icon"] = definition.icon
        doc["settings"] = dict(definition.settings)
        return self.save(doc)

    def isAvailable(self, doc: dict) -> bool:
        """Whether this document's key still has a registered implementation."""
        return getDashboard(doc["key"]) is not None

    def listForUser(
        self,
        user=None,
        level=AccessType.READ,
        includeDisabled=False,
        includeUnavailable=False,
        offset=0,
        limit=0,
        sort=None,
    ):
        """Find dashboards ``user`` may access, newest registry state first.

        :param includeDisabled: Include dashboards an admin has turned off.
        :param includeUnavailable: Include documents whose key no longer has a
            registered implementation (e.g. the providing plugin was removed).
        """
        query = {}
        if not includeDisabled:
            query["enabled"] = True
        if not includeUnavailable:
            query["key"] = {"$in": registeredKeys()}

        return self.findWithPermissions(
            query, offset=offset, limit=limit, sort=sort, user=user, level=level
        )
