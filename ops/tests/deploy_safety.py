"""Run in Linux: exercise the actual deploy script's drain/recovery with stubs."""
import ast
import re
import subprocess
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "deploy.ps1").read_text(encoding="utf-8")
REMOTE = SCRIPT.split("$remoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
RECOVERY = REMOTE.split("recover_previous_application() {", 1)[1].split("\ntrap recover_previous_application EXIT", 1)[0]
RECOVERY = "recover_previous_application() {" + RECOVERY
DRAIN = REMOTE.split("drained=false", 1)[1].split("\napplication_stopped=true", 1)[0]
DRAIN = "drained=false" + DRAIN


class DeploymentSafety(unittest.TestCase):
    def shell(self, source):
        return subprocess.run(["bash", "-c", source], text=True, capture_output=True, timeout=5)

    def test_remote_bash_and_embedded_python_parse(self):
        result = subprocess.run(["bash", "-n"], input=REMOTE, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for code in re.findall(r"<<'PY'\n(.*?)\nPY", REMOTE, flags=re.S):
            ast.parse(code)

    def drain(self, mode):
        stub = r'''
set -Eeuo pipefail
backend_container=only-test-container
calls=0
docker() {
  cat >/dev/null
  calls=$((calls + 1))
  case "$mode" in
    unavailable) return 2 ;;
    busy) return 1 ;;
    wait) [ "$calls" -gt 1 ] || return 1 ;;
  esac
  return 0
}
sleep() { :; }
fail() { echo "$1"; exit 1; }
'''
        return self.shell("mode=" + mode + "\n" + stub + DRAIN + '\necho "drained=$drained checks=$calls"')

    def test_idle_call_check_allows_deployment(self):
        result = self.drain("idle")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("drained=true checks=1", result.stdout)

    def test_waits_for_existing_call_without_terminating_it(self):
        result = self.drain("wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checks=2", result.stdout)

    def test_unavailable_provider_aborts_before_stopping(self):
        result = self.drain("unavailable")
        self.assertEqual(result.returncode, 1)
        self.assertIn("active_call_check_failed", result.stdout)

    def test_busy_calls_abort_after_bounded_wait(self):
        result = self.drain("busy")
        self.assertEqual(result.returncode, 1)
        self.assertIn("active_calls_did_not_drain", result.stdout)

    def recover(self, *, stopped="true", complete="false", restore_fails=False):
        stub = '''
previous_backend_image=old-backend
previous_frontend_image=old-frontend
backend_image_tag=backend-tag
frontend_image_tag=frontend-tag
current_dir=/docker/telefonagent/releases/previous
project=telefonagent
backend_health=http://127.0.0.1:18001/health
frontend_health=http://127.0.0.1:18081/
sleep() { :; }
curl() { return 0; }
'''
        stub += 'docker() { echo "docker $*"; ' + ('return 1;' if restore_fails else 'return 0;') + ' }\n'
        return self.shell(f"application_stopped={stopped}\ndeployment_complete={complete}\n" + stub + RECOVERY + "\nfalse\nrecover_previous_application")

    def test_failure_restores_only_previous_application_and_keeps_error_exit(self):
        result = self.recover()
        self.assertEqual(result.returncode, 1)
        self.assertIn("previous_application_healthy", result.stderr)
        self.assertIn("--no-build --no-deps backend frontend", result.stdout)
        self.assertNotIn("downgrade", result.stdout)
        self.assertNotIn("volume", result.stdout)

    def test_before_stop_and_completed_deploy_do_not_restore(self):
        for values in ({"stopped": "false"}, {"complete": "true"}):
            with self.subTest(values=values):
                result = self.recover(**values)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("docker ", result.stdout)

    def test_failed_restore_is_not_reported_as_healthy(self):
        result = self.recover(restore_fails=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("deployment_recovery=failed", result.stderr)
        self.assertNotIn("previous_application_healthy", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
