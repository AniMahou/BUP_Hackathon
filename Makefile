.PHONY: install run test test-live lint docker-build docker-run smoke samples

install:
	python -m venv .venv || true
	.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest -m "not live"

test-live:
	pytest -m live

lint:
	ruff check .

docker-build:
	docker build -t gridwise-llm:local .

docker-run:
	docker run --rm -p 8000:8000 -e GEMINI_API_KEY=$(GEMINI_API_KEY) gridwise-llm:local

smoke:
	bash scripts/smoke_test.sh $(URL)

samples:
	python scripts/run_public_samples.py --base-url $(URL)
