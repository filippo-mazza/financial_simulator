from pathlib import Path


def _can_create_directory_under(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.exists() and candidate.is_dir() and _is_writable(candidate)


def _is_writable(path: Path) -> bool:
    try:
        probe = path / ".pytest-write-probe"
        probe.touch()
        probe.unlink()
    except OSError:
        return False
    return True


def pytest_configure(config):
    basetemp = config.option.basetemp
    if basetemp and not _can_create_directory_under(Path(basetemp)):
        config.option.basetemp = str(config.rootpath / ".pytest-tmp")
