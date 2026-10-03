from pathlib import Path
import subprocess
from typer.testing import CliRunner
from book_distiller.cli import main

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[2]


def test_help():
    result = runner.invoke(main.app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_version():
    result = runner.invoke(main.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "Book Distiller 0.1.0-dev"


def test_doctor():
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert result.output.count("OK") == 8


def test_doctor_missing_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "find_project_root", lambda: tmp_path)
    monkeypatch.setenv("BOOK_DISTILLER_HOME", str(tmp_path))
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_doctor_unsupported_os(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Linux")
    assert runner.invoke(main.app, ["doctor"]).exit_code == 1


def test_launcher_from_other_directory(tmp_path):
    result = subprocess.run([str(ROOT / "book"), "version"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Book Distiller 0.1.0-dev"


def test_launcher_without_venv(tmp_path):
    launcher = tmp_path / "book"
    launcher.write_bytes((ROOT / "book").read_bytes())
    launcher.chmod(0o755)
    result = subprocess.run([str(launcher), "version"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Run ./setup.sh first." in result.stderr


def test_doctor_wrong_python(monkeypatch):
    monkeypatch.setattr(main.sys, "version_info", (3, 13, 0))
    assert runner.invoke(main.app, ["doctor"]).exit_code == 1


def test_doctor_missing_root(monkeypatch):
    from book_distiller.core.errors import ConfigurationError
    def missing():
        raise ConfigurationError("Project root not found")
    monkeypatch.setattr(main, "find_project_root", missing)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
