import argparse
from pathlib import Path

import uvicorn

from studio.app import create_app


def main():
    parser = argparse.ArgumentParser(description='Local ChatGPT Image Studio')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=None)
    arguments = parser.parse_args()
    uvicorn.run(create_app(arguments.data_dir), host='127.0.0.1', port=arguments.port, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
