def pytest_collection_modifyitems(items):
    items.append(items[0])
