"""Install recipes for the apps Orbit knows how to install. Add one: subclass `base.Recipe`, list it here."""
from .beacon import BeaconRecipe
from .nova import NovaRecipe
from .pocketfader import PocketfaderRecipe

ALL = (PocketfaderRecipe, NovaRecipe, BeaconRecipe)


def load(host, sources):
    return {r.id: r(host, sources) for r in ALL}
