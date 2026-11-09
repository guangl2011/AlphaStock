import pipelines


def test_package_imports() -> None:
    assert pipelines.__version__ == "0.1.0"
