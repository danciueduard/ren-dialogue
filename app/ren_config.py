from os import getenv


def ren_env(name, default=None):
    """Read a Ren Dialogue setting, with support for older launch configurations."""
    return getenv(f"REN_{name}", getenv(f"RIN_{name}", default))
