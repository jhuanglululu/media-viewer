import uvicorn

from app import app
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=int(args.port))


if __name__ == "__main__":
    main()
