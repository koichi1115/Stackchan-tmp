"""`scripts/backup-device.sh` を実機なしで端から端まで実行して検証します。

PATH の先頭に偽の `esptool.py` を置き、スクリプト本体には手を入れずに走らせます。
検証したい順序（検出 → 拒否 → 2 回目の読み出し → SHA-256 比較 → 拒否 → 成功）で
出力が並ぶよう、テスト名に番号を付けています。
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "backup-device.sh"
FAKE_ESPTOOL = ROOT / "tests" / "fixtures" / "fake_esptool.py"

_spec = importlib.util.spec_from_file_location("flash_fixtures", ROOT / "tests" / "test_flash_tools.py")
flash_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flash_fixtures)

FLASH_BYTES = 4 * 1024 * 1024
FLASH_ID_OUTPUT = """esptool.py v4.7.0
Serial port /dev/ttyUSB0
Connecting....
Detecting chip type... ESP32
Chip is ESP32-D0WDQ6 (revision v1.0)
Features: WiFi, BT, Dual Core, 240MHz
Crystal is 40MHz
Manufacturer: 5e
Device: 4016
Detected flash size: 4MB
Hard resetting via RTS pin...
"""
FLASH_ID_WITHOUT_SIZE = "\n".join(
    line for line in FLASH_ID_OUTPUT.splitlines() if not line.startswith("Detected flash size:")
)
SUCCESS_LINE = "バックアップ成功。2 回の読み出しが SHA-256 で一致しました。書き込みへ進んで構いません。"


class BackupRun:
    """偽 esptool を PATH に置いた 1 回分の実行結果です。"""

    def __init__(self, workspace: Path, scenario: dict) -> None:
        self.workspace = workspace
        bin_dir = workspace / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "esptool.py"
        shutil.copy(FAKE_ESPTOOL, shim)
        shim.chmod(0o755)

        image, _table = flash_fixtures.synthetic_flash()
        image_path = workspace / "device-flash.bin"
        image_path.write_bytes(image)

        self.port = workspace / "ttyFAKE"
        self.port.touch()
        self.output_root = workspace / "backups"
        self.log = workspace / "esptool-calls.log"
        scenario_path = workspace / "scenario.json"
        scenario_path.write_text(json.dumps({"image": str(image_path), **scenario}), encoding="utf-8")

        environment = dict(os.environ)
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
        environment["FAKE_ESPTOOL_SCENARIO"] = str(scenario_path)
        environment["FAKE_ESPTOOL_LOG"] = str(self.log)

        completed = subprocess.run(
            [
                "bash",
                str(BACKUP_SCRIPT),
                "--port",
                str(self.port),
                "--output-dir",
                str(self.output_root),
                "--banner-seconds",
                "1",
            ],
            capture_output=True,
            text=True,
            env=environment,
            timeout=300,
        )
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr

    @property
    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def subcommands(self) -> list[str]:
        names = []
        for call in self.calls:
            tokens = call.split(" ")
            index = 0
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 2
            names.append(tokens[index])
        return names

    def full_reads(self) -> list[str]:
        return [call for call in self.calls if f"read_flash 0 {FLASH_BYTES} " in call]

    @property
    def backup_dir(self) -> Path:
        directories = sorted(self.output_root.iterdir())
        if len(directories) != 1:
            raise AssertionError(f"バックアップ先が 1 つではありません: {directories}")
        return directories[0]


def run_backup(case, scenario: dict) -> BackupRun:
    """`case` はテストクラスでもインスタンスでも構いません（後片付けはクラス単位です）。"""
    workspace = Path(tempfile.mkdtemp(prefix="backup-test-"))
    case.addClassCleanup(shutil.rmtree, workspace, True)
    return BackupRun(workspace, scenario)


class BackupScriptTests(unittest.TestCase):
    """偽 esptool を通した実行結果を、必要な証拠の順に確かめます。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ok = run_backup(cls, {"flash_id_output": FLASH_ID_OUTPUT})

    def test_1_detects_chip_and_flash_size_from_flash_id(self) -> None:
        self.assertEqual(self.ok.returncode, 0, self.ok.stderr)
        self.assertIn(
            "検出結果: チップ=ESP32-D0WDQ6 (revision v1.0) フラッシュ=4MB (4194304 バイト)",
            self.ok.stdout,
        )
        self.assertIn("flash_id", self.ok.subcommands())
        self.assertIn(
            "Detected flash size: 4MB",
            (self.ok.backup_dir / "chip-info.txt").read_text(encoding="utf-8"),
        )
        manifest = (self.ok.backup_dir / "manifest.txt").read_text(encoding="utf-8")
        self.assertIn("flash_size_label=4MB", manifest)
        self.assertIn(f"flash_size_bytes={FLASH_BYTES}", manifest)
        self.assertIn("chip=ESP32-D0WDQ6 (revision v1.0)", manifest)

    def test_2_refuses_when_detected_flash_size_line_is_absent(self) -> None:
        run = run_backup(self, {"flash_id_output": FLASH_ID_WITHOUT_SIZE})

        self.assertEqual(run.returncode, 1)
        self.assertIn("フラッシュ容量を検出できませんでした。中止します。", run.stderr)
        self.assertIn("容量を仮定して読み出すことはしません。", run.stderr)
        self.assertNotIn("read_flash", run.subcommands())
        self.assertNotIn(SUCCESS_LINE, run.stdout)

    def test_3_refuses_when_dump_length_differs_from_detected_size(self) -> None:
        run = run_backup(
            self,
            {"flash_id_output": FLASH_ID_OUTPUT, "short_read_bytes": FLASH_BYTES - 4096},
        )

        self.assertEqual(run.returncode, 1)
        self.assertIn(
            f"読み出し長が検出容量と一致しません（{FLASH_BYTES - 4096} != {FLASH_BYTES}）。",
            run.stderr,
        )
        self.assertIn("バックアップは無効です。", run.stderr)
        self.assertEqual(len(run.full_reads()), 1, "長さ不一致なら 2 回目へ進みません。")
        self.assertNotIn(SUCCESS_LINE, run.stdout)

    def test_4_reads_the_whole_flash_a_second_time(self) -> None:
        self.assertEqual(len(self.ok.full_reads()), 2, self.ok.calls)
        self.assertIn("[3/7] フラッシュ全体を読み出します（1 回目）。", self.ok.stdout)
        self.assertIn("[4/7] フラッシュ全体をもう一度読み出します（2 回目）。", self.ok.stdout)
        for name in ("read-1.log", "read-2.log"):
            self.assertTrue((self.ok.backup_dir / name).exists(), name)
        self.assertFalse(
            (self.ok.backup_dir / "full-flash.verify.bin").exists(),
            "照合用のダンプは一致確認後に削除されます。",
        )

    def test_5_compares_the_two_reads_by_sha256(self) -> None:
        image = self.ok.backup_dir / "full-flash.bin"
        digest = hashlib.sha256(image.read_bytes()).hexdigest()

        self.assertEqual(image.stat().st_size, FLASH_BYTES)
        self.assertIn(f"SHA-256: {digest}", self.ok.stdout)
        self.assertIn(f"full_flash_sha256={digest}", (self.ok.backup_dir / "manifest.txt").read_text(encoding="utf-8"))
        self.assertIn(
            f"{digest}  full-flash.bin",
            (self.ok.backup_dir / "SHA256SUMS").read_text(encoding="utf-8"),
        )

    def test_6_refuses_with_non_zero_exit_when_the_hashes_differ(self) -> None:
        run = run_backup(
            self,
            {"flash_id_output": FLASH_ID_OUTPUT, "corrupt_second_read": True},
        )

        self.assertEqual(run.returncode, 1)
        self.assertEqual(len(run.full_reads()), 2)
        self.assertIn("2 回の読み出しの SHA-256 が一致しません。", run.stderr)
        self.assertIn("書き込みへ進まないでください。", run.stderr)
        self.assertIn("1 回目: ", run.stderr)
        self.assertIn("2 回目: ", run.stderr)
        self.assertNotIn(SUCCESS_LINE, run.stdout)

    def test_7_prints_the_exact_success_line_when_the_hashes_match(self) -> None:
        self.assertEqual(self.ok.returncode, 0, self.ok.stderr)
        self.assertEqual(self.ok.stdout.rstrip("\n").splitlines()[-1], SUCCESS_LINE)

    def test_8_never_issues_an_erase_or_write_command(self) -> None:
        self.assertTrue(self.ok.calls)
        for name in self.ok.subcommands():
            self.assertIn(name, ("version", "flash_id", "read_flash"))
        for call in self.ok.calls:
            self.assertNotIn("erase", call)
            self.assertNotIn("write_flash", call)


if __name__ == "__main__":
    unittest.main()
