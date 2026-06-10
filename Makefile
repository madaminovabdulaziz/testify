.PHONY: help dev test lint typecheck migrate migrate-up migrate-down \
        build deploy load-test seed-admin backup restore smoke

PROD_COMPOSE := docker-compose.prod.yml
# macOS ships ``python3`` only; Linux + Docker images expose both.
# Prefer ``python3`` so the dev workflow works on a vanilla Mac without
# a venv shim. Override with ``make PYTHON=python dev`` if needed.
PYTHON ?= python3

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev:  ## Start MySQL + Redis, run migrations, seed fixtures, run bot in polling mode
	docker compose up -d mysql redis
	@echo "▶ Waiting for MySQL to accept a real bot-user connection…"
	@set -a; . ./.env; set +a; $(PYTHON) -m scripts.wait_for_mysql
	@set -a; . ./.env; set +a; alembic upgrade head
	@set -a; . ./.env; set +a; $(PYTHON) -m scripts.seed_dev
	@set -a; . ./.env; set +a; $(PYTHON) -m app.main

test:  ## Full pytest suite (unit + integration)
	pytest

load-test:  ## 50 concurrent users smoke test (needs DB_* env vars to reach a real MySQL)
	$(PYTHON) scripts/load_test.py

lint:  ## ruff check + ruff format --check
	ruff check .
	ruff format --check .

typecheck:  ## mypy on services + repositories + web panel
	mypy app/services app/repositories app/web

migrate:  ## Create a new Alembic revision (usage: make migrate name="add_xyz")
	@if [ -z "$(name)" ]; then \
		echo 'Usage: make migrate name="short description"'; \
		exit 1; \
	fi
	alembic revision --autogenerate -m "$(name)"

migrate-up:  ## alembic upgrade head
	alembic upgrade head

migrate-down:  ## alembic downgrade -1
	alembic downgrade -1

build:  ## Build Docker image
	docker build -f docker/Dockerfile -t attestation-bot:latest .

deploy:  ## Production deploy runbook (see ARCHITECTURE_SPEC §14.4)
	docker compose -f $(PROD_COMPOSE) pull
	docker compose -f $(PROD_COMPOSE) build bot
	docker compose -f $(PROD_COMPOSE) up -d mysql redis
	docker compose -f $(PROD_COMPOSE) run --rm bot alembic upgrade head
	docker compose -f $(PROD_COMPOSE) up -d bot nginx
	@echo
	@echo "▶ Smoke test (from host):  make smoke"
	@echo "▶ Telegram webhook URL:    $${WEBHOOK_URL:-<set WEBHOOK_URL>}"

smoke:  ## Curl /healthz against the local nginx (uses NGINX_SERVER_NAME)
	@if [ -z "$$NGINX_SERVER_NAME" ]; then \
		echo 'Set NGINX_SERVER_NAME (or source .env) and retry.'; \
		exit 1; \
	fi
	curl -sf "https://$$NGINX_SERVER_NAME/healthz" \
		&& echo "OK" \
		|| (echo "/healthz failed" && exit 1)

seed-admin:  ## Grant owner-admin to a Telegram ID (usage: make seed-admin TELEGRAM_ID=12345)
	@if [ -z "$(TELEGRAM_ID)" ]; then \
		echo 'Usage: make seed-admin TELEGRAM_ID=<integer> [ROLE=owner|moderator]'; \
		exit 1; \
	fi
	@# In dev mode the bot runs on the host, so seed against the host's
	@# Python (which reads .env). In prod/staging the bot is in a
	@# container — run there so the DB_HOST=mysql alias resolves.
	@if [ "$$ENV" = "prod" ] || [ "$$ENV" = "staging" ]; then \
		docker compose -f $(PROD_COMPOSE) run --rm bot \
			python -m scripts.seed_admin $(TELEGRAM_ID) --role=$${ROLE:-owner}; \
	else \
		set -a; . ./.env; set +a; \
		$(PYTHON) -m scripts.seed_admin $(TELEGRAM_ID) --role=$${ROLE:-owner}; \
	fi

backup:  ## Run scripts/backup.sh against the prod MySQL container
	./scripts/backup.sh

restore:  ## Restore the prod DB from a .sql.gz file (usage: make restore BACKUP=./backups/attestation-XXX.sql.gz)
	@if [ -z "$(BACKUP)" ]; then \
		echo 'Usage: make restore BACKUP=./backups/attestation-<timestamp>.sql.gz'; \
		exit 1; \
	fi
	@if [ ! -f "$(BACKUP)" ]; then \
		echo "Backup file not found: $(BACKUP)"; \
		exit 1; \
	fi
	@echo "⚠ This will overwrite the current $(DB_NAME) database. Ctrl-C in 5s to abort…"
	@sleep 5
	gunzip -c $(BACKUP) | docker compose -f $(PROD_COMPOSE) exec -T mysql \
		mysql -uroot -p$(DB_ROOT_PASSWORD) $(DB_NAME)
	@echo "✅ Restore complete."
