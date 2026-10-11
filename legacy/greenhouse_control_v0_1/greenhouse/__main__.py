import argparse
import uvicorn

from .config import Config


def main():
    parser = argparse.ArgumentParser(description="AquaCrop greenhouse dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    cfg = Config.from_env()  # Validate configuration before serving.
    # Do not launch multiple worker processes: one controller owns the Modbus bus.
    uvicorn.run("greenhouse.app:create_app", factory=True, host=args.host,
                port=args.port, workers=1, access_log=True)


if __name__ == "__main__":
    main()
