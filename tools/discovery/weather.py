"""Weather query tool"""
from mocks import MockBackend

_backend = MockBackend()

def get_weather(location="北京"):
    return _backend.handle_weather({"location": location})
