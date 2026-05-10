# ai-bot-platform — Sprint 0 dev shortcuts.
# Usage: `make <target>`. All targets assume docker compose is the runtime.

.PHONY: help up down restart shell test migrate makemigrations logs ps clean reset chroma-ping minio-bucket

help:
	@echo "Targets:"
	@echo "  up               Bring up the dev stack (postgres, redis, chromadb, minio, web)"
	@echo "  down             Stop the dev stack (volumes preserved)"
	@echo "  restart          Restart all services"
	@echo "  shell            Django shell inside the web container"
	@echo "  test             Run pytest inside the web container"
	@echo "  migrate          Apply migrations against the postgres container"
	@echo "  makemigrations   Generate new migrations"
	@echo "  logs             Tail logs from all services"
	@echo "  ps               Show service status + healthcheck"
	@echo "  chroma-ping      Verify chromadb HTTP API"
	@echo "  minio-bucket     Create the replay bucket in MinIO"
	@echo "  clean            Stop the stack (preserves volumes)"
	@echo "  reset            Stop the stack AND delete all volumes (destructive)"

up:
	docker compose up -d --build
	docker compose ps

down:
	docker compose down

restart:
	docker compose restart

shell:
	docker compose exec web python manage.py shell

test:
	docker compose exec web pytest

migrate:
	docker compose exec web python manage.py migrate

makemigrations:
	docker compose exec web python manage.py makemigrations

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

chroma-ping:
	curl -fsS http://localhost:8001/api/v2/heartbeat && echo

minio-bucket:
	docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
	docker compose exec minio mc mb --ignore-existing local/ai-bot-replay
	docker compose exec minio mc ls local/

clean: down

reset:
	docker compose down -v
