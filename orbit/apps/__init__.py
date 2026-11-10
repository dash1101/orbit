"""The apps Orbit knows. Add a built-in one by writing an App subclass and listing it in BUILTIN."""
from .beacon import Beacon
from .custom import CustomApp
from .nova import Nova
from .pocketfader import Pocketfader

BUILTIN = (Pocketfader, Nova, Beacon)


def load(ctx):
    """All apps, in the user's order (built-ins first by default), keyed by id."""
    apps = {cls.id: cls(ctx) for cls in BUILTIN}
    for spec in ctx.config.get("custom"):
        if isinstance(spec, dict) and spec.get("id") and spec["id"] not in apps:
            try:
                apps[spec["id"]] = CustomApp(ctx, spec)
            except (KeyError, TypeError):
                continue
    order = [i for i in ctx.config.get("order") if i in apps]
    order += [i for i in apps if i not in order]
    return {i: apps[i] for i in order}
