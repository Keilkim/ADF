import sys

if __name__ == '__main__':
    # The background agent skips the editor's imports and stays small.
    if sys.argv[1:] == ['--background']:
        from adf.agent import main
    else:
        from adf.app import main
    raise SystemExit(main())
