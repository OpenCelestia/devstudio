import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from devstudio.cli.main import main
from devstudio.tools.registry import invoke
from devstudio.workspace import tasks_dir


class MergeAndRulesTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.environ["DEVSTUDIO_DATA"] = str(self.root / "data")
        self.project = self.root / "tree"
        self.project.mkdir()
        os.chdir(self.project)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        os.environ.pop("DEVSTUDIO_DATA", None)

    def _run(self, args):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_generate_without_sequence_skips_init_and_matches_mcp(self):
        (self.project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\n",
            encoding="utf-8",
        )
        for name in ("t0001", "t0002"):
            (tasks_dir(self.project) / name).mkdir(parents=True)
        code, out, err = self._run([
            "rules", "generate", "--task", "t0001", "--ref", "gc5603", "--model", "mit999",
        ])
        self.assertEqual(code, 0, out + err)
        self.assertIn("未生成初始化数组", out)
        manifest = json.loads((tasks_dir(self.project) / "t0001" / "plan_src" / "merge_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(manifest.get("init_regs"))
        self.assertTrue(any("没有寄存器序列" in item for item in manifest["gaps"]))
        result = invoke("rules", "cis_generate", {"task_id": "t0002", "ref": "gc5603", "model": "mit999"}, self.project)
        self.assertEqual(result["status"], "ok", result)
        other = json.loads((tasks_dir(self.project) / "t0002" / "plan_src" / "merge_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["written"], other["written"])
        self.assertEqual(manifest["gaps"], other["gaps"])

    def test_merge_waits_for_review_and_approval(self):
        sdk = self.project / "sdk"
        ref = sdk / "driver_src" / "sensor" / "gc5603"
        ref.mkdir(parents=True)
        original = ref / "gc5603.c"
        original.write_text("int gc5603_ref(void) { return 1; }\n", encoding="utf-8")
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        (self.project / "devstudio.yaml").write_text(
            "state: sdk_celestia\nrelease_id: AnyCloud39AV200_V1.04\nsdk:\n  root: sdk\n",
            encoding="utf-8",
        )
        task = tasks_dir(self.project) / "t0001"
        src = task / "plan_src"
        src.mkdir(parents=True)
        (task / "plan.md").write_text("# 计划 t0001\n状态：已批准\n", encoding="utf-8")
        (task / "task.json").write_text(json.dumps({"task_id": "t0001", "state": "sdk_celestia"}), encoding="utf-8")
        (src / "mit999.c").write_text("int mit999_init(void) { return 0; }\n", encoding="utf-8")
        (src / "isp_mit999.conf").write_text("binary", encoding="utf-8")
        (src / "merge_manifest.json").write_text(
            json.dumps({"model": "mit999", "ref_model": "gc5603", "gaps": ["没有寄存器序列，初始化表未改"]}),
            encoding="utf-8",
        )
        code, out, err = self._run(["plan", "merge", "t0001"])
        self.assertEqual(code, 1, err)
        self.assertIn("待复核", out)
        self.assertFalse((sdk / "driver_src" / "sensor" / "mit999").exists())
        code, out, err = self._run(["plan", "review", "t0001", "--item", "没有寄存器序列，初始化表未改"])
        self.assertEqual(code, 0, out + err)
        code, out, err = self._run(["plan", "merge", "t0001"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("未写入 SDK", out)
        self.assertFalse((sdk / "driver_src" / "sensor" / "mit999").exists())
        code, out, err = self._run(["plan", "merge-apply", "t0002"])
        self.assertEqual(code, 1, err)
        self.assertIn("未批准", out)
        code, out, err = self._run(["plan", "approve", "t0002"])
        self.assertEqual(code, 0, out + err)
        code, out, err = self._run(["plan", "merge-apply", "t0002"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue((sdk / "driver_src" / "sensor" / "mit999" / "mit999.c").is_file())
        self.assertFalse((sdk / "driver_src" / "sensor" / "mit999" / "isp_mit999.conf").exists())
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), before)
        self.assertIn("参考型号文件未改", out)
