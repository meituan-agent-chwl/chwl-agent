"""Activity search tool"""
from mocks import MockBackend

_backend = MockBackend()

def search(scene="family"):
    return _backend.handle_activities_search({"scene": scene})
