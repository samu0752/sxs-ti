PYTHON ?= python

.PHONY: test lint run
test:
	$(PYTHON) -m coverage run -m unittest discover -s tests -v
	$(PYTHON) -m coverage report

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy --python-version 3.11 app.py sxs_core.py scripts
	$(PYTHON) -m bandit app.py sxs_core.py -r scripts

run:
	$(PYTHON) -m flask --app app run
