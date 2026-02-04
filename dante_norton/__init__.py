from importlib.metadata import version

__name__ = "dante_norton"
__version__ = version(__name__)

from .parser import Canto
from .utils import roman_number
from .llm import LLMClient, history_to_xml, xml_to_history

__all__ = ['Canto', 'roman_number', 'LLMClient', 'history_to_xml', 'xml_to_history']
