"""Restaurant search tool"""
from mocks import MockBackend

_backend = MockBackend()

def search(scene="family", party_size=3):
    return _backend.handle_restaurants_search({"scene": scene, "party_size": party_size})
