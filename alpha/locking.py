"""Unchanged local directory lock, separated from the legacy application import."""
import os

def lock_data(root):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / 'service.lock').open('a+b')
    try:
        # Do not read the locked byte: Windows rejects that before locking,
        # which previously leaked this handle on a second launch.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit('This data directory is already in use by AIC. Open the existing service.')
    return handle

