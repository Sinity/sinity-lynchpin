from __future__ import annotations

from lynchpin.analysis.machine.devshell import _command_class


def test_devshell_command_class_uses_effective_command_prefix() -> None:
    assert _command_class("nix develop") == "nix_develop"
    assert _command_class("FOO=bar nix build .#lynchpin") == "nix_build"
    assert _command_class("sudo nix flake check") == "nix_flake"
    assert _command_class("! direnv reload") == "direnv_activation"


def test_devshell_command_class_does_not_match_search_text() -> None:
    assert _command_class('rg "nix develop" docs') is None
    assert _command_class("printf 'direnv reload'") is None
