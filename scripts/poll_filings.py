from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from private_credit_monitor.monitor import main


if __name__ == "__main__":
    raise SystemExit(main())
