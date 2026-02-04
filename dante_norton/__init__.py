from importlib.metadata import version

__name__ = "dante_norton"
__version__ = version(__name__)

from .parser import Canto
from .utils import roman_number

__all__ = ['Canto', 'roman_number']
