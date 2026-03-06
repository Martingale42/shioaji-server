IMAGE := shioaji-server
CONTAINER := shioaji

# Paths (relative to this Makefile)
ENV_FILE := $(CURDIR)/.env
CA_FILE := $(CURDIR)/Sinopac.pfx
LOG_FILE := $(CURDIR)/server.log

# Read port from .env, fall back to 8000
PORT := $(or $(shell grep -s '^SHIOAJI_SERVER_PORT=' $(ENV_FILE) | cut -d= -f2 | tr -d '"'"'"),8000)

# --- Build ---

.PHONY: build
build: ## Build Docker image
	docker build -t $(IMAGE) .

# --- Run ---

.PHONY: up
up: _ensure-build _ensure-env _ensure-log ## Start server (simulation, detached)
	docker run -d --name $(CONTAINER) \
		-p $(PORT):8000 \
		-v $(ENV_FILE):/app/.env:ro \
		-v $(CA_FILE):/app/Sinopac.pfx:ro \
		-v $(LOG_FILE):/app/server.log \
		-e CA_PATH=/app/Sinopac.pfx \
		$(IMAGE)
	@echo "Server started at http://localhost:$(PORT)"
	@echo "Logs: tail -f server.log"

.PHONY: up-live
up-live: _ensure-build _ensure-env _ensure-log ## Start server (LIVE, detached)
	docker run -d --name $(CONTAINER) \
		-p $(PORT):8000 \
		-v $(ENV_FILE):/app/.env:ro \
		-v $(CA_FILE):/app/Sinopac.pfx:ro \
		-v $(LOG_FILE):/app/server.log \
		-e CA_PATH=/app/Sinopac.pfx \
		$(IMAGE) --live
	@echo "Server started in LIVE mode at http://localhost:$(PORT)"

.PHONY: down
down: ## Stop and remove container
	docker stop $(CONTAINER) 2>/dev/null || true
	docker rm $(CONTAINER) 2>/dev/null || true

.PHONY: restart
restart: down up ## Restart server

# --- Logs & Status ---

.PHONY: logs
logs: ## Tail container stdout
	docker logs -f $(CONTAINER)

.PHONY: status
status: ## Check server health
	@curl -s http://localhost:$(PORT)/api/health 2>/dev/null || echo "Server not reachable"

# --- Local (no Docker) ---

.PHONY: local
local: ## Run locally without Docker
	uv run shioaji-server

.PHONY: local-live
local-live: ## Run locally in LIVE mode
	uv run shioaji-server --live

# --- Cleanup ---

.PHONY: clean
clean: down ## Remove container and image
	docker rmi $(IMAGE) 2>/dev/null || true

# --- Helpers ---

.PHONY: _ensure-build
_ensure-build:
	@docker image inspect $(IMAGE) >/dev/null 2>&1 || $(MAKE) build

.PHONY: _ensure-env
_ensure-env:
	@test -f $(ENV_FILE) || (echo "Missing .env — copy from .env.example:" && echo "  cp .env.example .env" && exit 1)
	@test -f $(CA_FILE) || (echo "Missing Sinopac.pfx — download from Sinopac API management page" && exit 1)

.PHONY: _ensure-log
_ensure-log:
	@touch $(LOG_FILE)

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
