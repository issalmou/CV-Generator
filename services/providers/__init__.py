"""
Job-source providers.

One module per platform (``linkedin_provider.py``, ``indeed_provider.py``,
...). Every provider subclasses :class:`services.providers.base.JobProvider`,
does all network I/O through :mod:`services.providers.http`, and normalises
its results to :class:`schemas.jobs.NormalizedOffer`.

``services/jobs/search_service.py`` only ever talks to the
:data:`services.providers.registry.registry` — it contains **no**
platform-specific logic.
"""
