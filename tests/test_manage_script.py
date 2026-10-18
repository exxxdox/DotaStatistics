"""Exercise the Linux entry point without touching services or the network."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires Linux bash")
class ManageScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Special characters catch broken quoting and sed replacement escaping.
        self.project = self.root / "project & bot"
        self.project.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copyfile(source / "manage.sh", self.project / "manage.sh")
        (self.project / "main.py").write_text("", encoding="utf-8")
        (self.project / "res").mkdir()
        shutil.copyfile(source / "res/dota.service", self.project / "res/dota.service")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls"
        self.unit = self.root / "installed.service"
        self.secrets = self.root / "secrets"
        self.home = self.root / "home"
        (self.home / ".local/bin").mkdir(parents=True)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "CALL_LOG": str(self.log),
            "UNIT_CAPTURE": str(self.unit),
        }
        self.secrets.write_text("QQBOT_APP_ID=test\n", encoding="utf-8")
        self.write_executable(
            self.home / ".local/bin/uv",
            'printf "uv|%s" "$PWD" >> "$CALL_LOG"\n'
            'printf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n',
        )
        self.write_executable(
            self.bin / "install",
            'printf "install" >> "$CALL_LOG"\n'
            'printf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n'
            'if [[ "$1" == "-m" && "$2" == "0644" ]]; then cp "$3" "$UNIT_CAPTURE"; fi\n',
        )
        self.write_executable(self.bin / "getent", "exit 0\n")
        self.write_executable(
            self.bin / "chown",
            'printf "chown" >> "$CALL_LOG"\nprintf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n',
        )
        self.write_executable(
            self.bin / "chmod",
            'printf "chmod" >> "$CALL_LOG"\nprintf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n',
        )
        self.write_executable(self.bin / "runuser", 'shift 3\nexec "$@"\n')
        self.write_executable(
            self.bin / "systemd-analyze",
            'printf "systemd-analyze" >> "$CALL_LOG"\n'
            'printf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n',
        )
        self.write_executable(
            self.bin / "systemctl",
            'printf "systemctl" >> "$CALL_LOG"\n'
            'printf "|%s" "$@" >> "$CALL_LOG"\nprintf "\\n" >> "$CALL_LOG"\n',
        )
        # Any unexpected network installation fails safely instead of running curl.
        self.write_executable(self.bin / "curl", 'echo "unexpected curl" >&2\nexit 99\n')
        (self.project / ".venv/bin").mkdir(parents=True)
        self.write_executable(self.project / ".venv/bin/python", "exit 0\n")

    @staticmethod
    def write_executable(path, body):
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def run_script(self, *arguments):
        # Tests redirect privileged paths without weakening production defaults.
        script = (self.project / "manage.sh").read_text(encoding="utf-8")
        script = script.replace('DATA_DIR="/var/lib/dota"', f'DATA_DIR="{self.root / "data"}"')
        script = script.replace('SECRETS_FILE="/etc/dota/secrets"', f'SECRETS_FILE="{self.secrets}"')
        script = script.replace('if [[ ${EUID} -ne 0 ]]; then', 'if false; then')
        (self.project / "manage.sh").write_text(script, encoding="utf-8")
        return subprocess.run(
            ["bash", str(self.project / "manage.sh"), *arguments],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10,
        )

    def test_help_and_invalid_arguments_do_not_run_actions(self):
        for arguments, expected in [((), 0), (("help",), 0), (("unknown",), 2)]:
            with self.subTest(arguments=arguments):
                result = self.run_script(*arguments)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn("start", result.stdout + result.stderr)
                self.assertFalse(self.log.exists())

    def test_start_finds_project_and_uv_from_another_directory(self):
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log.read_text().splitlines(),
            [f"uv|{self.project}|run|--frozen|--no-dev|python|main.py"],
        )

    def test_init_requires_preinstalled_uv_without_downloading_script(self):
        (self.home / ".local/bin/uv").unlink()

        result = self.run_script("init")

        self.assertEqual(result.returncode, 1)
        self.assertIn("uv 未安装", result.stderr)
        self.assertFalse(self.log.exists())

    def test_deploy_and_init_render_unit_and_apply_service_in_order(self):
        for command in ("deploy", "init"):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_script(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.log.read_text().splitlines()
                if command == "init":
                    self.assertEqual(calls.pop(0), f"uv|{self.project}|sync|--frozen|--no-dev")
                self.assertTrue(calls[0].startswith("install|-d|-m|0700|-o|dota|-g|dota|"), calls)
                self.assertTrue(calls[1].startswith("install|-d|-m|0750|-o|root|-g|dota|"), calls)
                self.assertEqual(calls[2], f"chown|root:dota|{self.secrets}")
                self.assertEqual(calls[3], f"chmod|0640|{self.secrets}")
                self.assertTrue(calls[4].startswith("systemd-analyze|verify|"), calls)
                self.assertTrue(calls[5].startswith("install|-m|0644|"), calls)
                self.assertTrue(calls[5].endswith("|/etc/systemd/system/dota.service"))
                self.assertEqual(calls[6:], [
                    "systemctl|daemon-reload",
                    "systemctl|enable|dota.service",
                    "systemctl|restart|dota.service",
                    "systemctl|status|--no-pager|--full|dota.service",
                ])
                unit = self.unit.read_text()
                self.assertNotIn("__PROJECT_DIR__", unit)
                self.assertIn(f"WorkingDirectory={self.project}", unit)
                self.assertIn(f'ExecStart="{self.project}/.venv/bin/python" main.py', unit)
                self.assertIn("User=dota", unit)
                self.assertIn("NoNewPrivileges=true", unit)
                self.assertIn("EnvironmentFile=/etc/dota/secrets", unit)
                self.assertIn("UMask=0077", unit)


if __name__ == "__main__":
    unittest.main()
