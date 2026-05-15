.PHONY: help tree check-structure

help:
	@printf '%s\n' 'OpenLakeForge bootstrap targets:'
	@printf '%s\n' '  make tree             Show the repository structure'
	@printf '%s\n' '  make check-structure  Validate the Iteration 0 repository contract'

tree:
	@find . -path './.git' -prune -o -print | sort

check-structure:
	@bash scripts/check-structure.sh
