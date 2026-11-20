"""Ride photo buffer on disk (the alpha flow): files live under PHOTO_DIR/<ride_id>/<n>.jpg and the
photo_buf rows in State are the queue. The onboarding bot saves incoming photos here; the runner
loads them for the album and wipes the directory after publishing. Paths in logs carry no tokens:
the buffer layout is opaque numbers only, and the download URL (which embeds the bot token) is
never logged — see onboarding._download_photo."""
import os
import shutil
from pathlib import Path


def root(cfg) -> Path:
    """The photo buffer root: PHOTO_DIR when set, else DATA_DIR + "/photos"."""
    return Path(cfg.photo_dir or os.path.join(cfg.data_dir, "photos"))


def ride_dir(root_path, ride_id: int) -> Path:
    return Path(root_path) / str(int(ride_id))


def save(root_path, ride_id: int, n: int, data: bytes) -> str:
    """Write photo number n of the ride; returns the stored path (what photo_buf keeps)."""
    d = ride_dir(root_path, ride_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{int(n)}.jpg"
    p.write_bytes(data)
    return str(p)


def load(root_path, rows: list, limit: int) -> list:
    """(name, bytes) pairs for the first `limit` buffered rows, oldest first. A file that vanished
    from disk is skipped with no error: a partial album beats a failed one."""
    out = []
    for row in rows[:max(0, int(limit))]:
        p = Path(row["path"])
        if not p.is_file():
            continue
        out.append((p.name, p.read_bytes()))
    return out


def clear_ride(root_path, ride_id: int) -> None:
    """Remove the ride's photo directory (after publish, or when the user is deleted)."""
    shutil.rmtree(ride_dir(root_path, ride_id), ignore_errors=True)
