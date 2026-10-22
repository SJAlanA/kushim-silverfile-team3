"""SilverFile entry point.

Usage (planned):
    python run.py --case demo/eval_case_1.md
    python run.py --batch [--no-context-strategy]
    python run.py --dashboard
"""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Kushim SilverFile: Form 1040-SR draft preparation")
    parser.add_argument("--case", help="Path to an eval case file to run")
    parser.add_argument("--batch", action="store_true", help="Run the batch scenario")
    parser.add_argument("--no-context-strategy", action="store_true", help="Disable the context-window strategy")
    parser.add_argument("--dashboard", action="store_true", help="Start the staff dashboard")
    args = parser.parse_args()
    print("SilverFile is not wired up yet.", vars(args))


if __name__ == "__main__":
    main()
