"""Capability scopes used by public resource access."""

from enum import StrEnum


class CapabilityScope(StrEnum):
    READ_PAGE = "read:page"
    READ_IMAGE = "read:image"
    REPROCESS_PAGE = "reprocess:page"


class DocumentCapabilityScope(StrEnum):
    READ_DOCUMENT = "read:document"
    READ_DOCUMENT_IMAGE = "read:document-image"
    REPROCESS_DOCUMENT = "reprocess:document"
    MANAGE_DOCUMENT = "manage:document"


CapabilityScopeValue = CapabilityScope | DocumentCapabilityScope
