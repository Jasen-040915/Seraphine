"""Small frozen entry point for the keyboard service; no UI/model imports."""
import sys

from app.overlay.keyboard_runtime import run_worker


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--keyboard-worker':
        raise SystemExit(2)
    run_worker(sys.argv[2], sys.argv[3])
