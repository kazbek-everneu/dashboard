"""The capture package's monotonic clock.

Every capture module reads time as ``clock.now()`` through this module object,
so a test patches ``app.screenshots.capture.clock.now`` without touching the
global ``time.monotonic`` the asyncio event loop itself relies on.
"""

import time

now = time.monotonic
