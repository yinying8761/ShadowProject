import json
from datetime import datetime


async def get_current_time() -> str:
    """Return the local current time as a compact JSON payload."""
    now = datetime.now().astimezone()
    return json.dumps(
        {
            "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": str(now.tzinfo),
            "weekday": now.strftime("%A"),
            "iso": now.isoformat(),
        },
        ensure_ascii=False,
    )
