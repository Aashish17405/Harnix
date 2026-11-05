from slug import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_whitespace_and_edges():
    assert slugify("  Many   spaces  ") == "many-spaces"


def test_symbols_collapse():
    assert slugify("a -- b__c") == "a-b-c"


def test_empty():
    assert slugify("") == ""
