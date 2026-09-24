"""The offline backend's destructive-command patterns.

`is_destructive` is a `critical` question: a hit blocks on its own. So these patterns have to
catch the commands that actually destroy things, and stay off the ones people run all day.
"""

from __future__ import annotations

import pytest

from jev_guard.backends.local import DESTRUCTIVE_PATTERNS


def matches(command: str) -> bool:
    return any(pattern.search(command) for pattern in DESTRUCTIVE_PATTERNS)


DESTRUCTIVE = [
    "rm -rf / --no-preserve-root",
    "rm -rf /",
    "sudo rm -fr ~",
    "rm -rf ~/Documents",
    "rm -rf *",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "echo boom > /dev/sda",
    "format c:",
    "del /s /q C:\\Data\\archive",
    ":(){:|:&};:",
    "psql -c 'DROP DATABASE production'",
    "DROP TABLE customers;",
    "TRUNCATE TABLE orders",
    "DELETE FROM users;",
    "terraform destroy -auto-approve",
    "kubectl delete namespace production",
    "aws s3 rm s3://backups --recursive",
    "docker volume rm app_data",
    "git push --force origin main",
    "git push -f",
    "git reset --hard HEAD~10",
    "git clean -fdx",
    "chmod -R 777 /",
    "chown -R nobody /",
]

SAFE = [
    "ls -la",
    "rm -rf node_modules",
    "rm -rf ./build",
    "rm -f tmp/cache.json",
    "git push origin feature/new-policy",
    "git push --force-with-lease origin my-branch",
    "git status",
    "DELETE FROM users WHERE id = 42",
    "SELECT * FROM customers LIMIT 10",
    "kubectl delete pod web-7d9f",
    "docker ps -a",
    "pytest -q",
    "aws s3 cp report.csv s3://reports/",
    "chmod 755 scripts/preflight.py",
    "dd if=backup.img of=restored.img",
    "npm run format",
    "cat notes/DEVIATIONS.md",
]


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_match(command: str) -> None:
    assert matches(command), f"missed a destructive command: {command!r}"


@pytest.mark.parametrize("command", SAFE)
def test_ordinary_commands_do_not_match(command: str) -> None:
    assert not matches(command), f"false positive on an everyday command: {command!r}"
