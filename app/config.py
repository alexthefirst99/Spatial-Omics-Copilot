import os

try:
    import yaml
except ImportError:  # pragma: no cover - dependency error is clearer at runtime.
    yaml = None


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "app.yaml")
DOTENV_PATH = os.path.join(PROJECT_ROOT, ".env")

_CONFIG = None
_DOTENV_LOADED = False


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_dotenv(path=DOTENV_PATH):
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    if not os.path.exists(path):
        return

    with open(path) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = _strip_quotes(value)


def load_config(path=CONFIG_PATH):
    global _CONFIG
    load_dotenv()
    if _CONFIG is not None:
        return _CONFIG

    if not os.path.exists(path):
        _CONFIG = {}
        return _CONFIG

    if yaml is None:
        raise RuntimeError("PyYAML is required to read config/app.yaml. Install dependencies with `pip install -r requirements.txt`.")

    with open(path) as f:
        _CONFIG = yaml.safe_load(f) or {}
    return _CONFIG


def _nested_get(data, dotted_key):
    current = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def get_config(dotted_key, default=None, env=None):
    load_dotenv()
    if env and os.environ.get(env) not in (None, ""):
        return os.environ[env]

    value = _nested_get(load_config(), dotted_key)
    return default if value is None else value


def get_bool(dotted_key, default=False, env=None):
    value = get_config(dotted_key, default=default, env=env)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_path(dotted_key, default=None, env=None):
    value = str(get_config(dotted_key, default=default, env=env))
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        value = os.path.join(PROJECT_ROOT, value)
    return value
