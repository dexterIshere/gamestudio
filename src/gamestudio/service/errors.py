"""Business errors of the service layer, translated by each interface.

The service knows neither HTTP nor MCP: it raises these errors and each
interface renders them in its own terms -- a 404 or 402 status for the API, a
readable `ValueError` for an MCP agent. An exception that is not a
`ServiceError` is a studio bug, not a caller mistake: it propagates unchanged.
"""

from __future__ import annotations


class ServiceError(Exception):
    """The request is at fault: invalid parameter, incompatible state."""

    status = 400


class NotFound(ServiceError):
    """The requested resource does not exist."""

    status = 404


class PaymentRequired(ServiceError):
    """A paid operation called without the user's explicit consent."""

    status = 402
