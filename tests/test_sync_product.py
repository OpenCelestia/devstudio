import tempfile
import unittest
from pathlib import Path

from devstudio.orchestration.init_cmd import _recorded_product, _sync_product
from devstudio.root import repo_root


class SyncProductTests(unittest.TestCase):
    def test_same_product_does_not_rewrite_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "devstudio.yaml").write_text("project:\n  name: anypi_nova\n", encoding="utf-8")
            (project / "AGENTS.md").write_text("keep\n", encoding="utf-8")
            code = _sync_product(repo_root(), project, "anypi_nova")
            self.assertEqual(code, 0)
            self.assertEqual((project / "AGENTS.md").read_text(encoding="utf-8"), "keep\n")

    def test_recorded_product_reads_yaml_name(self):
        self.assertEqual(_recorded_product({"project": {"name": "anypi_luna"}}), "anypi_luna")
        self.assertEqual(_recorded_product({}), "")


if __name__ == "__main__":
    unittest.main()
