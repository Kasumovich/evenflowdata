.PHONY: help install run report export all offline test lint docker up down clean sources

help:
	@echo "  make install    Install the package and core dependencies"
	@echo "  make all        Steps A-F, Situation Report, static dashboard"
	@echo "  make offline    Same, with no network (seed + last-good only)"
	@echo "  make sources    Show which sources are live for this environment"
	@echo "  make test       Run the test suite"
	@echo "  make up         docker compose up (dashboard + scheduler)"
	@echo "  make clean      Remove outputs, keep the versioned store"

install:
	pip install -e ".[app,orchestration,dev]"

run:
	enso-tracker run

report:
	enso-tracker report --in-docs

export:
	enso-tracker export

all:
	enso-tracker all --in-docs

offline:
	enso-tracker all --offline --in-docs

sources:
	enso-tracker sources

test:
	pytest -q

lint:
	ruff check src tests scripts

docker:
	docker build -t enso-tracker:latest .

up:
	docker compose up -d --build

down:
	docker compose down

clean:
	rm -rf output/dashboard.html output/dashboard_payload.json
	@echo "Versioned store left intact -- it is the audit history."
