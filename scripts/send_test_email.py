from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from private_credit_monitor.monitor import send_test_email


if __name__ == "__main__":
    sent, error = send_test_email()
    if not sent:
        raise SystemExit(error or "Test email failed.")
    print("Test email sent successfully.")
