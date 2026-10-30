"""Regression test: cron.sh must never erase upstream rpmbuilder commits.

Until 2026-10-30 cron.sh regenerated ../rpmbuilder files first and pulled
afterwards. The pull aborted on the dirty overlap, the cron committed on a
stale base and force-pushed, wiping rpmbuilder 118b076.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CRON = REPO_ROOT / "cron.sh"

GENERATOR_STUB = """\
#!/bin/sh
echo generated > ../rpmbuilder/distro_versions.json
"""


def git(cwd, *args):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


class CronRpmbuilderSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def make_remote(self, name, branch, files):
        bare = self.tmp / f"{name}.git"
        git(self.tmp, "init", "--quiet", "--bare", f"--initial-branch={branch}", str(bare))
        seed = self.tmp / f"{name}-seed"
        git(self.tmp, "clone", "--quiet", str(bare), str(seed))
        git(seed, "checkout", "--quiet", "-b", branch)
        for rel, body in files.items():
            (seed / rel).write_text(body)
        git(seed, "add", "--all")
        git(seed, "commit", "--quiet", "-m", "seed")
        git(seed, "push", "--quiet", "origin", branch)
        return bare, seed

    def test_upstream_commit_touching_generated_file_survives(self):
        bs_bare, bs_seed = self.make_remote("buildstrap", "main", {
            "cron.sh": CRON.read_text(),
            "generate_config.py": GENERATOR_STUB,
        })
        os.chmod(bs_seed / "generate_config.py", 0o755)
        git(bs_seed, "add", "--all")
        git(bs_seed, "commit", "--quiet", "-m", "exec bit")
        git(bs_seed, "push", "--quiet", "origin", "main")
        rb_bare, rb_seed = self.make_remote("rpmbuilder", "master", {
            "distro_versions.json": "old\n",
        })

        work = self.tmp / "work"
        work.mkdir()
        git(work, "clone", "--quiet", str(bs_bare), "buildstrap")
        git(work, "clone", "--quiet", str(rb_bare), "rpmbuilder")

        # Upstream moves after the server checkout was cloned, touching the
        # same file the generator writes.
        (rb_seed / "distro_versions.json").write_text("upstream\n")
        (rb_seed / "keep.txt").write_text("must survive\n")
        git(rb_seed, "add", "--all")
        git(rb_seed, "commit", "--quiet", "-m", "upstream fix")
        git(rb_seed, "push", "--quiet", "origin", "master")
        upstream = git(rb_seed, "rev-parse", "HEAD")

        subprocess.run(
            ["bash", str(work / "buildstrap" / "cron.sh")],
            env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                     GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                     GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1"),
            capture_output=True, text=True,
        )

        head = git(rb_bare, "rev-parse", "master")
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", upstream, head], cwd=rb_bare,
        )
        self.assertEqual(ancestry.returncode, 0, "cron erased the upstream commit")
        self.assertEqual(git(rb_bare, "show", "master:distro_versions.json"), "generated")
        self.assertEqual(git(rb_bare, "show", "master:keep.txt"), "must survive")


if __name__ == "__main__":
    unittest.main()
