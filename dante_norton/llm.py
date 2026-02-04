"""
LLM client for interacting with language models.

Provides a wrapper around llm7shi for managing conversation history
and converting history to/from XML format for persistence.
"""

from typing import List, Dict, Optional, Any
from xml.dom.minidom import Document, parseString
from llm7shi.compat import generate_with_schema


class LLMClient:
    """
    Client for interacting with language models via llm7shi.

    Manages conversation history and provides methods for making LLM calls
    with automatic history tracking.
    """

    def __init__(self, model: str, think: bool, temperature: float = 1.0):
        """
        Initialize the LLM client.

        Args:
            model: Model identifier to use
            think: Whether to include thinking in responses
            temperature: Sampling temperature (default: 1.0)
        """
        self.model: str = model
        self.think: bool = think
        self.temperature: float = temperature
        self.history: List[Dict[str, str]] = []

    def copy(self) -> 'LLMClient':
        """
        Create a copy of the LLMClient with the same model, think setting, and history.

        Returns:
            New LLMClient instance with copied history
        """
        new_client = LLMClient(self.model, self.think, self.temperature)
        new_client.history = self.history.copy()
        return new_client

    def call(self, prompt: str, system_prompt: Optional[str] = None, schema: Any = None) -> str:
        """
        Call LLM and automatically add query/response to history.

        Args:
            prompt: User prompt text
            system_prompt: Optional system prompt (used only for first call)
            schema: Optional JSON schema or Pydantic model for structured output

        Returns:
            Response text from LLM
        """
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.extend(self.history)
        messages.append({'role': 'user', 'content': prompt})

        response = generate_with_schema(
            messages,
            schema=schema,
            model=self.model,
            include_thoughts=self.think,
            temperature=self.temperature,
            show_params=False,
        )
        response_text = response.text.strip()

        # Automatically add to history
        if system_prompt:
            self.history.append({'role': 'system', 'content': system_prompt})
        self.history.append({'role': 'user', 'content': prompt})
        self.history.append({'role': 'assistant', 'content': response_text})

        return response_text

def history_to_xml(history: List[Dict[str, str]]) -> str:
    """
    Convert LLM interaction history to XML format.

    Args:
        history: List of message dictionaries with 'role' and 'content'

    Returns:
        XML string representing the history
    """
    doc = Document()
    messages = doc.createElement("messages")
    doc.appendChild(messages)
    for msg in history:
        message = doc.createElement("message")
        message.setAttribute("role", msg["role"])
        content = msg["content"].rstrip()
        if content:
            message.appendChild(doc.createCDATASection(f"\n{content}\n"))
        messages.appendChild(message)
    return doc.toprettyxml(encoding='utf-8', indent='').decode('utf-8')

def xml_to_history(xml_string: str) -> List[Dict[str, str]]:
    """
    Convert XML format back to LLM interaction history.

    Args:
        xml_string: XML string representing the history

    Returns:
        List of message dictionaries with 'role' and 'content'
    """
    doc = parseString(xml_string)
    history = []
    for message in doc.getElementsByTagName("message"):
        role = message.getAttribute("role")
        content = ""
        for child in message.childNodes:
            if child.nodeType == child.CDATA_SECTION_NODE:
                content = child.data
                if content.startswith('\n'):
                    content = content[1:]
                break
        history.append({"role": role, "content": content})
    return history
