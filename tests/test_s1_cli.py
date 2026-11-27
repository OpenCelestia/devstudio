import os
import tempfile
import unittest
from pathlib import Path

from devstudio.adapter.catalog import list_adapters
from devstudio.cli.main import main


class CliTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(main(["--version"]), 0)

    def test_adapter_list_empty_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DEVSTUDIO_ROOT"] = tmp
            try:
                self.assertEqual(list_adapters(), [])
                self.assertEqual(main(["adapter", "list"]), 0)
            finally:
                os.environ.pop("DEVSTUDIO_ROOT", None)

    def test_adapter_list_reads_adapter_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adapters" / "AnyCloud39AV200_V1.04").mkdir(parents=True)
            (root / "adapters" / "AnyCloud39AV200_V1.04" / "adapter.yaml").write_text(
                "release_id: AnyCloud39AV200_V1.04\n", encoding="utf-8"
            )
            (root / "adapters" / "skip").mkdir()
            os.environ["DEVSTUDIO_ROOT"] = str(root)
            try:
                self.assertEqual(list_adapters(), ["AnyCloud39AV200_V1.04"])
            finally:
                os.environ.pop("DEVSTUDIO_ROOT", None)

    def test_unknown_command(self):
        self.assertEqual(main(["nope"]), 2)


if __name__ == "__main__":
    unittest.main()
