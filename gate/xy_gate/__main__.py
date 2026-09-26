"""python -m xy_gate <子命令>（等价于 xy-gate CLI）。"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
