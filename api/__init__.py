"""HTTP API layer — one module per domain.

Each module exposes a FastAPI ``router`` (``profile`` also exposes
``dashboard_router``). ``main`` wires them. Routers hold no pipeline code —
they call ``services.*``.
"""
