# Each target delegates to tasks.py, so `python tasks.py <target>` is equivalent where
# make is not installed. `reproduce` covers every core check and regenerates the analysis.
PYTHON ?= python

.PHONY: setup test models replay correspondence lean experiments figures report manifest reproduce demo llm

setup test models replay correspondence lean experiments figures report manifest reproduce demo llm:
	$(PYTHON) tasks.py $@
