"""Application-service composition seam.

This package owns composition of use cases that coordinate existing
capabilities without owning their domain semantics.  It is an in-process
seam: it is not an API, a transport, a runtime host, a job runtime or a
client.

ASS-01 establishes ownership and enforcement only.  No application service,
selector resolution, result envelope, error translation or configuration
resolution is implemented here yet; those are ASS-02 and ASS-03.
"""

__all__: list[str] = []
