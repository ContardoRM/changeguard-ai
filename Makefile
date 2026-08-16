# ChangeGuard AI — judge-facing demo convenience targets.
#
# This Makefile is developer/demo ergonomics ONLY. It adds no new rule
# logic, no new runtime dependency, and no new safety behavior of its
# own — every target below is a thin wrapper around the existing,
# already-reviewed deterministic scripts under scripts/ (run_tf_plan.py,
# inject_demo_candidate.py, cleanup_run_artifacts.py) plus the Python
# stdlib `unittest` runner. It never invokes `terraform apply`,
# `terraform destroy`, or any AWS CLI command, and it never mutates a
# real AWS account (see .kiro/steering/changeguard-principles.md).
#
# `make demo-rel`/`make demo-sec` only prepare terraform/main.tf's
# candidate state; they do NOT run the ChangeGuard Kiro Crew review or
# approve/reject anything automatically — the human-approval gate stays
# genuine and explicit, exactly as designed. See `make help` and
# design.md's "Five-Minute Demo Walkthrough" for the full flow.

.PHONY: setup baseline demo-rel demo-sec reset test test-live help

PYTHON := python3
TERRAFORM_DIR := terraform
ARTIFACTS_DIR := artifacts

help:
	@echo "ChangeGuard AI — judge demo commands"
	@echo ""
	@echo "  make setup       Check required local binaries (terraform, python3,"
	@echo "                   kiro-cli, kirocrew). Installs nothing."
	@echo "  make baseline    Generate artifacts/baseline-plan.json from the safe"
	@echo "                   terraform/main.tf. Never runs terraform apply."
	@echo "  make demo-rel    Edit terraform/main.tf to the REL-001 candidate"
	@echo "                   scenario (desired_count 3 -> 1) and print the next"
	@echo "                   command to run the ChangeGuard review."
	@echo "  make demo-sec    Edit terraform/main.tf to the SEC-001 candidate"
	@echo "                   scenario (TCP/22 10.0.0.0/8 -> 0.0.0.0/0) and print"
	@echo "                   the next command to run the ChangeGuard review."
	@echo "  make reset       Restore terraform/main.tf to the safe baseline and"
	@echo "                   remove only run-generated artifacts (never"
	@echo "                   baseline-plan.json)."
	@echo "  make test        Run the fast deterministic unittest suite (no live"
	@echo "                   kiro-cli/Gateway calls, no Kiro credits spent)."
	@echo "  make test-live   Run the FULL suite, including live kiro-cli agent"
	@echo "                   judgment tests. SLOW and consumes Kiro credits."
	@echo "  make help        Show this message."
	@echo ""
	@echo "5-minute judge demo:"
	@echo "  make baseline"
	@echo "  make demo-rel        # or: make demo-sec"
	@echo "  <run the printed changeguard_launch.py review command>"
	@echo "  <approve or reject in the Kiro Crew Gateway dashboard>"
	@echo "  make reset           # return to the safe baseline for the next run"

setup:
	@echo "Checking required local binaries (nothing will be installed)..."
	@missing=0; \
	for bin in terraform python3 kiro-cli kirocrew; do \
		if command -v "$$bin" >/dev/null 2>&1; then \
			echo "  [ok]      $$bin -> $$(command -v $$bin)"; \
		else \
			echo "  [MISSING] $$bin"; \
			missing=1; \
		fi; \
	done; \
	if [ "$$missing" -eq 1 ]; then \
		echo ""; \
		echo "One or more required binaries are missing. terraform and kiro-cli/"; \
		echo "kirocrew are external tools this repo does not install for you --"; \
		echo "see README.md for where to get them. python3 must already be on PATH."; \
		exit 1; \
	fi; \
	echo ""; \
	echo "All required binaries are present."

baseline:
	@echo "Generating artifacts/baseline-plan.json from the safe $(TERRAFORM_DIR)/main.tf..."
	$(PYTHON) scripts/run_tf_plan.py --terraform-dir $(TERRAFORM_DIR) --output $(ARTIFACTS_DIR)/baseline-plan.json
	@echo "Baseline plan written to $(ARTIFACTS_DIR)/baseline-plan.json."

demo-rel:
	@echo "Preparing the REL-001 demo scenario (desired_count 3 -> 1)..."
	$(PYTHON) scripts/inject_demo_candidate.py --terraform-dir $(TERRAFORM_DIR) --rule-id REL-001
	@echo ""
	@echo "terraform/main.tf now reflects the REL-001 candidate change."
	@echo "Next: run the ChangeGuard review against a running kirocrew gateway,"
	@echo "then approve or reject remediation in the Gateway dashboard yourself --"
	@echo "this target never does that for you:"
	@echo ""
	@echo "  python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage review"

demo-sec:
	@echo "Preparing the SEC-001 demo scenario (TCP/22 10.0.0.0/8 -> 0.0.0.0/0)..."
	$(PYTHON) scripts/inject_demo_candidate.py --terraform-dir $(TERRAFORM_DIR) --rule-id SEC-001
	@echo ""
	@echo "terraform/main.tf now reflects the SEC-001 candidate change."
	@echo "Next: run the ChangeGuard review against a running kirocrew gateway,"
	@echo "then approve or reject remediation in the Gateway dashboard yourself --"
	@echo "this target never does that for you:"
	@echo ""
	@echo "  python3 scripts/changeguard_launch.py --gateway-url http://127.0.0.1:8787 --stage review"

reset:
	@echo "Restoring $(TERRAFORM_DIR)/main.tf to the safe baseline (git checkout)..."
	git checkout -- $(TERRAFORM_DIR)/main.tf
	@echo "Removing run-generated artifacts (never $(ARTIFACTS_DIR)/baseline-plan.json)..."
	$(PYTHON) scripts/cleanup_run_artifacts.py --artifacts-dir $(ARTIFACTS_DIR)
	@echo "Demo state reset. $(TERRAFORM_DIR)/main.tf and $(ARTIFACTS_DIR)/baseline-plan.json are unchanged from the safe baseline."

test:
	@echo "Running the fast deterministic test suite (no live kiro-cli/Gateway calls)..."
	CHANGEGUARD_SKIP_LIVE_TESTS=1 $(PYTHON) -m unittest discover -s tests -v

test-live:
	@echo "WARNING: this runs the FULL suite, including live kiro-cli agent"
	@echo "judgment tests. This is slow (real LLM calls) and consumes Kiro credits."
	$(PYTHON) -m unittest discover -s tests -v
