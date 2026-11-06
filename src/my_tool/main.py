"""Entry point for the venue scheduling demand change tool."""

import argparse
import sys


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="venue-scheduling-demand-change",
        description="AI-assisted venue scheduling & demand change support tool.",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s 0.1.0"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to the configuration file (default: config/default.yaml)",
    )
    # Add subcommands and other arguments as needed

    args = parser.parse_args()
    # Placeholder: replace with actual logic
    print(f"Venue Scheduling Demand Change Tool v0.1.0")
    print(f"Config: {args.config}")
    print("Ready. (Core logic to be implemented in src/my_tool/core/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
