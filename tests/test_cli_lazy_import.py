"""Regression tests for the package-level lazy CLI export."""

import subprocess
import sys


def test_cli_export_loads_main_only_when_accessed() -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "sys.path.insert(0, 'src')\n"
                "import infralink.cli as package\n"
                "assert 'infralink.cli.main' not in sys.modules\n"
                "assert package.cli.name == 'cli'\n"
                "assert 'infralink.cli.main' in sys.modules\n"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
