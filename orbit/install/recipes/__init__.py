"""Install recipes for the apps Orbit knows how to install. Add one: subclass `base.Recipe`, list it here."""
from .nova import NovaRecipe
from .orbit import OrbitRecipe
from .starcast import StarcastRecipe

ALL = (StarcastRecipe, NovaRecipe, OrbitRecipe)


def load(host, sources):
    return {r.id: r(host, sources) for r in ALL}
