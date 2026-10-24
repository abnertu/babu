import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def test_cli_uses_xdg_config_and_persists_runs_across_processes(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(length))
            assert payload["messages"][-1]["content"] == "hello"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"choices": [{"message": {"content": "fixture reply"}, "finish_reason": "stop"}]}).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tmp_path / "config/babu"
    directory.mkdir(parents=True)
    config = directory / "config.toml"
    config.write_text(f'[llm]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\n')
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_STATE_HOME": str(tmp_path / "state")}

    # The fixture uses its own loopback endpoint and the dependency's bundled CA defaults.
    # Nix deliberately sets nonexistent CA paths in build environments; host proxy settings
    # and credentials must not influence this isolated endpoint.
    for key in (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "babu.main", *args], env=env, capture_output=True, text=True, timeout=10, check=True
        )

    try:
        assert "valid" in cli("check-runtime").stdout
        output = cli("chat", "hello").stdout
        assert "completed\nfixture reply" in output
        run_id = re.search(r"\[([a-f0-9]+)\]", output).group(1)
        state = json.loads(cli("show", run_id).stdout)
        assert state["content"] == "fixture reply"
        events = [json.loads(line) for line in cli("show", run_id, "--events").stdout.splitlines()]
        assert [event["kind"] for event in events] == ["RunStarted", "ModelStarted", "RunFinished"]
        assert (tmp_path / "state/babu/babu.db").is_file()
        # Inspection remains available when an enabled integration's credentials are missing.
        config.write_text(
            config.read_text() + "\n[plugins.notion]\nenabled = true\n"
            '[plugins.notion.default.tasks]\ndata_source_id = "fixture-tasks"\n'
            '[plugins.notion.default.finance]\ndata_source_id = "fixture-finance"\n'
        )
        env.pop("NOTION_TOKEN", None)
        assert json.loads(cli("show", run_id).stdout)["id"] == run_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
