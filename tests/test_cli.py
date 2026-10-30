import subprocess
import sys

from minigit.commits import CommitManager


def run_cli(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "minigit.cli", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def init_repo(repo):
    result = run_cli(repo, "init")
    assert result.returncode == 0
    assert result.stderr == ""


def commit_file(repo, content, message):
    (repo / "file.txt").write_text(content)
    assert run_cli(repo, "add", "file.txt").returncode == 0
    result = run_cli(repo, "commit", "-m", message)
    assert result.returncode == 0
    assert result.stderr == ""
    return CommitManager(repo).read_ref("main")


def test_log_subprocess_detailed_oneline_and_unborn(tmp_path):
    init_repo(tmp_path)
    unborn = run_cli(tmp_path, "log")
    assert (unborn.returncode, unborn.stdout, unborn.stderr) == (0, "no commits yet\n", "")
    commit_hash = commit_file(tmp_path, "hello\n", "héllo\nbody")

    detailed = run_cli(tmp_path, "log")
    assert detailed.returncode == 0
    assert detailed.stderr == ""
    assert f"commit {commit_hash}\n" in detailed.stdout
    assert "Author: minigit <minigit@local>\n" in detailed.stdout
    assert "Date:   " in detailed.stdout and "+00:00\n" in detailed.stdout
    assert "    héllo\n    body\n" in detailed.stdout

    compact = run_cli(tmp_path, "log", "--oneline")
    assert (compact.returncode, compact.stdout, compact.stderr) == (
        0,
        f"{commit_hash[:7]} héllo\n",
        "",
    )


def test_branch_and_checkout_subprocess_messages_and_errors(tmp_path):
    init_repo(tmp_path)
    tip = commit_file(tmp_path, "base\n", "base")

    created = run_cli(tmp_path, "branch", "feature")
    assert (created.returncode, created.stdout, created.stderr) == (
        0,
        f"Created branch 'feature' at {tip[:7]}.\n",
        "",
    )
    listing = run_cli(tmp_path, "branch")
    assert (listing.returncode, listing.stdout, listing.stderr) == (
        0,
        "  feature\n* main\n",
        "",
    )

    existing = run_cli(tmp_path, "branch", "feature")
    assert existing.returncode == 1
    assert existing.stdout == ""
    assert existing.stderr == "minigit: branch already exists: feature\n"

    same = run_cli(tmp_path, "checkout", "main")
    assert (same.returncode, same.stdout, same.stderr) == (
        0,
        "Already on branch 'main'.\n",
        "",
    )
    switched = run_cli(tmp_path, "checkout", "feature")
    assert (switched.returncode, switched.stdout, switched.stderr) == (
        0,
        "Switched to branch 'feature'.\n",
        "",
    )

    missing = run_cli(tmp_path, "checkout", "missing")
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert missing.stderr == "minigit: branch not found: missing\n"

    invalid = run_cli(tmp_path, "branch", "../escape")
    assert invalid.returncode == 1
    assert invalid.stdout == ""
    assert "invalid branch name" in invalid.stderr
    assert "Traceback" not in invalid.stderr


def test_merge_subprocess_reports_fast_forward_and_up_to_date(tmp_path):
    init_repo(tmp_path)
    commit_file(tmp_path, "base\n", "base")
    assert run_cli(tmp_path, "branch", "feature").returncode == 0
    assert run_cli(tmp_path, "checkout", "feature").returncode == 0
    (tmp_path / "file.txt").write_text("feature\n")
    assert run_cli(tmp_path, "add", "file.txt").returncode == 0
    assert run_cli(tmp_path, "commit", "-m", "feature").returncode == 0
    assert run_cli(tmp_path, "checkout", "main").returncode == 0

    advanced = run_cli(tmp_path, "merge", "feature")
    assert (advanced.returncode, advanced.stdout, advanced.stderr) == (
        0,
        "Fast-forwarded 'main' to 'feature'.\n",
        "",
    )
    current = run_cli(tmp_path, "merge", "feature")
    assert (current.returncode, current.stdout, current.stderr) == (
        0,
        "Already up to date.\n",
        "",
    )


def test_merge_conflict_subprocess_preserves_paths_and_explains_recovery(tmp_path):
    init_repo(tmp_path)
    commit_file(tmp_path, "base\n", "base")
    assert run_cli(tmp_path, "branch", "feature").returncode == 0
    commit_file(tmp_path, "main\n", "main")
    assert run_cli(tmp_path, "checkout", "feature").returncode == 0
    (tmp_path / "file.txt").write_text("feature\n")
    assert run_cli(tmp_path, "add", "file.txt").returncode == 0
    assert run_cli(tmp_path, "commit", "-m", "feature").returncode == 0
    assert run_cli(tmp_path, "checkout", "main").returncode == 0

    conflict = run_cli(tmp_path, "merge", "feature")

    assert conflict.returncode == 1
    assert conflict.stdout == ""
    assert "minigit: merge conflict in: file.txt" in conflict.stderr
    assert "resolve conflicts, run 'minigit add <path>', then 'minigit commit -m <message>'" in (
        conflict.stderr
    )
    assert "Traceback" not in conflict.stderr
    assert CommitManager(tmp_path).get_merge_status().unresolved_paths == ("file.txt",)

    blocked = run_cli(tmp_path, "commit", "-m", "too soon")
    assert blocked.returncode == 1
    assert blocked.stdout == ""
    assert "unresolved merge paths: file.txt" in blocked.stderr


def test_merge_subprocess_reports_clean_two_parent_merge(tmp_path):
    init_repo(tmp_path)
    commit_file(tmp_path, "base\n", "base")
    assert run_cli(tmp_path, "branch", "feature").returncode == 0
    (tmp_path / "main.txt").write_text("main\n")
    assert run_cli(tmp_path, "add", "main.txt").returncode == 0
    assert run_cli(tmp_path, "commit", "-m", "main").returncode == 0
    main_tip = CommitManager(tmp_path).read_ref("main")
    assert run_cli(tmp_path, "checkout", "feature").returncode == 0
    (tmp_path / "feature.txt").write_text("feature\n")
    assert run_cli(tmp_path, "add", "feature.txt").returncode == 0
    assert run_cli(tmp_path, "commit", "-m", "feature").returncode == 0
    feature_tip = CommitManager(tmp_path).read_ref("feature")
    assert run_cli(tmp_path, "checkout", "main").returncode == 0

    merged = run_cli(tmp_path, "merge", "feature")

    manager = CommitManager(tmp_path)
    merge_tip = manager.read_ref("main")
    assert (merged.returncode, merged.stdout, merged.stderr) == (
        0,
        f"Merged 'feature' into 'main' as {merge_tip[:7]}.\n",
        "",
    )
    assert manager.read_commit(merge_tip).parents == [main_tip, feature_tip]


def test_log_expected_object_and_ref_failures_are_concise(tmp_path):
    init_repo(tmp_path)
    commit_hash = commit_file(tmp_path, "base\n", "base")
    ref = tmp_path / ".minigit" / "refs" / "heads" / "main"

    ref.write_text("f" * 40 + "\n")
    missing = run_cli(tmp_path, "log")
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert missing.stderr == f"minigit: object not found: {'f' * 40}\n"
    assert "Traceback" not in missing.stderr

    ref.write_text("not-a-hash\n")
    invalid = run_cli(tmp_path, "log")
    assert invalid.returncode == 1
    assert invalid.stdout == ""
    assert invalid.stderr == "minigit: invalid ref 'main': not-a-hash\n"
    assert "Traceback" not in invalid.stderr

    ref.write_text(commit_hash + "\n")
    object_path = tmp_path / ".minigit" / "objects" / commit_hash[:2] / commit_hash[2:]
    object_path.write_bytes(b"corrupt")
    corrupt = run_cli(tmp_path, "log")
    assert corrupt.returncode == 1
    assert corrupt.stdout == ""
    assert corrupt.stderr == f"minigit: object corrupt: {commit_hash}\n"
    assert "Traceback" not in corrupt.stderr


def test_argparse_usage_errors_keep_exit_code_two(tmp_path):
    result = run_cli(tmp_path, "log", "--unknown")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage: minigit" in result.stderr
    assert "unrecognized arguments: --unknown" in result.stderr


def test_network_failure_remains_distinct_expected_cli_error(tmp_path):
    init_repo(tmp_path)
    commit_file(tmp_path, "base\n", "base")

    result = run_cli(tmp_path, "push", "127.0.0.1:1", "main", "--token", "test-token")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("minigit: could not connect to 127.0.0.1:1")
    assert "Traceback" not in result.stderr
