# Regenerating lockfiles
#
# CI (lightweight, no torch):
#   python -m pip install pip-tools
#   python -m piptools compile requirements-ci.txt -o requirements-ci.lock.txt --strip-extras
#
# Production image (optional; large / platform-sensitive due to torch):
#   python -m piptools compile requirements.txt -o requirements.lock.txt --strip-extras
#   Docker prefers requirements.lock.txt when present (see Dockerfile).
#
# Commit lockfiles after intentional dependency bumps.
