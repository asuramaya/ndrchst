"""PyInstaller entry point. Kept as a top-level script (not a module)
because PyInstaller wants a script path, not a `-m` target."""
from ndrchst_pilot.app import run

if __name__ == "__main__":
    run()
