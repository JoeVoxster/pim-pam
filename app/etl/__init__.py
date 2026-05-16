__all__ = ["pim_import_main"]


def pim_import_main() -> int:
    from app.etl.pim_import import main

    return main()
