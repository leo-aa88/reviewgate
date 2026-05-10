def test_package_root_exposes_core() -> None:
    import reviewgate

    assert reviewgate.core is not None


def test_core_submodules_importable() -> None:
    """Guarantee the §15 skeleton modules exist and are importable."""
    import reviewgate.core.categorizer  # noqa: F401
    import reviewgate.core.cli  # noqa: F401
    import reviewgate.core.config  # noqa: F401
    import reviewgate.core.heuristics  # noqa: F401
    import reviewgate.core.report  # noqa: F401
    import reviewgate.core.schemas  # noqa: F401
