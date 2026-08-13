#!/usr/bin/env python3
"""Terraform Plan Tool.

Deterministic local CLI tool that generates real Terraform plan JSON
evidence for the ChangeGuard workflow. It contains no risk-detection or
rule-evaluation logic (Requirement 2.3) and cannot invoke `terraform apply`
or `terraform destroy` under any argument combination (Requirement 2.4),
because those subcommands are simply absent from its fixed allow-list.

CLI contract (design.md "Terraform Plan Tool"):

    python3 scripts/run_tf_plan.py --terraform-dir <path> --output <path>
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# Fixed allow-list of Terraform subcommands this tool is permitted to run.
# Every subprocess invocation is checked against this set before it is
# executed. `apply`, `destroy`, and any AWS CLI invocation are not members
# of this set, so no code path in this script can reach them. This is the
# Requirement 11.6 secondary (code-level) enforcement layer, independent of
# the Kiro safety hook.
ALLOWED_SUBCOMMANDS = frozenset({"init", "fmt", "validate", "plan", "show"})


class DisallowedSubcommandError(Exception):
    """Raised when a Terraform subcommand outside the allow-list is requested."""


def run_terraform_command(argv, cwd=None):
    """Run a Terraform CLI command, enforcing the subcommand allow-list.

    Args:
        argv: The full argv list for the subprocess call, e.g.
            ["terraform", "init", "-input=false"]. Must be a list, never a
            shell string.
        cwd: Optional working directory to run the command in (e.g. the
            `--terraform-dir` value). Passed straight through to
            subprocess.run's `cwd` parameter rather than embedded in argv,
            so relative paths (like a plan output file) keep whatever
            meaning the caller intends.

    Returns:
        The completed subprocess.CompletedProcess.

    Raises:
        DisallowedSubcommandError: if argv[1] (the Terraform subcommand) is
            not in ALLOWED_SUBCOMMANDS. Raised before subprocess.run is
            ever called, so the disallowed command is never executed.
    """
    if len(argv) < 2:
        raise DisallowedSubcommandError(
            f"Malformed Terraform command, no subcommand present: {argv!r}"
        )

    subcommand = argv[1]
    if subcommand not in ALLOWED_SUBCOMMANDS:
        raise DisallowedSubcommandError(
            f"Refusing to run disallowed Terraform subcommand '{subcommand}'. "
            f"Allowed subcommands: {sorted(ALLOWED_SUBCOMMANDS)}"
        )

    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd)


def parse_args(argv=None):
    """Parse CLI arguments for the Terraform Plan Tool.

    Accepts exactly `--terraform-dir` and `--output`, both required.
    """
    parser = argparse.ArgumentParser(
        prog="run_tf_plan.py",
        description="Generate Terraform plan JSON evidence (no risk logic).",
    )
    parser.add_argument(
        "--terraform-dir",
        required=True,
        help="Path to the Terraform configuration directory to plan against.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the resulting plan JSON to.",
    )
    return parser.parse_args(argv)


def _fail(step_name, result):
    """Print a fail-fast error for a failed subcommand and return 1.

    Surfaces the subcommand's captured stderr (Terraform diagnostic output)
    to the caller's stderr, per the fail-fast contract: abort immediately,
    write nothing to --output, exit non-zero.
    """
    print(
        f"run_tf_plan.py: '{step_name}' failed with exit code {result.returncode}",
        file=sys.stderr,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return 1


def main(argv=None):
    args = parse_args(argv)
    terraform_dir = args.terraform_dir
    output_path = args.output

    # Binary plan file lives outside terraform_dir (system tempdir) and is
    # referenced by absolute path so its meaning doesn't depend on cwd.
    plan_fd, plan_path = tempfile.mkstemp(prefix="run_tf_plan_", suffix=".tfplan")
    os.close(plan_fd)

    try:
        steps = [
            ("terraform init", ["terraform", "init", "-input=false"]),
            ("terraform fmt -check", ["terraform", "fmt", "-check"]),
            ("terraform validate", ["terraform", "validate"]),
            (
                "terraform plan",
                [
                    "terraform",
                    "plan",
                    "-refresh=false",
                    "-input=false",
                    "-lock=false",
                    f"-out={plan_path}",
                ],
            ),
        ]

        for step_name, step_argv in steps:
            result = run_terraform_command(step_argv, cwd=terraform_dir)
            if result.returncode != 0:
                return _fail(step_name, result)

        show_result = run_terraform_command(
            ["terraform", "show", "-json", plan_path], cwd=terraform_dir
        )
        if show_result.returncode != 0:
            return _fail("terraform show -json", show_result)

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "w") as output_file:
            output_file.write(show_result.stdout)
    finally:
        try:
            os.remove(plan_path)
        except OSError:
            pass

    print(json.dumps({"status": "success", "plan": output_path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
