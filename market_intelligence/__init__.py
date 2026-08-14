"""Scott Market Intelligence.

The package is intentionally independent from the Flask web process so the
same pipeline can run from a Railway worker, a cron invocation, or the manual
web endpoint without importing ``app.py``.
"""

from .config import IntelligenceConfig
from .service import run_intelligence

__all__ = ["IntelligenceConfig", "run_intelligence"]
__version__ = "1.0.0"
