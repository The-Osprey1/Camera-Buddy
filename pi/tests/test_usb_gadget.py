"""Runs setup/usb-gadget.sh against a fake folder instead of the Pi's real one."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "setup" / "usb-gadget.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run(tmp_path, controller=True):
    udc = tmp_path / "udc"
    udc.mkdir(exist_ok=True)
    if controller:
        (udc / "fe980000.usb").mkdir(exist_ok=True)
    env = dict(os.environ, DESK_CONFIGFS=str(tmp_path / "config"), DESK_UDC_CLASS=str(udc), DESK_UDC_WAIT="1")
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def gadget(tmp_path):
    return tmp_path / "config" / "usb_gadget" / "deskpresence"


def test_sets_up_keyboard_and_data_line(tmp_path):
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    g = gadget(tmp_path)
    assert (g / "UDC").read_text().strip() == "fe980000.usb"
    assert (g / "idProduct").read_text().strip() == "0x0104"
    assert (g / "configs/c.1/hid.usb0").is_symlink()
    assert (g / "configs/c.1/acm.usb0").is_symlink()


def test_fails_clearly_when_no_controller(tmp_path):
    done = run(tmp_path, controller=False)
    assert done.returncode != 0
    assert "dwc2" in done.stderr
    assert not gadget(tmp_path).exists()


def test_finishes_a_half_made_gadget(tmp_path):
    # As if the power went (or the controller was late) after the links were made.
    assert run(tmp_path).returncode == 0
    (gadget(tmp_path) / "UDC").write_text("")
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert (gadget(tmp_path) / "UDC").read_text().strip() == "fe980000.usb"


def test_leaves_a_working_link_alone(tmp_path):
    assert run(tmp_path).returncode == 0
    report = gadget(tmp_path) / "functions/hid.usb0/report_desc"
    before = report.read_bytes()
    done = run(tmp_path)
    assert "already set up and connected" in done.stdout
    assert report.read_bytes() == before
