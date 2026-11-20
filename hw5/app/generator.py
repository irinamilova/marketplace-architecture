import random
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

DEVICES = ("MOBILE", "DESKTOP", "TV", "TABLET")


def session(rng, user, movie, started_at, session_id, completed):
    device = rng.choice(DEVICES)
    search_first = rng.random() < 0.7
    events = [(-15, "SEARCHED", 0)] if search_first else []
    events.append((0, "VIEW_STARTED", 0))
    progress = rng.randint(60, 600)
    if rng.random() < 0.65:
        events.extend([(progress, "VIEW_PAUSED", progress), (progress + 20, "VIEW_RESUMED", progress)])
    if completed:
        duration = rng.randint(2400, 7200)
        events.append((duration + 20, "VIEW_FINISHED", duration))
        if rng.random() < 0.45:
            events.append((duration + 30, "LIKED", 0))
    for offset, kind, seconds in events:
        yield {
            "event_id": str(uuid5(NAMESPACE_URL, f"cinema/{session_id}/{kind}")),
            "user_id": user,
            "movie_id": movie,
            "event_type": kind,
            "timestamp": started_at + timedelta(seconds=offset),
            "device_type": device,
            "session_id": session_id,
            "progress_seconds": seconds,
        }


def history(today=None):
    today = today or datetime.now(timezone.utc).date()
    sessions = []
    for day_offset in range(14, 0, -1):
        first_day = today - timedelta(days=day_offset)
        for index in range(8):
            user = f"demo-{first_day}-{index}"
            for age in range(min(day_offset, 8)):
                rng = random.Random(f"{user}-{age}")
                if age and rng.random() >= 0.72 - age * 0.07:
                    continue
                day = first_day + timedelta(days=age)
                start = datetime.combine(day, time(rng.randint(8, 20), rng.randrange(60)), timezone.utc)
                sessions.extend(session(rng, user, f"movie-{rng.randint(1, 12):02}", start,
                                        f"{user}-{age}", rng.random() < 0.8))
    yield from sorted(sessions, key=lambda record: record["timestamp"])


def live():
    rng = random.Random()
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=125)
    if start.date() != now.date():
        uid = str(uuid4())
        yield {"event_id": uid, "user_id": f"live-{rng.randrange(24)}", "movie_id": "movie-01",
               "event_type": "SEARCHED", "timestamp": now, "device_type": rng.choice(DEVICES),
               "session_id": uid, "progress_seconds": 0}
        return
    yield from session(rng, f"live-{rng.randrange(24)}", f"movie-{rng.randint(1, 12):02}",
                       start, str(uuid4()), rng.random() < 0.8)
